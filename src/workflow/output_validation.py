"""Workflow Agent 最终输出的可选门禁。"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from .json_output import validate_and_format_json


MAX_JSON_FIELD_MIN_CHARS = 10_000_000
_JSON_FIELD_PATH_RE = re.compile(
    r"^[A-Za-z_][A-Za-z0-9_-]*(?:\.(?:[A-Za-z_][A-Za-z0-9_-]*|\d+))*$"
)


@dataclass(frozen=True)
class OutputValidationResult:
    success: bool
    error: str = ""


def is_valid_json_field_path(path: str) -> bool:
    """字段路径使用点号分隔，并允许数组下标，如 chapters.0.body。"""
    return bool(_JSON_FIELD_PATH_RE.fullmatch(path.strip()))


def validate_agent_output(
    raw_output: str,
    *,
    require_non_empty: bool = False,
    json_field: str = "",
    json_field_min_chars: int = 0,
) -> OutputValidationResult:
    """校验 Agent 最终输出；失败由工作流节点重试策略统一处理。"""
    if require_non_empty and not raw_output.strip():
        return OutputValidationResult(False, "LLM 最终输出为空")

    field_path = json_field.strip()
    if not field_path and json_field_min_chars == 0:
        return OutputValidationResult(True)

    parsed = validate_and_format_json(raw_output, repair=True)
    if not parsed.success:
        return OutputValidationResult(
            False,
            f"LLM 最终输出不是有效 JSON: {parsed.error}",
        )

    try:
        value: Any = json.loads(parsed.formatted)
    except (json.JSONDecodeError, TypeError) as exc:
        return OutputValidationResult(False, f"LLM 最终输出不是有效 JSON: {exc}")

    for segment in field_path.split("."):
        if isinstance(value, dict) and segment in value:
            value = value[segment]
            continue
        if isinstance(value, list) and segment.isdigit():
            index = int(segment)
            if index < len(value):
                value = value[index]
                continue
        return OutputValidationResult(
            False,
            f"LLM JSON 输出缺少字段 '{field_path}'",
        )

    if not isinstance(value, str):
        return OutputValidationResult(
            False,
            f"LLM JSON 输出字段 '{field_path}' 必须是字符串",
        )

    actual_chars = len(value.strip())
    if actual_chars <= json_field_min_chars:
        return OutputValidationResult(
            False,
            (
                f"LLM JSON 输出字段 '{field_path}' 字数为 {actual_chars}，"
                f"必须大于 {json_field_min_chars}"
            ),
        )
    return OutputValidationResult(True)
