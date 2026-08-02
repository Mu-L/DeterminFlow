from __future__ import annotations

import json
import logging
import re
from pathlib import Path

import aiofiles


_PLACEHOLDER_RE = re.compile(r"\{\{([\w.-]+)\}\}")
_NESTED_LIST_RE = re.compile(r"\{\{(\w+)\[(\w+)\]\}\}")
_NESTED_DICT_RE = re.compile(r"\{\{(\w+)\.(\w+)\}\}")
_LOOP_EXPR_RE = re.compile(
    r"^for\s+(\w+(?:\s*,\s*\w+)?)\s+in\s+"
    r"(?:range\((\d+)(?:\s*,\s*(\d+))?\)|(\w+))\s*$"
)
_MAX_RESOLVE_DEPTH = 10

logger = logging.getLogger(__name__)


def _try_parse_json(value: str) -> tuple[bool, object]:
    """尝试将字符串解析为 JSON（list/dict），返回解析状态和值。"""
    stripped = value.strip()
    if not stripped:
        return False, value
    try:
        parsed = json.loads(stripped)
        if isinstance(parsed, (list, dict)):
            return True, parsed
    except (json.JSONDecodeError, ValueError):
        pass
    return False, value


def parse_loop_expression(expression: str) -> dict:
    """解析循环网关出边表达式，返回迭代元信息。"""
    expr = expression.strip()
    match = _LOOP_EXPR_RE.match(expr)
    if not match:
        raise ValueError(
            f"循环表达式格式错误: '{expr}'。"
            "支持: for item in list_var / for key, value in dict_var / "
            "for i in range(N[,M])"
        )

    var_part = match.group(1).strip()
    range_start_str = match.group(2)
    range_end_str = match.group(3)
    identifier = match.group(4)

    if range_start_str is not None:
        start = int(range_start_str)
        end = int(range_end_str) if range_end_str else start
        if not range_end_str:
            start = 0
        return {
            "mode": "range",
            "iter_var": var_part,
            "range_start": start,
            "range_end": end,
        }
    if identifier:
        if "," in var_part:
            vars_ = [value.strip() for value in var_part.split(",", 1)]
            if len(vars_) != 2:
                raise ValueError("dict 遍历需要恰好两个变量: for key, value in ...")
            return {
                "mode": "dict",
                "iter_var": (vars_[0], vars_[1]),
                "source": identifier,
            }
        return {
            "mode": "list",
            "iter_var": var_part,
            "source": identifier,
        }
    raise ValueError(f"无法解析循环表达式: {expr}")


def _resolve_nested_value(
    value: str,
    values: dict[str, str],
    visited: set[str] | None = None,
    depth: int = 0,
) -> str:
    """递归展开嵌套变量，并检测循环依赖。"""
    if not value or "{{" not in value:
        return value
    if visited is None:
        visited = set()
    if depth > _MAX_RESOLVE_DEPTH:
        raise ValueError(
            f"变量展开超过最大深度 {_MAX_RESOLVE_DEPTH}，可能存在深层嵌套引用"
        )

    result = value
    while match := _PLACEHOLDER_RE.search(result):
        key = match.group(1)
        if key in visited:
            raise ValueError(f"变量循环引用: {' -> '.join(visited)} -> {key}")
        if key not in values:
            break
        replacement = _resolve_nested_value(
            values[key] or "",
            values,
            visited | {key},
            depth + 1,
        )
        result = result[:match.start()] + replacement + result[match.end():]
    return result


def _variable_attribute(variable, name: str, default):
    if hasattr(variable, name):
        return getattr(variable, name)
    return variable.get(name, default)


def resolve_workspace_file_path(shared_ws: str | Path, target: str) -> Path:
    """Resolve a workflow file path and fail closed outside its workspace."""
    if not shared_ws:
        raise ValueError("未配置 Workflow workspace，拒绝访问文件")
    workspace = Path(shared_ws).resolve()
    requested = Path(target)
    resolved = (
        requested.resolve()
        if requested.is_absolute()
        else (workspace / requested).resolve()
    )
    if not resolved.is_relative_to(workspace):
        raise ValueError(f"Workflow 文件路径越出 workspace: {target}")
    return resolved


async def resolve_placeholders(
    content: str,
    values: dict[str, str],
    variables: list | None = None,
    shared_ws: str = "",
) -> str:
    """展开嵌套变量、文件变量和普通占位符。"""
    if not content or not values:
        return content

    resolved_values: dict[str, str] = {}
    for key, value in values.items():
        try:
            resolved_values[key] = _resolve_nested_value(value or "", values)
        except ValueError as exc:
            logger.warning(f"变量 {key} 嵌套展开失败: {exc}")
            resolved_values[key] = value or ""

    if variables and shared_ws:
        for variable in variables:
            var_type = _variable_attribute(variable, "type", "text")
            var_key = _variable_attribute(variable, "key", "")
            var_required = _variable_attribute(variable, "required", False)
            if var_type != "file" or var_key not in resolved_values:
                continue

            file_path_str = resolved_values[var_key].strip()
            if not file_path_str:
                resolved_values[var_key] = ""
                continue
            file_path = resolve_workspace_file_path(shared_ws, file_path_str)
            try:
                if file_path.exists() and file_path.is_file():
                    async with aiofiles.open(file_path, "r", encoding="utf-8") as file:
                        resolved_values[var_key] = await file.read()
                    logger.info(f"文件变量 {var_key} 已读取: {file_path}")
                elif var_required:
                    raise FileNotFoundError(
                        f"文件变量 {var_key} 必填但文件不存在: {file_path}"
                    )
                else:
                    resolved_values[var_key] = ""
                    logger.warning(f"文件变量 {var_key} 文件不存在，替换为空: {file_path}")
            except FileNotFoundError:
                raise
            except Exception as exc:
                logger.error(f"读取文件变量 {var_key} 失败 ({file_path}): {exc}")
                if var_required:
                    raise
                resolved_values[var_key] = ""

    file_var_keys = {
        _variable_attribute(variable, "key", "")
        for variable in (variables or [])
        if _variable_attribute(variable, "type", "text") == "file"
    }
    for key, original_value in values.items():
        if key in file_var_keys or not original_value or "{{" not in original_value:
            continue
        try:
            resolved_values[key] = _resolve_nested_value(
                original_value,
                resolved_values,
            )
        except ValueError as exc:
            logger.warning(f"变量 {key} 二级嵌套展开失败: {exc}")

    result = content
    for match in _NESTED_LIST_RE.finditer(result):
        var_key = match.group(1)
        idx_ref = match.group(2)
        if var_key not in resolved_values:
            continue
        idx_str = resolved_values.get(idx_ref, idx_ref)
        try:
            idx_val = int(idx_str)
        except ValueError as exc:
            raise ValueError(f"列表索引不是有效数字: {var_key}[{idx_str}]") from exc
        ok, parsed = _try_parse_json(resolved_values[var_key])
        if not ok:
            raw_value = resolved_values[var_key]
            if "\n" not in raw_value:
                raise ValueError(f"变量 {var_key} 不是列表类型")
            lines = [line.strip() for line in raw_value.split("\n") if line.strip()]
            if not 0 <= idx_val < len(lines):
                raise ValueError(
                    f"变量 {var_key} 按行分割后索引 {idx_val} 越界 "
                    f"(len={len(lines)})"
                )
            replacement = lines[idx_val]
        else:
            if not isinstance(parsed, list):
                raise ValueError(f"变量 {var_key} 不是列表类型")
            if not 0 <= idx_val < len(parsed):
                raise ValueError(
                    f"列表索引越界: {var_key}[{idx_val}] (len={len(parsed)})"
                )
            replacement = str(parsed[idx_val])
        result = result.replace(match.group(0), replacement)

    for match in _NESTED_DICT_RE.finditer(result):
        var_key = match.group(1)
        dict_key = match.group(2)
        if var_key not in resolved_values:
            continue
        ok, parsed = _try_parse_json(resolved_values[var_key])
        if not ok:
            raise ValueError(f"变量 {var_key} 不是字典类型（无法解析为 JSON）")
        if not isinstance(parsed, dict):
            raise ValueError(f"变量 {var_key} 不是字典类型")
        result = result.replace(match.group(0), str(parsed.get(dict_key, "")))

    for match in _PLACEHOLDER_RE.finditer(result):
        key = match.group(1)
        if key in resolved_values:
            result = result.replace(f"{{{{{key}}}}}", resolved_values[key] or "")
    return result
