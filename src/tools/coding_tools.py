"""
Coding 工具直接实现 — 10 个核心编码工具在主进程中直接执行

从 coding_server.py 移植而来，不再通过 MCP 子进程调用。
execute_command 工具集成了 ApprovalManager 审批流程。
"""
from __future__ import annotations

import asyncio
import os
import re
import sys
import json
import logging
import subprocess
from pathlib import Path
from typing import Optional, Any

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

import src.config as config

logger = logging.getLogger(__name__)


async def _run_io(func, *args, **kwargs):
    """在 I/O 线程池中执行同步函数，防止阻塞事件循环。

    coding 工具函数声明为 async 但内部做 sync I/O（open/os.walk/subprocess.run），
    必须包装到线程池执行，否则多个 Agent 并行调用时会阻塞事件循环导致死锁。
    """
    try:
        from src.agent.session import _io_executor
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(_io_executor, func, *args, **kwargs)
    except (ImportError, RuntimeError):
        return await asyncio.to_thread(func, *args, **kwargs)

# coding 工具名称集合 —— 本模块是唯一 source of truth。
# registry.py 从此处导入，用于排除 MCP 同名工具。
CODING_TOOL_NAMES: set[str] = {
    "read_file", "write_to_file", "replace_in_file",
    "search_files", "search_file", "list_files", "list_code_definitions",
    "apply_diff", "execute_command", "ask_user",
}

# ═══════════════════════════════════════════════════════════════
# 辅助函数（从 coding_server.py 移植）
# ═══════════════════════════════════════════════════════════════


def _validate_and_resolve_path(
    workspace_path: str, relative_path: str
) -> tuple[bool, str, Path | None]:
    """验证路径并解析为安全绝对路径。

    Returns:
        (success, error_message, resolved_path)
    """
    if not workspace_path:
        return False, "未配置 workspace 路径", None

    workspace = Path(workspace_path).resolve()
    if not workspace.exists():
        return False, f"Workspace 路径不存在: {workspace}", None

    # 路径沙箱关闭：允许绝对路径，但仍拒绝敏感系统路径
    if not config.CODING_PATH_SANDBOX_ENABLED:
        if os.path.isabs(relative_path):
            resolved = Path(relative_path).resolve()
        else:
            resolved = (workspace / relative_path).resolve()
        # 基本安全检查：拒绝访问敏感系统目录（使用 Path.is_relative_to 做目录边界检查）
        _SENSITIVE_DIRS = (
            Path("/etc"), Path("/proc"), Path("/sys"),
            Path("/dev"), Path("/boot"), Path("/root"),
        )
        for sensitive_dir in _SENSITIVE_DIRS:
            if resolved == sensitive_dir or resolved.is_relative_to(sensitive_dir):
                return False, f"沙箱关闭时仍禁止访问系统敏感路径: {resolved}", None
        logger.debug(f"沙箱关闭，允许路径访问: {resolved}")
        return True, "", resolved

    # 拒绝绝对路径
    if os.path.isabs(relative_path):
        return False, f"不允许使用绝对路径: {relative_path}", None

    # 路径穿越检测
    resolved = (workspace / relative_path).resolve()
    try:
        resolved.relative_to(workspace)
    except ValueError:
        return False, f"路径穿越检测失败: {relative_path}", None

    # 符号链接逃逸检测
    real_path = Path(os.path.realpath(str(resolved)))
    try:
        real_path.relative_to(workspace)
    except ValueError:
        return False, "符号链接逃逸: 真实路径不在 workspace 内", None

    return True, "", resolved


def _check_file_size(path: Path) -> tuple[bool, str]:
    """检查文件大小"""
    max_size = config.CODING_MAX_FILE_SIZE
    if path.exists() and path.is_file():
        size = path.stat().st_size
        if size > max_size:
            return False, f"文件大小 {size} bytes 超过限制 {max_size} bytes"
    return True, ""


_MAX_RIPGREP_RESULTS = 100


def _search_with_ripgrep(
    search_dir: Path, regex: str, file_pattern: str, workspace_path: str
) -> str | None:
    """使用 ripgrep 搜索，不可用时返回 None"""
    try:
        cmd = ["rg", "--json", "-e", regex]
        if file_pattern:
            cmd.extend(["-g", file_pattern])
        cmd.append(str(search_dir))

        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=30, encoding="utf-8"
        )

        if result.returncode not in (0, 1):  # 1 = no matches
            return None

        matches: list[dict] = []
        total = 0
        workspace = Path(workspace_path).resolve()
        for line in result.stdout.strip().split("\n"):
            if not line:
                continue
            try:
                data = json.loads(line)
                if data.get("type") == "match":
                    total += 1
                    if len(matches) < _MAX_RIPGREP_RESULTS:
                        match_data = data["data"]
                        file_path = Path(match_data["path"]["text"])
                        try:
                            rel_path = str(file_path.relative_to(workspace))
                        except ValueError:
                            rel_path = str(file_path)
                        line_number = match_data["line_number"]
                        line_text = match_data["lines"]["text"].rstrip()
                        if len(line_text) > 500:
                            line_text = line_text[:500] + "..."
                        matches.append(
                            {
                                "file": rel_path,
                                "line": line_number,
                                "content": line_text,
                            }
                        )
            except (json.JSONDecodeError, KeyError):
                continue

        return json.dumps(
            {
                "matches": matches,
                "total": total,
                "engine": "ripgrep",
            },
            ensure_ascii=False,
        )

    except FileNotFoundError:
        return None
    except subprocess.TimeoutExpired:
        return json.dumps({"error": "搜索超时"}, ensure_ascii=False)


def _search_with_python(
    search_dir: Path, regex: str, file_pattern: str, workspace_path: str
) -> str:
    """Python 正则搜索降级实现"""
    import fnmatch

    skip_dirs = _SKIP_DIRS
    pattern = re.compile(regex)
    matches: list[dict] = []
    workspace = Path(workspace_path).resolve()
    max_results = 100

    for root, dirs, files in os.walk(search_dir):
        dirs[:] = [d for d in sorted(dirs) if d not in skip_dirs]
        for fname in files:
            if file_pattern and not fnmatch.fnmatch(fname, file_pattern):
                continue
            fpath = Path(root) / fname
            try:
                with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                    for i, line in enumerate(f, 1):
                        if pattern.search(line):
                            try:
                                rel = str(fpath.relative_to(workspace))
                            except ValueError:
                                rel = str(fpath)
                            text = line.rstrip()
                            if len(text) > 500:
                                text = text[:500] + "..."
                            matches.append(
                                {"file": rel, "line": i, "content": text}
                            )
                            if len(matches) >= max_results:
                                break
            except (OSError, UnicodeDecodeError):
                continue
            if len(matches) >= max_results:
                break
        if len(matches) >= max_results:
            break

    return json.dumps(
        {"matches": matches, "total": len(matches), "engine": "python"},
        ensure_ascii=False,
    )


def _extract_definitions(content: str, suffix: str) -> list[dict]:
    """通过正则提取代码定义"""
    definitions: list[dict] = []
    lines = content.split("\n")
    patterns = _get_patterns_for_suffix(suffix)

    for i, line in enumerate(lines, 1):
        for compiled_pattern, kind in patterns:
            match = compiled_pattern.match(line)
            if match:
                name = match.group(1) if match.groups() else ""
                definitions.append(
                    {
                        "line": i,
                        "kind": kind,
                        "name": name,
                        "text": line.rstrip()[:200],
                    }
                )
                break

    return definitions


def _get_patterns_for_suffix(suffix: str) -> list[tuple[re.Pattern, str]]:
    """根据文件后缀返回匹配模式"""
    if suffix in (".py",):
        return [
            (re.compile(r"^(?:async\s+)?def\s+(\w+)"), "function"),
            (re.compile(r"^class\s+(\w+)"), "class"),
        ]
    elif suffix in (".js", ".jsx", ".ts", ".tsx", ".mjs"):
        return [
            (
                re.compile(
                    r"^\s*(?:export\s+)?(?:async\s+)?function\s+(\w+)"
                ),
                "function",
            ),
            (re.compile(r"^\s*(?:export\s+)?class\s+(\w+)"), "class"),
            (
                re.compile(
                    r"^\s*(?:export\s+)?(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s+)?\("
                ),
                "function",
            ),
            (
                re.compile(
                    r"^\s*(?:export\s+)?(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s+)?(?:function|\()"
                ),
                "function",
            ),
            (re.compile(r"^\s*(?:export\s+)?interface\s+(\w+)"), "interface"),
            (re.compile(r"^\s*(?:export\s+)?type\s+(\w+)"), "type"),
            (re.compile(r"^\s*(?:export\s+)?enum\s+(\w+)"), "enum"),
        ]
    elif suffix in (".java", ".kt"):
        return [
            (
                re.compile(
                    r"^\s*(?:public|private|protected)?\s*(?:static\s+)?(?:abstract\s+)?class\s+(\w+)"
                ),
                "class",
            ),
            (
                re.compile(
                    r"^\s*(?:public|private|protected)?\s*(?:static\s+)?interface\s+(\w+)"
                ),
                "interface",
            ),
            (
                re.compile(
                    r"^\s*(?:public|private|protected)?\s*(?:static\s+)?(?:abstract\s+)?(?:\w+\s+)+(\w+)\s*\("
                ),
                "method",
            ),
        ]
    elif suffix in (".go",):
        return [
            (
                re.compile(r"^func\s+(?:\(\w+\s+\*?\w+\)\s+)?(\w+)"),
                "function",
            ),
            (re.compile(r"^type\s+(\w+)\s+struct"), "struct"),
            (re.compile(r"^type\s+(\w+)\s+interface"), "interface"),
        ]
    elif suffix in (".rs",):
        return [
            (re.compile(r"^\s*(?:pub\s+)?fn\s+(\w+)"), "function"),
            (re.compile(r"^\s*(?:pub\s+)?struct\s+(\w+)"), "struct"),
            (re.compile(r"^\s*(?:pub\s+)?enum\s+(\w+)"), "enum"),
            (re.compile(r"^\s*(?:pub\s+)?trait\s+(\w+)"), "trait"),
            (re.compile(r"^\s*impl\s+(\w+)"), "impl"),
        ]
    elif suffix in (".c", ".h", ".cpp", ".hpp", ".cc"):
        return [
            (
                re.compile(
                    r"^\s*(?:static\s+)?(?:inline\s+)?(?:\w+\s+)+(\w+)\s*\("
                ),
                "function",
            ),
            (re.compile(r"^\s*(?:typedef\s+)?struct\s+(\w+)"), "struct"),
            (re.compile(r"^\s*class\s+(\w+)"), "class"),
            (re.compile(r"^\s*namespace\s+(\w+)"), "namespace"),
        ]
    else:
        return []


# ═══════════════════════════════════════════════════════════════
# 工具参数模型
# ═══════════════════════════════════════════════════════════════


class _ReadFileArgs(BaseModel):
    path: str = Field(description="文件路径（相对于 workspace）")
    offset: int = Field(default=0, description="起始行号（从 0 开始，0 表示文件开头）")
    limit: int = Field(default=0, description="读取行数（0 表示读取全部）")


class _WriteToFileArgs(BaseModel):
    path: str = Field(description="文件路径（相对于 workspace）")
    content: str = Field(description="文件内容")


class _ReplaceInFileArgs(BaseModel):
    path: str = Field(description="文件路径（相对于 workspace）")
    old_str: str = Field(description="要替换的旧文本（必须精确且唯一匹配）")
    new_str: str = Field(description="替换后的新文本")


class _SearchFilesArgs(BaseModel):
    path: str = Field(description="搜索根目录（相对于 workspace）")
    regex: str = Field(description="正则表达式模式")
    file_pattern: str = Field(default="", description="可选的文件名 glob 过滤（如 '*.py'）")


class _ListFilesArgs(BaseModel):
    path: str = Field(description="目录路径（相对于 workspace）")
    recursive: bool = Field(default=True, description="是否递归列出子目录，默认 True")


class _ListCodeDefinitionsArgs(BaseModel):
    path: str = Field(description="文件路径（相对于 workspace）")


class _ExecuteCommandArgs(BaseModel):
    command: str = Field(description="要执行的终端命令")
    cwd: str = Field(default="", description="工作目录（相对于 workspace，默认为 workspace 根目录）")


class _AskUserArgs(BaseModel):
    question: str = Field(description="要向用户提出的问题")


class _ApplyDiffArgs(BaseModel):
    path: str = Field(description="文件路径（相对于 workspace）")
    diff: str = Field(description="SEARCH/REPLACE 块格式的 diff 内容。格式：<<<<<<< SEARCH\n原始代码\n=======\n新代码\n>>>>>>> REPLACE。支持多个块批量处理。")


class _SearchFileArgs(BaseModel):
    path: str = Field(description="搜索根目录（相对于 workspace）")
    pattern: str = Field(description="文件名 glob 模式（如 '*.py', '*Drawer*.tsx'）")
    recursive: bool = Field(default=True, description="是否递归搜索子目录，默认 True")
    caseSensitive: bool = Field(default=False, description="是否区分大小写，默认 False")


# ═══════════════════════════════════════════════════════════════
# 工具实现函数（模块级，无闭包依赖）
# ═══════════════════════════════════════════════════════════════

# SEARCH/REPLACE 块正则：匹配 <<<<<<< SEARCH ... ======= ... >>>>>>> REPLACE
_SEARCH_REPLACE_PATTERN = re.compile(
    r"<<<<<<< SEARCH\s*\n(.*?)\n?=======\s*\n(.*?)\n?>>>>>>> REPLACE",
    re.DOTALL,
)


async def _read_file(path: str, offset: int = 0, limit: int = 0) -> str:
    """读取文件内容，支持行范围读取。"""
    from src.session.context import get_session_context
    ctx = get_session_context()
    workspace_path = ctx.get("workspace_path", "")
    ok, err, abs_path = _validate_and_resolve_path(workspace_path, path)
    if not ok:
        return json.dumps({"error": err}, ensure_ascii=False)
    if not abs_path.exists():
        return json.dumps({"error": f"文件不存在: {path}"}, ensure_ascii=False)
    if not abs_path.is_file():
        return json.dumps({"error": f"不是文件: {path}"}, ensure_ascii=False)
    ok, err = _check_file_size(abs_path)
    if not ok:
        return json.dumps({"error": err}, ensure_ascii=False)

    def _do_read():
        with open(abs_path, "rb") as f:
            chunk = f.read(8192)
        is_binary = b"\x00" in chunk
        if is_binary:
            return (True, json.dumps(
                {"error": f"二进制文件不支持读取: {path}"}, ensure_ascii=False
            ))
        with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        return (False, lines)

    try:
        is_binary, result_or_lines = await _run_io(_do_read)
        if is_binary:
            return result_or_lines
        lines = result_or_lines
        total_lines = len(lines)

        if offset > 0 or limit > 0:
            start = max(0, offset)
            end = start + limit if limit > 0 else total_lines
            selected = lines[start:end]
            numbered = [f"{i:6d}:{line.rstrip()}" for i, line in enumerate(selected, start=start + 1)]
            content = "\n".join(numbered)
            return json.dumps(
                {"content": content, "total_lines": total_lines,
                 "showing": f"lines {start + 1}-{min(end, total_lines)}"},
                ensure_ascii=False,
            )
        else:
            numbered = [f"{i:6d}:{line.rstrip()}" for i, line in enumerate(lines, start=1)]
            content = "\n".join(numbered)
            return json.dumps(
                {"content": content, "total_lines": total_lines},
                ensure_ascii=False,
            )
    except Exception as e:
        return json.dumps({"error": f"读取文件失败: {str(e)}"}, ensure_ascii=False)


async def _write_to_file(path: str, content: str) -> str:
    """创建或覆盖整个文件。"""
    from src.session.context import get_session_context
    ctx = get_session_context()
    workspace_path = ctx.get("workspace_path", "")
    ok, err, abs_path = _validate_and_resolve_path(workspace_path, path)
    if not ok:
        return json.dumps({"error": err}, ensure_ascii=False)
    def _do_write():
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        with open(abs_path, "w", encoding="utf-8", newline="") as f:
            f.write(content)
        return len(content.encode("utf-8"))

    try:
        bytes_written = await _run_io(_do_write)
        return json.dumps(
            {"message": f"文件已写入: {path}", "bytes_written": bytes_written},
            ensure_ascii=False,
        )
    except Exception as e:
        return json.dumps({"error": f"写入文件失败: {str(e)}"}, ensure_ascii=False)


async def _replace_in_file(path: str, old_str: str, new_str: str) -> str:
    """在文件中进行搜索替换编辑（精确字符串匹配）。"""
    from src.session.context import get_session_context
    ctx = get_session_context()
    workspace_path = ctx.get("workspace_path", "")
    ok, err, abs_path = _validate_and_resolve_path(workspace_path, path)
    if not ok:
        return json.dumps({"error": err}, ensure_ascii=False)
    if not abs_path.exists():
        return json.dumps({"error": f"文件不存在: {path}"}, ensure_ascii=False)
    ok, err = _check_file_size(abs_path)
    if not ok:
        return json.dumps({"error": err}, ensure_ascii=False)

    def _do_replace():
        with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
            return f.read()

    try:
        original_content = await _run_io(_do_replace)
    except Exception as e:
        return json.dumps({"error": f"替换失败: {str(e)}"}, ensure_ascii=False)

    try:
        if old_str not in original_content:
            normalized_content = original_content.replace("\r\n", "\n")
            normalized_old = old_str.replace("\r\n", "\n")
            if normalized_old not in normalized_content:
                return json.dumps(
                    {"error": "未找到要替换的文本"}, ensure_ascii=False
                )
            # 归一化路径也需检查唯一性，防止 CRLF 差异绕过唯一性校验
            norm_count = normalized_content.count(normalized_old)
            if norm_count > 1:
                return json.dumps(
                    {
                        "error": f"找到 {norm_count} 处匹配（归一化后），old_str 不唯一，请提供更多上下文使其唯一"
                    },
                    ensure_ascii=False,
                )
            new_content = normalized_content.replace(
                normalized_old, new_str.replace("\r\n", "\n"), 1
            )
        else:
            count = original_content.count(old_str)
            if count > 1:
                return json.dumps(
                    {"error": f"找到 {count} 处匹配，old_str 不唯一，请提供更多上下文使其唯一"},
                    ensure_ascii=False,
                )
            new_content = original_content.replace(old_str, new_str, 1)

        def _do_write():
            with open(abs_path, "w", encoding="utf-8", newline="") as f:
                f.write(new_content)
        await _run_io(_do_write)
        return json.dumps(
            {"message": f"文件已更新: {path}", "replacements": 1},
            ensure_ascii=False,
        )
    except Exception as e:
        return json.dumps({"error": f"替换失败: {str(e)}"}, ensure_ascii=False)


async def _search_files(
    path: str, regex: str, file_pattern: str = ""
) -> str:
    """在文件内容中进行正则搜索（自动尝试 ripgrep，不可用则降级为 Python）。"""
    from src.session.context import get_session_context
    ctx = get_session_context()
    workspace_path = ctx.get("workspace_path", "")
    ok, err, abs_path = _validate_and_resolve_path(workspace_path, path)
    if not ok:
        return json.dumps({"error": err}, ensure_ascii=False)
    if not abs_path.exists():
        return json.dumps({"error": f"路径不存在: {path}"}, ensure_ascii=False)

    def _do_search():
        rg_results = _search_with_ripgrep(
            abs_path, regex, file_pattern, workspace_path
        )
        if rg_results is not None:
            return rg_results
        return _search_with_python(
            abs_path, regex, file_pattern, workspace_path
        )

    try:
        return await _run_io(_do_search)
    except Exception as e:
        return json.dumps({"error": f"搜索失败: {str(e)}"}, ensure_ascii=False)


async def _list_files(path: str, recursive: bool = True) -> str:
    """列出目录结构。"""
    from src.session.context import get_session_context
    ctx = get_session_context()
    workspace_path = ctx.get("workspace_path", "")
    ok, err, abs_path = _validate_and_resolve_path(workspace_path, path)
    if not ok:
        return json.dumps({"error": err}, ensure_ascii=False)
    if not abs_path.exists():
        return json.dumps({"error": f"路径不存在: {path}"}, ensure_ascii=False)
    if not abs_path.is_dir():
        return json.dumps({"error": f"不是目录: {path}"}, ensure_ascii=False)

    workspace = Path(workspace_path).resolve()

    def _do_list():
        return _walk_directory(
            abs_path, workspace, recursive=recursive, include_dirs=True,
        )

    try:
        entries = await _run_io(_do_list)
        return json.dumps(
            {"entries": entries, "total": len(entries),
             "truncated": len(entries) >= _MAX_DIR_ENTRIES},
            ensure_ascii=False,
        )
    except Exception as e:
        return json.dumps({"error": f"列出目录失败: {str(e)}"}, ensure_ascii=False)


async def _list_code_definitions(path: str) -> str:
    """列出代码文件中的定义（函数、类、方法等）。"""
    from src.session.context import get_session_context
    ctx = get_session_context()
    workspace_path = ctx.get("workspace_path", "")
    ok, err, abs_path = _validate_and_resolve_path(workspace_path, path)
    if not ok:
        return json.dumps({"error": err}, ensure_ascii=False)
    if not abs_path.exists():
        return json.dumps({"error": f"文件不存在: {path}"}, ensure_ascii=False)
    ok, err = _check_file_size(abs_path)
    if not ok:
        return json.dumps({"error": err}, ensure_ascii=False)

    def _do_parse():
        with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
        suffix = abs_path.suffix.lower()
        return _extract_definitions(content, suffix)

    try:
        definitions = await _run_io(_do_parse)
        return json.dumps(
            {"file": path, "definitions": definitions, "total": len(definitions)},
            ensure_ascii=False,
        )
    except Exception as e:
        return json.dumps(
            {"error": f"解析代码定义失败: {str(e)}"}, ensure_ascii=False
        )


async def _execute_command(
    command: str, cwd: str = "", approval_manager: Any = None
) -> str:
    """执行终端命令（含审批流）。"""
    # ══════════════════════════════════════════════════════
    # 命令鉴权（通过 WorkspaceGuard 调用主进程的审批管理器）
    # ══════════════════════════════════════════════════════
    from src.session.context import get_session_context
    from src.core.workspace_guard import WorkspaceGuard

    ctx = get_session_context()
    workspace_path = ctx.get("workspace_path", "")
    session_id = ctx.get("session_id", "")

    if not workspace_path:
        return json.dumps({"error": "未配置 workspace 路径"}, ensure_ascii=False)

    workspace = Path(workspace_path).resolve()

    # 确定工作目录
    if cwd:
        ok, err, work_dir = _validate_and_resolve_path(workspace_path, cwd)
        if not ok:
            return json.dumps({"error": err}, ensure_ascii=False)
    else:
        work_dir = workspace

    # WorkspaceGuard 命令鉴权
    guard = WorkspaceGuard()
    guard_result = guard.validate_command(workspace_path, command)

    # 需要审批 → 走 ApprovalManager 流程
    if not guard_result.allowed and guard_result.needs_approval:
        if approval_manager is None:
            return json.dumps(
                {
                    "error": (
                        f"命令需要审批但审批管理器不可用: "
                        f"{guard_result.reason}"
                    )
                },
                ensure_ascii=False,
            )

        # 创建审批请求 → EventBus 推送 → 前端展示审批卡片
        req = approval_manager.request_approval(
            command=command,
            session_id=session_id,
            tool_name="execute_command",
            workspace=str(work_dir),
        )

        # 异步等待审批结果
        result = await approval_manager.wait_for_approval(req.request_id)

        if result == "rejected":
            return json.dumps(
                {
                    "error": f"命令审批被拒绝: {command}",
                    "exit_code": -1,
                },
                ensure_ascii=False,
            )
        elif result == "timeout":
            return json.dumps(
                {
                    "error": f"命令审批超时: {command}",
                    "exit_code": -1,
                },
                ensure_ascii=False,
            )
        # result == "approved" → 标记为已授权，跳过后续拒绝检查
        guard_result.allowed = True

    # 直接拒绝（非审批场景，审批已通过的不会走到这里）
    if not guard_result.allowed:
        return json.dumps(
            {"error": f"命令被拒绝: {guard_result.reason}"}, ensure_ascii=False
        )

    # ══════════════════════════════════════════════════════
    # 执行命令（线程池，防止阻塞事件循环）
    # ══════════════════════════════════════════════════════
    from anyio import to_thread

    timeout = config.CODING_CMD_TIMEOUT

    def _run_command():
        creationflags = 0
        if sys.platform == "win32":
            creationflags = getattr(
                subprocess, "CREATE_NO_WINDOW", 0x08000000
            )
        return subprocess.run(
            command,
            shell=True,
            cwd=str(work_dir),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            creationflags=creationflags,
        )

    try:
        result = await to_thread.run_sync(_run_command)

        stdout_str = (
            result.stdout.decode("utf-8", errors="replace")
            if result.stdout
            else ""
        )
        stderr_str = (
            result.stderr.decode("utf-8", errors="replace")
            if result.stderr
            else ""
        )

        output = ""
        if stdout_str:
            output += stdout_str
        if stderr_str:
            if output:
                output += "\n--- stderr ---\n"
            output += stderr_str

        if len(output) > 10000:
            output = output[:10000] + "\n... (输出已截断)"

        return json.dumps(
            {
                "exit_code": result.returncode,
                "output": output,
                "command": command,
            },
            ensure_ascii=False,
        )

    except subprocess.TimeoutExpired:
        return json.dumps(
            {
                "error": f"命令执行超时 ({timeout}s): {command}",
                "exit_code": -1,
            },
            ensure_ascii=False,
        )
    except Exception as e:
        return json.dumps(
            {
                "error": f"命令执行失败: {str(e)}",
                "exit_code": -1,
            },
            ensure_ascii=False,
        )


async def _ask_user(question: str) -> str:
    """向用户提问等待确认。"""
    from src.session.context import get_session_context
    ctx = get_session_context()
    session_id = ctx.get("session_id", "")
    return json.dumps(
        {
            "needs_user_input": True,
            "question": question,
            "session_id": session_id,
        },
        ensure_ascii=False,
    )


async def _apply_diff(path: str, diff: str) -> str:
    """使用 SEARCH/REPLACE 块格式对文件进行批量搜索替换编辑。"""
    from src.session.context import get_session_context
    ctx = get_session_context()
    workspace_path = ctx.get("workspace_path", "")
    ok, err, abs_path = _validate_and_resolve_path(workspace_path, path)
    if not ok:
        return json.dumps({"error": err}, ensure_ascii=False)
    if not abs_path.exists():
        return json.dumps({"error": f"文件不存在: {path}"}, ensure_ascii=False)
    ok, err = _check_file_size(abs_path)
    if not ok:
        return json.dumps({"error": err}, ensure_ascii=False)

    def _do_read_diff():
        with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    try:
        original_content = await _run_io(_do_read_diff)
    except Exception as e:
        return json.dumps({"error": f"读取文件失败: {str(e)}"}, ensure_ascii=False)

    # 解析 SEARCH/REPLACE 块
    blocks = list(_SEARCH_REPLACE_PATTERN.finditer(diff))
    if not blocks:
        return json.dumps(
            {"error": "未找到有效的 SEARCH/REPLACE 块。"
             "请使用格式: <<<<<<< SEARCH\\n原始代码\\n=======\\n新代码\\n>>>>>>> REPLACE"},
            ensure_ascii=False,
        )

    current_content = original_content
    results: list[dict] = []

    for idx, match in enumerate(blocks):
        search_text = match.group(1)
        replace_text = match.group(2)
        block_info = {"index": idx, "success": False, "message": ""}

        # 空搜索内容
        if not search_text.strip():
            block_info["message"] = f"第 {idx + 1} 块：SEARCH 部分为空，已跳过"
            results.append(block_info)
            continue

        # 1) 精确匹配
        if search_text in current_content:
            count = current_content.count(search_text)
            if count > 1:
                block_info["message"] = (
                    f"第 {idx + 1} 块：SEARCH 文本匹配到 {count} 处，"
                    f"请提供更多上下文使其唯一"
                )
                results.append(block_info)
                continue
            current_content = current_content.replace(search_text, replace_text, 1)
            block_info["success"] = True
            block_info["message"] = f"第 {idx + 1} 块：已应用"
            results.append(block_info)
            continue

        # 2) 宽松匹配（strip 首尾空白）
        search_stripped = search_text.strip()
        replace_stripped = replace_text.strip() if replace_text.strip() else ""
        if search_stripped and search_stripped in current_content:
            count = current_content.count(search_stripped)
            if count > 1:
                block_info["message"] = (
                    f"第 {idx + 1} 块：经过宽松匹配后 SEARCH 文本仍匹配到 {count} 处，"
                    f"请提供更多上下文"
                )
                results.append(block_info)
                continue
            current_content = current_content.replace(
                search_stripped, replace_stripped if replace_stripped else replace_text, 1
            )
            block_info["success"] = True
            block_info["message"] = f"第 {idx + 1} 块：已应用（宽松匹配）"
            results.append(block_info)
            continue

        # 3) 正则模糊匹配：将 SEARCH 中所有连续空白替换为 \s+
        # 先将连续空白替换为占位符，再 escape 非空白部分，最后还原 \s+
        _WS_PLACEHOLDER = "\x00WS\x00"
        parts = re.split(r"(\s+)", search_text)
        escaped_parts = []
        for part in parts:
            if re.fullmatch(r"\s+", part):
                escaped_parts.append(_WS_PLACEHOLDER)
            else:
                escaped_parts.append(re.escape(part))
        fuzzy_pattern = "".join(escaped_parts).replace(_WS_PLACEHOLDER, r"\s+")
        try:
            fuzzy_re = re.compile(fuzzy_pattern)
            fmatch = fuzzy_re.search(current_content)
            if fmatch:
                # 用实际匹配到的文本来替换
                actual_match = fmatch.group(0)
                current_content = current_content.replace(actual_match, replace_text, 1)
                block_info["success"] = True
                block_info["message"] = f"第 {idx + 1} 块：已应用（空白宽松匹配）"
                results.append(block_info)
                continue
        except re.error:
            pass

        # 4) 全部失败
        # 给出上下文帮助 LLM 修正
        first_line = search_text.strip().split("\n")[0][:80]
        block_info["message"] = (
            f"第 {idx + 1} 块：未找到 SEARCH 文本。"
            f"搜索起始内容: '{first_line}...'。"
            f"请使用 read_file 查看当前文件内容后重试。"
        )
        results.append(block_info)

    # 写回文件
    succeeded = sum(1 for r in results if r["success"])
    failed = len(results) - succeeded

    if succeeded > 0:
        def _do_apply_write():
            with open(abs_path, "w", encoding="utf-8", newline="") as f:
                f.write(current_content)
        try:
            await _run_io(_do_apply_write)
        except Exception as e:
            return json.dumps({"error": f"写入文件失败: {str(e)}"}, ensure_ascii=False)

    return json.dumps(
        {
            "file": path,
            "blocks": results,
            "total": len(results),
            "succeeded": succeeded,
            "failed": failed,
        },
        ensure_ascii=False,
    )


_SKIP_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "dist", "build",
    ".next", ".turbo", "target",
}
_MAX_DIR_ENTRIES = 500


def _walk_directory(
    abs_path: Path,
    workspace: Path,
    recursive: bool = True,
    include_dirs: bool = False,
    skip_hidden: bool = False,
    file_filter=None,
    max_entries: int = _MAX_DIR_ENTRIES,
) -> list[str]:
    """通用目录遍历：返回相对路径列表。

    Args:
        abs_path: 要遍历的目录绝对路径
        workspace: 工作区根路径，用于计算相对路径
        recursive: 是否递归遍历
        include_dirs: 是否在结果中包含目录（以 / 结尾）
        skip_hidden: 是否跳过 . 开头的文件/目录
        file_filter: 可选 `(filename: str) -> bool` 文件名过滤函数
        max_entries: 最大返回条目数
    """
    entries: list[str] = []

    def _to_rel(full: Path) -> str:
        try:
            return str(full.relative_to(workspace))
        except ValueError:
            return str(full)

    if recursive:
        for root, dirs, files in os.walk(abs_path):
            dirs[:] = [d for d in sorted(dirs)
                       if d not in _SKIP_DIRS and (not skip_hidden or not d.startswith("."))]
            if include_dirs:
                for d in dirs:
                    entries.append(_to_rel(Path(root) / d) + "/")
                    if len(entries) >= max_entries:
                        break
            for fname in sorted(files):
                if skip_hidden and fname.startswith("."):
                    continue
                if file_filter and not file_filter(fname):
                    continue
                entries.append(_to_rel(Path(root) / fname))
                if len(entries) >= max_entries:
                    break
            if len(entries) >= max_entries:
                break
    else:
        for item in sorted(abs_path.iterdir()):
            if skip_hidden and item.name.startswith("."):
                continue
            is_dir = item.is_dir()
            if is_dir:
                if item.name in _SKIP_DIRS:
                    continue
                if include_dirs:
                    entries.append(_to_rel(item) + "/")
            else:
                if file_filter and not file_filter(item.name):
                    continue
                entries.append(_to_rel(item))
            if len(entries) >= max_entries:
                break

    return entries


async def _search_file(
    path: str, pattern: str, recursive: bool = True, caseSensitive: bool = False
) -> str:
    """按文件名 glob 模式搜索文件。"""
    import fnmatch
    from src.session.context import get_session_context

    ctx = get_session_context()
    workspace_path = ctx.get("workspace_path", "")
    ok, err, abs_path = _validate_and_resolve_path(workspace_path, path)
    if not ok:
        return json.dumps({"error": err}, ensure_ascii=False)
    if not abs_path.exists():
        return json.dumps({"error": f"路径不存在: {path}"}, ensure_ascii=False)
    if not abs_path.is_dir():
        return json.dumps({"error": f"不是目录: {path}"}, ensure_ascii=False)

    workspace = Path(workspace_path).resolve()
    match_pattern = pattern if caseSensitive else pattern.lower()

    def _file_filter(fname: str) -> bool:
        name = fname if caseSensitive else fname.lower()
        return fnmatch.fnmatch(name, match_pattern)

    def _do_search_file():
        return _walk_directory(
            abs_path, workspace, recursive=recursive,
            skip_hidden=True, file_filter=_file_filter,
        )

    try:
        entries = await _run_io(_do_search_file)
        return json.dumps(
            {"matches": entries, "total": len(entries),
             "truncated": len(entries) >= _MAX_DIR_ENTRIES, "pattern": pattern},
            ensure_ascii=False,
        )
    except Exception as e:
        return json.dumps({"error": f"搜索文件失败: {str(e)}"}, ensure_ascii=False)


# ═══════════════════════════════════════════════════════════════
# create_coding_tools_direct() — 工厂函数
# ═══════════════════════════════════════════════════════════════


def _dummy_sync(**kwargs) -> str:
    raise NotImplementedError("Coding 工具仅支持异步调用")


def create_coding_tools_direct(
    approval_manager: Any = None,
) -> list[StructuredTool]:
    """创建 10 个直接实现的 coding 工具（主进程内执行）。

    session_id 和 workspace_path 通过 get_session_context() 运行时获取。

    Args:
        approval_manager: ApprovalManager 实例（execute_command 审批使用，可为 None）

    Returns:
        LangChain StructuredTool 列表
    """
    if not config.CODING_TOOLS_ENABLED:
        logger.info("编码工具已禁用（CODING_TOOLS_ENABLED=false），返回空列表")
        return []

    # execute_command 需要闭包捕获 approval_manager
    async def _exec_cmd_wrapper(command: str, cwd: str = "") -> str:
        return await _execute_command(command, cwd, approval_manager)

    tools = [
        StructuredTool(
            name="read_file",
            description="读取文件内容。支持行范围读取。",
            args_schema=_ReadFileArgs,
            func=_dummy_sync,
            coroutine=_read_file,
        ),
        StructuredTool(
            name="write_to_file",
            description="创建或覆盖整个文件。",
            args_schema=_WriteToFileArgs,
            func=_dummy_sync,
            coroutine=_write_to_file,
        ),
        StructuredTool(
            name="replace_in_file",
            description="在文件中进行搜索替换编辑（精确字符串匹配）。",
            args_schema=_ReplaceInFileArgs,
            func=_dummy_sync,
            coroutine=_replace_in_file,
        ),
        StructuredTool(
            name="search_files",
            description="在文件内容中进行正则搜索（自动尝试 ripgrep，不可用则降级为 Python）。",
            args_schema=_SearchFilesArgs,
            func=_dummy_sync,
            coroutine=_search_files,
        ),
        StructuredTool(
            name="search_file",
            description="按文件名 glob 模式搜索文件（如 '*.py', 'test_*.ts'）。区别于 search_files（按内容搜索）。",
            args_schema=_SearchFileArgs,
            func=_dummy_sync,
            coroutine=_search_file,
        ),
        StructuredTool(
            name="list_files",
            description="列出目录结构。",
            args_schema=_ListFilesArgs,
            func=_dummy_sync,
            coroutine=_list_files,
        ),
        StructuredTool(
            name="list_code_definitions",
            description="列出代码文件中的定义（函数、类、方法等）。",
            args_schema=_ListCodeDefinitionsArgs,
            func=_dummy_sync,
            coroutine=_list_code_definitions,
        ),
        StructuredTool(
            name="apply_diff",
            description=(
                "使用 SEARCH/REPLACE 块格式对文件进行批量搜索替换编辑。\n"
                "格式: <<<<<<< SEARCH\\n原始代码\\n=======\\n新代码\\n>>>>>>> REPLACE\n"
                "支持多块批量处理，允许小范围空白偏差模糊匹配。\n"
                "比 replace_in_file 更灵活：单次可修改多处，支持删除（REPLACE 为空）。"
            ),
            args_schema=_ApplyDiffArgs,
            func=_dummy_sync,
            coroutine=_apply_diff,
        ),
        StructuredTool(
            name="execute_command",
            description=(
                "执行终端命令。命令的工作目录默认为 session 的 workspace 根目录。\n"
                "⚠️ 高风险命令需要用户审批确认后才能执行。"
            ),
            args_schema=_ExecuteCommandArgs,
            func=_dummy_sync,
            coroutine=_exec_cmd_wrapper,
        ),
        StructuredTool(
            name="ask_user",
            description="向用户提问等待确认。",
            args_schema=_AskUserArgs,
            func=_dummy_sync,
            coroutine=_ask_user,
        ),
    ]

    logger.info(f"已创建 {len(tools)} 个直接编码工具")
    return tools
