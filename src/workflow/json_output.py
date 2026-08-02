"""Workflow Agent JSON 输出校验与安全修复工具。"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from json import JSONDecodeError


JSON_REPAIR_POLICIES = {
    "none",
    "validate_only",
    "safe_repair",
    "safe_repair_then_retry",
    "retry_only",
}


@dataclass
class JsonValidationResult:
    success: bool
    formatted: str = ""
    data: object | None = None
    error: str = ""
    repaired_text: str = ""
    repairs: list[str] = field(default_factory=list)


def detect_output_format(output_file_path: str, node_params: dict | None = None) -> str:
    """推断节点输出格式。"""
    params = node_params or {}
    explicit = str(params.get("output_format", "") or "").strip().lower()
    if explicit in {"text", "json", "markdown"}:
        return explicit
    if output_file_path.lower().strip().endswith(".json"):
        return "json"
    return "text"


def get_json_policy(output_file_path: str, node_params: dict | None = None) -> str:
    """读取 JSON 修复策略，.json 输出默认启用 safe_repair_then_retry。"""
    params = node_params or {}
    raw = str(params.get("json_repair_policy", "") or "").strip().lower()
    if raw in JSON_REPAIR_POLICIES:
        return raw
    if detect_output_format(output_file_path, params) == "json":
        return "safe_repair_then_retry"
    return "none"


def get_json_retry_count(node_params: dict | None = None) -> int:
    """读取 JSON 重试次数，默认 1。"""
    params = node_params or {}
    raw = params.get("json_retry_count", 1)
    try:
        return max(0, int(raw))
    except (TypeError, ValueError):
        return 1


def validate_and_format_json(text: str, repair: bool = True) -> JsonValidationResult:
    """校验 JSON；可选在原文非法时执行确定性安全修复。"""
    original = text or ""
    try:
        data = json.loads(original)
        return JsonValidationResult(
            success=True,
            formatted=json.dumps(data, ensure_ascii=False, indent=2),
            data=data,
            repaired_text=original,
            repairs=[],
        )
    except JSONDecodeError:
        if not repair:
            try:
                json.loads(original)
            except JSONDecodeError as e:
                return JsonValidationResult(
                    success=False,
                    error=str(e),
                    repaired_text=original,
                    repairs=[],
                )

    candidate, repairs = safe_repair_json_text(original)

    try:
        data = json.loads(candidate)
    except JSONDecodeError as e:
        return JsonValidationResult(
            success=False,
            error=str(e),
            repaired_text=candidate,
            repairs=repairs,
        )

    return JsonValidationResult(
        success=True,
        formatted=json.dumps(data, ensure_ascii=False, indent=2),
        data=data,
        repaired_text=candidate,
        repairs=repairs,
    )


def safe_repair_json_text(text: str) -> tuple[str, list[str]]:
    """执行低风险 JSON 文本修复，不做语义补全。"""
    candidate = text.strip()
    repairs: list[str] = []

    unfenced = _strip_markdown_fence(candidate)
    if unfenced != candidate:
        candidate = unfenced
        repairs.append("strip_markdown_fence")

    normalized = _normalize_quotes(candidate)
    if normalized != candidate:
        candidate = normalized
        repairs.append("normalize_quotes")

    extracted = _extract_json_body(candidate)
    if extracted != candidate:
        candidate = extracted
        repairs.append("extract_json_body")

    no_trailing_commas = _remove_trailing_commas(candidate)
    if no_trailing_commas != candidate:
        candidate = no_trailing_commas
        repairs.append("remove_trailing_commas")

    if _try_loads(candidate):
        return candidate, repairs

    without_orphans = _remove_obvious_orphan_lines(candidate)
    if without_orphans != candidate:
        without_orphans = _remove_trailing_commas(without_orphans)
        if _try_loads(without_orphans):
            candidate = without_orphans
            repairs.append("remove_orphan_lines")

    return candidate, repairs


def get_json_error_context(text: str, error: str, radius: int = 160) -> str:
    """从 JSONDecodeError 文本中的 char 位置提取错误附近内容。"""
    match = re.search(r"char\s+(\d+)", error or "")
    if not match:
        return (text or "")[: radius * 2]
    pos = int(match.group(1))
    start = max(0, pos - radius)
    end = min(len(text or ""), pos + radius)
    return (text or "")[start:end]


def build_json_retry_prompt(error_message: str, context: str) -> str:
    return (
        "你的上一次输出不是合法 JSON，错误如下：\n\n"
        f"{error_message}\n\n"
        "错误附近内容：\n"
        f"{context}\n\n"
        "请只返回修复后的完整 JSON。\n"
        "不得输出 Markdown、解释、注释或额外文字。"
    )


def _strip_markdown_fence(text: str) -> str:
    stripped = text.strip()
    match = re.fullmatch(r"```(?:json|JSON)?\s*\n?(.*?)\n?```", stripped, flags=re.DOTALL)
    if match:
        return match.group(1).strip()
    lines = [line for line in stripped.splitlines() if not line.strip().startswith("```")]
    return "\n".join(lines).strip()


def _normalize_quotes(text: str) -> str:
    return (
        text.replace("“", '"')
        .replace("”", '"')
        .replace("＂", '"')
        .replace("‘", "'")
        .replace("’", "'")
    )


def _extract_json_body(text: str) -> str:
    start_positions = [(text.find("{"), "{"), (text.find("["), "[")]
    start_positions = [(idx, ch) for idx, ch in start_positions if idx != -1]
    if not start_positions:
        return text.strip()
    start, opening = min(start_positions, key=lambda item: item[0])
    closing = "}" if opening == "{" else "]"
    end = _find_matching_bracket(text, start, opening, closing)
    if end == -1:
        return text[start:].strip()
    return text[start : end + 1].strip()


def _find_matching_bracket(text: str, start: int, opening: str, closing: str) -> int:
    stack: list[str] = []
    in_string = False
    escape = False
    for idx in range(start, len(text)):
        ch = text[idx]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch in "{[":
            stack.append("}" if ch == "{" else "]")
        elif ch in "}]":
            if not stack or ch != stack[-1]:
                return -1
            stack.pop()
            if not stack and ch == closing:
                return idx
    return -1


def _remove_trailing_commas(text: str) -> str:
    previous = None
    current = text
    while previous != current:
        previous = current
        current = re.sub(r",\s*([}\]])", r"\1", current)
    return current


def _remove_obvious_orphan_lines(text: str) -> str:
    kept: list[str] = []
    changed = False
    for line in text.splitlines():
        stripped = line.strip()
        if _is_obvious_orphan_line(stripped):
            changed = True
            continue
        kept.append(line)
    return "\n".join(kept) if changed else text


def _is_obvious_orphan_line(stripped: str) -> bool:
    if not stripped:
        return False
    valid_prefixes = ('"', "{", "}", "[", "]")
    if stripped.startswith(valid_prefixes):
        return False
    if re.fullmatch(r"-?\d+(?:\.\d+)?[,]?", stripped):
        return False
    if stripped in {"true", "false", "null", "true,", "false,", "null,"}:
        return False
    return ":" not in stripped


def _try_loads(text: str) -> bool:
    try:
        json.loads(text)
        return True
    except JSONDecodeError:
        return False
