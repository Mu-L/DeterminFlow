"""
ScriptNode — 脚本执行节点插件。

在 workflow-main 所在进程内通过 subprocess 直接执行 shell/python 脚本。
脚本文件存储在 data/workflows/{workflow_id}/script/{name}.{ext}，
执行工作目录为工作流共享 workspace（data/workspaces/{workflow_id}/）。

脚本输出管理：
- stdout / stderr 全文存入 task JSON 的 node_states[node_id]（最大 50KB，超出截断）
- <WF_VAR>key:value</WF_VAR> 标签自动解析为节点 outputs，下游可通过 {{key}} 引用
- <script_out>...</script_out> 标签内容提取为节点 summary
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import os
import shlex
import subprocess
import sys
from pathlib import Path

from .base import BaseNodePlugin, NodeContext, NodeResult

logger = logging.getLogger(__name__)

# 从 stdout 提取 <script_out>...</script_out> 摘要内容
_SCRIPT_OUT_RE = re.compile(r"<script_out>(.*?)</script_out>", re.DOTALL)

# 从 stdout 提取 <WF_VAR>key:value</WF_VAR> 变量
_WF_VAR_RE = re.compile(r"<WF_VAR>(.+?):(.+?)</WF_VAR>")

# 从 stdout 提取 <WF_REJECT_UPSTREAM target="node_id">...</WF_REJECT_UPSTREAM>
_WF_REJECT_UPSTREAM_RE = re.compile(
    r"<WF_REJECT_UPSTREAM(?P<attrs>[^>]*)>(?P<reason>.*?)</WF_REJECT_UPSTREAM>",
    re.DOTALL,
)
_WF_REJECT_TARGET_RE = re.compile(r"\btarget\s*=\s*['\"](?P<target>[\w\-]+)['\"]")

# 变量名安全校验
_VAR_KEY_RE = re.compile(r"^\w[\w\-]*$")

# 脚本名安全校验：仅允许字母数字下划线连字符，不含扩展名（系统自动添加）
_SCRIPT_NAME_RE = re.compile(r"^[\w]+$")

# 脚本文件扩展名映射
_EXT_MAP = {"shell": "sh", "python": "py"}

# 脚本解释器
_INTERPRETER_MAP = {"shell": "bash", "python": sys.executable}

# stdout/stderr 最大保留字节数
_MAX_OUTPUT_BYTES = 50 * 1024  # 50KB


def _truncate(text: str, max_bytes: int = _MAX_OUTPUT_BYTES) -> str:
    """按字节截断文本，超出末尾追加提示。"""
    encoded = text.encode("utf-8", errors="replace")
    if len(encoded) <= max_bytes:
        return text
    truncated = encoded[:max_bytes].decode("utf-8", errors="replace")
    return truncated + f"\n...[output truncated, total {len(encoded)} bytes]"


def _parse_wf_vars(stdout_text: str) -> dict[str, str]:
    """从 stdout 中解析 <WF_VAR>key:value</WF_VAR> 变量。"""
    outputs: dict[str, str] = {}
    for m in _WF_VAR_RE.finditer(stdout_text):
        key = m.group(1).strip()
        value = m.group(2).strip()
        if key and _VAR_KEY_RE.match(key):
            outputs[key] = value
    return outputs


def _parse_summary(stdout_text: str) -> str:
    """从 stdout 中提取 <script_out> 摘要。"""
    match = _SCRIPT_OUT_RE.search(stdout_text)
    if match:
        return match.group(1).strip()
    return ""


def _parse_reject_upstream(stdout_text: str) -> tuple[str, str] | None:
    """从 stdout 中解析打回上游请求，返回 (reason, target_node_id)。"""
    match = _WF_REJECT_UPSTREAM_RE.search(stdout_text)
    if not match:
        return None
    reason = match.group("reason").strip()
    attrs = match.group("attrs") or ""
    target_match = _WF_REJECT_TARGET_RE.search(attrs)
    target = target_match.group("target").strip() if target_match else ""
    return reason, target


def _as_bool(value, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _build_result(status: str, stdout_text: str, stderr_text: str,
                  exit_code: int | None, error: str = "") -> NodeResult:
    """构建脚本节点的 NodeResult，含输出截断、变量解析、摘要提取。"""
    return NodeResult(
        status=status,
        summary=_parse_summary(stdout_text),
        error=error,
        stdout=_truncate(stdout_text),
        stderr=_truncate(stderr_text),
        outputs=_parse_wf_vars(stdout_text),
    )


class ScriptNode(BaseNodePlugin):
    node_type = "script"
    label = "脚本执行"
    default_icon = "terminal"

    @property
    def params_schema(self) -> list[dict]:
        return [
            {
                "key": "label",
                "label": "节点名称",
                "type": "text",
                "required": False,
                "default": "",
                "description": "节点在画布上的显示名称",
            },
            {
                "key": "script_source",
                "label": "脚本来源",
                "type": "select",
                "required": False,
                "default": "inline",
                "description": "脚本来源：直接编辑或从脚本库引用",
                "options": [
                    {"name": "直接编辑", "value": "inline"},
                    {"name": "脚本库", "value": "library"},
                ],
            },
            {
                "key": "script_type",
                "label": "脚本类型",
                "type": "select",
                "required": True,
                "default": "shell",
                "description": "脚本类型：Shell 或 Python",
                "options": [
                    {"name": "Shell", "value": "shell"},
                    {"name": "Python", "value": "python"},
                ],
            },
            {
                "key": "script_name",
                "label": "脚本名",
                "type": "text",
                "required": True,
                "default": "",
                "description": "脚本文件名（不含扩展名），如 deploy",
                "placeholder": "例如: deploy",
            },
            {
                "key": "script_group",
                "label": "分组（脚本库）",
                "type": "text",
                "required": False,
                "default": "",
                "description": "脚本库中的分组名（仅脚本来源为脚本库时有效）",
                "placeholder": "例如: utils",
            },
            {
                "key": "script_args",
                "label": "脚本参数",
                "type": "text",
                "required": False,
                "default": "",
                "description": "兼容用命令行参数，支持 {{key}} 占位符；资源定义优先使用 script_argv 数组",
                "placeholder": "--verbose --env dev",
            },
            {
                "key": "timeout",
                "label": "超时时间（秒）",
                "type": "number",
                "required": False,
                "default": "300",
                "description": "脚本执行超时时间，默认 300 秒",
            },
            {
                "key": "enable_reject_upstream",
                "label": "允许打回上游",
                "type": "boolean",
                "required": False,
                "default": False,
                "description": "开启后脚本可通过 <WF_REJECT_UPSTREAM>...</WF_REJECT_UPSTREAM> 请求上游节点重试",
            },
            {
                "key": "max_reject_count",
                "label": "最大打回次数",
                "type": "number",
                "required": False,
                "default": "3",
                "description": "允许同一上游节点被此脚本节点自动打回的最大次数",
            },
        ]

    async def execute(self, ctx: NodeContext) -> NodeResult:
        """执行脚本节点。"""
        from ..definition import resolve_placeholders

        node_def = ctx.node_def
        pv = ctx.parameter_values
        node_params = node_def.node_params or {}

        # 读取配置
        script_source = node_params.get("script_source", "inline")
        script_type = node_params.get("script_type", "shell")
        script_name = node_params.get("script_name", "").strip()
        script_group = node_params.get("script_group", "").strip()
        # 变量替换（含嵌套引用 + 文件变量读取）
        variables = ctx.definition.variables
        shared_ws = str(ctx.shared_ws) if ctx.shared_ws else ""
        raw_script_argv = node_params.get("script_argv")
        script_argv: list[str] = []
        if raw_script_argv is not None:
            if not isinstance(raw_script_argv, list) or not all(
                isinstance(item, str) for item in raw_script_argv
            ):
                return NodeResult(
                    status="failed",
                    error="script_argv 必须是字符串数组",
                )
            for item in raw_script_argv:
                script_argv.append(
                    await resolve_placeholders(item, pv, variables, shared_ws)
                )
        else:
            script_args = await resolve_placeholders(
                node_params.get("script_args", ""), pv, variables, shared_ws,
            )
            try:
                script_argv = shlex.split(script_args) if script_args else []
            except ValueError as exc:
                return NodeResult(
                    status="failed",
                    error=f"脚本参数解析失败: {exc}",
                )
        timeout_str = node_params.get("timeout", "300")
        try:
            timeout = int(timeout_str)
        except (ValueError, TypeError):
            timeout = 300

        # 脚本名安全校验
        if not script_name:
            return NodeResult(status="failed", error="脚本名为空")
        if not _SCRIPT_NAME_RE.match(script_name):
            return NodeResult(
                status="failed",
                error=f"脚本名包含非法字符: {script_name}（仅允许字母、数字、下划线）",
            )

        # 构建脚本文件路径
        ext = _EXT_MAP.get(script_type, "sh")
        script_environment: dict[str, str] = {}
        script_library_catalog = None

        if script_source == "library":
            # 脚本库来源：data/script-library/{group}/{script-name}/script.{ext}
            if not script_group:
                return NodeResult(
                    status="failed",
                    error="脚本来源为脚本库时，分组名不能为空",
                )
            from src.workflow.script_library import (
                ScriptLibraryError,
                get_script_library,
            )
            script_library_catalog = get_script_library()
            try:
                location = script_library_catalog.resolve(
                    script_group,
                    script_name,
                )
            except (ScriptLibraryError, ValueError) as exc:
                return NodeResult(
                    status="failed",
                    error=(
                        f"无法解析脚本库资源: {script_group}/{script_name}: "
                        f"{exc}"
                    ),
                )
            script_file = (
                location.directory / f"{script_name}.{ext}"
                if location else Path("__missing_extension_script__")
            )
            if location is not None:
                script_environment = script_library_catalog.environment(
                    location.owner
                )
        else:
            # 本地脚本：data/workflows/{workflow_id}/script/{name}.{ext}
            from src.config import WORKFLOWS_DIR
            script_environment = dict(ctx.owner_environment)
            # 防止 workflow_id 含 ../ 导致路径穿越（远程代码执行风险）
            wf_dir = WORKFLOWS_DIR / ctx.workflow_id
            if not wf_dir.is_relative_to(WORKFLOWS_DIR):
                return NodeResult(
                    status="failed",
                    error=f"非法的工作流 ID（路径穿越）: {ctx.workflow_id}",
                )
            script_file = wf_dir / "script" / f"{script_name}.{ext}"

        if not script_file.exists():
            return NodeResult(
                status="failed",
                error=f"脚本文件不存在: {script_file}",
            )

        if script_source == "library":
            from ..runtime_guards import RUNTIME_GUARD_KEY, RUNTIME_GUARD_SCHEMA
            runtime_guard = node_params.get(RUNTIME_GUARD_KEY)
            if (
                not isinstance(runtime_guard, dict)
                or runtime_guard.get("schema_version") != RUNTIME_GUARD_SCHEMA
                or not isinstance(
                    runtime_guard.get("library_script_attestation"),
                    dict,
                )
            ):
                return NodeResult(
                    status="failed",
                    error="Workflow Script Library 运行身份守卫无效",
                )
            try:
                script_library_catalog.verify_attestation(
                    runtime_guard["library_script_attestation"]
                )
            except ScriptLibraryError as exc:
                return NodeResult(status="failed", error=str(exc))

        # 读取脚本内容、核验 Task 创建时冻结的实际文件身份，并校验非空
        try:
            script_bytes = script_file.read_bytes()
            script_content = script_bytes.decode("utf-8").strip()
        except Exception as e:
            return NodeResult(status="failed", error=f"读取脚本文件失败: {e}")

        if script_source == "inline":
            from ..runtime_guards import (
                RUNTIME_GUARD_KEY,
                RUNTIME_GUARD_SCHEMA,
                WorkflowRuntimeGuardError,
                inline_script_dependency_identities,
            )

            runtime_guard = node_params.get(RUNTIME_GUARD_KEY)
            if runtime_guard is not None:
                if not isinstance(runtime_guard, dict) or runtime_guard.get(
                    "schema_version"
                ) != RUNTIME_GUARD_SCHEMA:
                    return NodeResult(
                        status="failed",
                        error="Workflow inline script 运行身份守卫无效",
                    )
                expected_sha256 = runtime_guard.get("inline_script_sha256")
                actual_sha256 = hashlib.sha256(script_bytes).hexdigest()
                if not expected_sha256 or actual_sha256 != expected_sha256:
                    return NodeResult(
                        status="failed",
                        error=(
                            "Workflow inline script 已漂移，拒绝执行: "
                            f"node={node_def.id}, expected={expected_sha256 or 'missing'}, "
                            f"actual={actual_sha256}"
                        ),
                    )
                try:
                    actual_dependencies = (
                        inline_script_dependency_identities(
                            script_file.parent,
                            node_params.get("script_dependencies"),
                        )
                    )
                except WorkflowRuntimeGuardError as exc:
                    return NodeResult(status="failed", error=str(exc))
                if actual_dependencies != runtime_guard.get(
                    "inline_script_dependencies",
                    [],
                ):
                    return NodeResult(
                        status="failed",
                        error=(
                            "Workflow inline script dependency 已漂移，拒绝执行: "
                            f"node={node_def.id}"
                        ),
                    )

        if not script_content:
            return NodeResult(status="failed", error=f"脚本内容为空: {script_file}")

        # 工作目录：共享 workspace
        working_dir = str(ctx.shared_ws) if ctx.shared_ws else "."

        # 构建命令
        interpreter = _INTERPRETER_MAP.get(script_type, "bash")
        cmd_line = [interpreter, str(script_file)]
        cmd_line.extend(script_argv)

        # 环境变量
        env = os.environ.copy()
        env["WORKFLOW_ID"] = ctx.workflow_id
        env["TASK_ID"] = ctx.task_id
        env["SCRIPT_DIR"] = str(script_file.parent)
        env["WORKSPACE_DIR"] = working_dir
        env.update(script_environment)

        logger.info(
            f"执行脚本: {script_file} (type={script_type}, timeout={timeout}s, "
            f"cwd={working_dir})"
        )

        try:
            subprocess_options = {}
            if sys.platform == "win32":
                subprocess_options["creationflags"] = getattr(
                    subprocess, "CREATE_NO_WINDOW", 0x08000000
                )
            process = await asyncio.create_subprocess_exec(
                *cmd_line,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=working_dir,
                env=env,
                **subprocess_options,
            )
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                process.communicate(), timeout=timeout,
            )
        except asyncio.TimeoutError:
            # 超时后尝试杀进程，读取已产生的部分输出
            stdout_text = ""
            stderr_text = ""
            try:
                process.kill()
                partial_stdout, partial_stderr = await process.communicate()
                stdout_text = partial_stdout.decode("utf-8", errors="replace") if partial_stdout else ""
                stderr_text = partial_stderr.decode("utf-8", errors="replace") if partial_stderr else ""
            except Exception:
                pass
            err_msg = f"脚本执行超时（超过 {timeout}s）"
            return _build_result(
                status="failed", error=err_msg,
                stdout_text=stdout_text, stderr_text=stderr_text,
                exit_code=None,
            )

        exit_code = process.returncode

        stdout_text = stdout_bytes.decode("utf-8", errors="replace")
        stderr_text = stderr_bytes.decode("utf-8", errors="replace")
        reject_request = _parse_reject_upstream(stdout_text)
        if reject_request:
            reason, target_node_id = reject_request
            reject_enabled = _as_bool(
                node_params.get("enable_reject_upstream"),
                getattr(node_def, "enable_reject_upstream", False),
            )
            if not reject_enabled:
                return _build_result(
                    status="failed",
                    error="脚本请求打回上游，但当前脚本节点未开启 enable_reject_upstream",
                    stdout_text=stdout_text,
                    stderr_text=stderr_text,
                    exit_code=exit_code,
                )
            if not reason:
                return _build_result(
                    status="failed",
                    error="脚本请求打回上游，但 WF_REJECT_UPSTREAM 内容为空",
                    stdout_text=stdout_text,
                    stderr_text=stderr_text,
                    exit_code=exit_code,
                )
            if not ctx.on_reject_upstream:
                return _build_result(
                    status="failed",
                    error="脚本请求打回上游，但当前执行上下文不支持 reject_upstream",
                    stdout_text=stdout_text,
                    stderr_text=stderr_text,
                    exit_code=exit_code,
                )

            reject_result = ctx.on_reject_upstream(
                f"script:{node_def.id}",
                reason,
                target_node_id,
            )
            if not isinstance(reject_result, dict) or not reject_result.get("success"):
                message = (
                    reject_result.get("message")
                    if isinstance(reject_result, dict)
                    else "reject_upstream 回调未返回成功结果"
                )
                return _build_result(
                    status="failed",
                    error=message,
                    stdout_text=stdout_text,
                    stderr_text=stderr_text,
                    exit_code=exit_code,
                )
            return _build_result(
                status="failed",
                error=reject_result.get("message", "已请求打回上游节点"),
                stdout_text=stdout_text,
                stderr_text=stderr_text,
                exit_code=exit_code,
            )

        if exit_code == 0:
            logger.info(f"脚本执行成功: {script_file} (exit_code=0)")
            return _build_result(
                status="success",
                stdout_text=stdout_text, stderr_text=stderr_text,
                exit_code=exit_code,
            )
        else:
            error_msg = stderr_text.strip() or f"退出码 {exit_code}"
            error_msg = error_msg[-2000:]  # 截断
            logger.warning(f"脚本执行失败: {script_file} (exit_code={exit_code})")
            return _build_result(
                status="failed", error=error_msg,
                stdout_text=stdout_text, stderr_text=stderr_text,
                exit_code=exit_code,
            )
