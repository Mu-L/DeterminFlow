"""One-shot inference probe for configured Provider models."""

from __future__ import annotations

import asyncio
import logging
from time import perf_counter
from typing import Any

import anthropic
import openai

from src.core.llm_client import (
    ModelConfigurationNotAvailableError,
    ModelCredentialNotConfiguredError,
    create_llm,
)
from src.core.model_manager import get_model_manager


logger = logging.getLogger(__name__)

PROBE_PROMPT = "Reply with exactly OK."
PROBE_MAX_OUTPUT_TOKENS = 32
PROBE_TIMEOUT_SECONDS = 30.0

_AUTH_ERRORS = (
    openai.AuthenticationError,
    openai.PermissionDeniedError,
    anthropic.AuthenticationError,
    anthropic.PermissionDeniedError,
)
_RATE_LIMIT_ERRORS = (openai.RateLimitError, anthropic.RateLimitError)
_TIMEOUT_ERRORS = (
    asyncio.TimeoutError,
    openai.APITimeoutError,
    anthropic.APITimeoutError,
)
_CONNECTION_ERRORS = (
    openai.APIConnectionError,
    anthropic.APIConnectionError,
)
_REJECTED_REQUEST_ERRORS = (
    openai.BadRequestError,
    openai.NotFoundError,
    anthropic.BadRequestError,
    anthropic.NotFoundError,
)
_PROVIDER_STATUS_ERRORS = (openai.APIStatusError, anthropic.APIStatusError)


class ModelProbeError(RuntimeError):
    """A sanitized, stable error returned by the model probe API."""

    def __init__(self, code: str, message: str, status_code: int) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


def _validate_model_reference(model: str) -> str:
    normalized = str(model or "").strip()
    if ":" not in normalized:
        raise ModelProbeError(
            "invalid_model_reference",
            "模型必须使用 provider_id:model_name 格式",
            422,
        )
    provider_id, model_name = normalized.split(":", 1)
    if not provider_id or not model_name:
        raise ModelProbeError(
            "invalid_model_reference",
            "模型必须使用 provider_id:model_name 格式",
            422,
        )
    return normalized


def _probe_model_params(model: str) -> dict[str, Any]:
    capabilities = get_model_manager().get_model_capabilities(model)
    fields = capabilities.get("model_params", {})
    params: dict[str, Any] = {}
    if "thinking_enabled" in fields:
        params["thinking_enabled"] = False
    efforts = capabilities.get("reasoning_efforts", [])
    if "reasoning_effort" in fields and "low" in efforts:
        params["reasoning_effort"] = "low"
    return params


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, list):
        return ""

    parts: list[str] = []
    for block in content:
        if isinstance(block, str):
            parts.append(block)
            continue
        if isinstance(block, dict):
            block_type = block.get("type")
            text = block.get("text")
        else:
            block_type = getattr(block, "type", None)
            text = getattr(block, "text", None)
        if block_type in {"text", "output_text"} and isinstance(text, str):
            parts.append(text)
    return "".join(parts).strip()


def _non_negative_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        normalized = int(value)
    except (TypeError, ValueError):
        return None
    return normalized if normalized >= 0 else None


def _extract_usage(output: Any) -> dict[str, int] | None:
    response_metadata = getattr(output, "response_metadata", None) or {}
    response_usage = (
        response_metadata.get("token_usage", {})
        if isinstance(response_metadata, dict)
        else {}
    )
    usage_metadata = getattr(output, "usage_metadata", None) or {}
    if not isinstance(response_usage, dict):
        response_usage = {}
    if not isinstance(usage_metadata, dict):
        usage_metadata = {}

    sources: dict[str, list[Any]] = {
        "prompt_tokens": [
            response_usage.get("prompt_tokens"),
            usage_metadata.get("input_tokens"),
        ],
        "completion_tokens": [
            response_usage.get("completion_tokens"),
            usage_metadata.get("output_tokens"),
        ],
        "total_tokens": [
            response_usage.get("total_tokens"),
            usage_metadata.get("total_tokens"),
        ],
        "cached_tokens": [
            response_usage.get("prompt_cache_hit_tokens"),
            (response_usage.get("prompt_tokens_details") or {}).get("cached_tokens")
            if isinstance(response_usage.get("prompt_tokens_details"), dict)
            else None,
            (usage_metadata.get("input_token_details") or {}).get("cache_read")
            if isinstance(usage_metadata.get("input_token_details"), dict)
            else None,
        ],
        "reasoning_tokens": [
            response_usage.get("reasoning_tokens"),
            (response_usage.get("completion_tokens_details") or {}).get(
                "reasoning_tokens"
            )
            if isinstance(response_usage.get("completion_tokens_details"), dict)
            else None,
            (usage_metadata.get("output_token_details") or {}).get("reasoning")
            if isinstance(usage_metadata.get("output_token_details"), dict)
            else None,
        ],
    }

    usage: dict[str, int] = {}
    for field, values in sources.items():
        for value in values:
            normalized = _non_negative_int(value)
            if normalized is not None:
                usage[field] = normalized
                break

    if (
        "total_tokens" not in usage
        and "prompt_tokens" in usage
        and "completion_tokens" in usage
    ):
        usage["total_tokens"] = (
            usage["prompt_tokens"] + usage["completion_tokens"]
        )
    return usage or None


def _sanitized_provider_error(error: Exception) -> ModelProbeError:
    if isinstance(error, _AUTH_ERRORS):
        return ModelProbeError(
            "provider_auth_failed",
            "供应商拒绝了当前凭据",
            502,
        )
    if isinstance(error, _RATE_LIMIT_ERRORS):
        return ModelProbeError(
            "provider_rate_limited",
            "供应商当前限流",
            429,
        )
    if isinstance(error, _TIMEOUT_ERRORS):
        return ModelProbeError(
            "provider_timeout",
            "供应商推理请求超时",
            504,
        )
    if isinstance(error, _CONNECTION_ERRORS):
        return ModelProbeError(
            "provider_connection_failed",
            "无法连接供应商推理接口",
            502,
        )
    if isinstance(error, _REJECTED_REQUEST_ERRORS):
        return ModelProbeError(
            "provider_rejected_request",
            "供应商拒绝了推理请求",
            502,
        )
    if isinstance(error, _PROVIDER_STATUS_ERRORS):
        return ModelProbeError(
            "provider_error",
            "供应商推理接口返回错误",
            502,
        )
    return ModelProbeError(
        "provider_error",
        "供应商推理调用失败",
        502,
    )


async def probe_configured_model(
    model: str,
    *,
    timeout_seconds: float = PROBE_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Run exactly one minimal inference against a configured model."""
    model = _validate_model_reference(model)
    try:
        model_params = _probe_model_params(model)
        llm = create_llm(
            model_override=model,
            streaming=False,
            model_params=model_params,
            provider_retries_enabled=False,
            max_tokens=PROBE_MAX_OUTPUT_TOKENS,
            timeout=timeout_seconds,
        )
    except ModelCredentialNotConfiguredError as error:
        raise ModelProbeError(
            "credential_not_configured",
            "所选供应商没有可用凭据",
            422,
        ) from error
    except ModelConfigurationNotAvailableError as error:
        raise ModelProbeError(
            "model_not_configured",
            "所选供应商或模型未配置",
            422,
        ) from error
    except ValueError as error:
        raise ModelProbeError(
            "invalid_model_configuration",
            "所选模型配置无效",
            422,
        ) from error

    started_at = perf_counter()
    try:
        output = await asyncio.wait_for(
            llm.ainvoke(PROBE_PROMPT),
            timeout=timeout_seconds,
        )
    except Exception as error:
        logger.warning(
            "模型探针失败: model=%s error=%s",
            model,
            type(error).__name__,
        )
        raise _sanitized_provider_error(error) from error

    latency_ms = max(0, round((perf_counter() - started_at) * 1000))
    content = _content_text(getattr(output, "content", None))
    if not content:
        raise ModelProbeError(
            "empty_output",
            "供应商返回了空的最终文本",
            502,
        )
    return {
        "ok": True,
        "model": model,
        "content": content,
        "latency_ms": latency_ms,
        "usage": _extract_usage(output),
    }
