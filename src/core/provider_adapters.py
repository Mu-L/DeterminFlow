"""Provider-specific model parameter declarations and request mappings."""

from __future__ import annotations

import math
from copy import deepcopy
from typing import Any, Callable


MODEL_PARAM_FIELDS: dict[str, dict[str, Any]] = {
    "thinking_enabled": {
        "type": "boolean",
        "label": "思考模式",
        "default": True,
    },
    "reasoning_effort": {
        "type": "select",
        "label": "思考力度",
        "default": "high",
        "options": ["low", "medium", "high", "max"],
        "option_labels": {
            "low": "低",
            "medium": "中",
            "high": "高",
            "xhigh": "极高",
            "max": "最高",
        },
    },
    "thinking_budget": {
        "type": "number",
        "label": "思考 Token 上限",
        "default": None,
        "nullable": True,
        "min": 1,
        "max": 131072,
        "step": 100,
    },
    "temperature": {
        "type": "range",
        "label": "温度",
        "default": 0.7,
        "min": 0,
        "max": 2,
        "step": 0.1,
    },
    "top_p": {
        "type": "range",
        "label": "Top P",
        "default": 1.0,
        "min": 0,
        "max": 1,
        "step": 0.05,
    },
    "presence_penalty": {
        "type": "range",
        "label": "存在惩罚",
        "default": 0.0,
        "min": -2,
        "max": 2,
        "step": 0.1,
    },
    "response_format": {
        "type": "json_mode",
        "label": "JSON 输出模式",
        "default": None,
    },
    "stream_chunk_timeout": {
        "type": "number",
        "label": "流式分块超时（秒）",
        "default": None,
        "nullable": True,
        "min": 1,
        "step": 1,
    },
}


PROVIDER_TYPE_ALIASES = {
    "ds": "deepseek",
    "gpt": "openai",
}


def _build_deepseek_params(params: dict[str, Any], _provider: dict) -> dict:
    thinking_enabled = params.get("thinking_enabled", True)
    extra_body: dict[str, Any] = {
        "thinking": {"type": "enabled" if thinking_enabled else "disabled"}
    }
    if thinking_enabled:
        extra_body["reasoning_effort"] = params.get("reasoning_effort", "high")
    return {
        "client_kwargs": _pick(params, "temperature", "top_p", "presence_penalty"),
        "extra_body": extra_body,
    }


def _build_qwen_params(params: dict[str, Any], _provider: dict) -> dict:
    extra_body: dict[str, Any] = {}
    if params.get("thinking_enabled") is not None:
        extra_body["enable_thinking"] = params["thinking_enabled"]
    if params.get("thinking_budget") is not None:
        extra_body["thinking_budget"] = params["thinking_budget"]
    return {
        "client_kwargs": _pick(params, "temperature", "top_p", "presence_penalty"),
        "extra_body": extra_body,
    }


def _build_openai_params(params: dict[str, Any], _provider: dict) -> dict:
    return {
        "client_kwargs": {
            **_pick(params, "temperature", "top_p", "presence_penalty"),
            "reasoning_effort": params.get("reasoning_effort", "medium"),
        },
        "extra_body": {},
    }


def _build_openai_compatible_params(
    params: dict[str, Any], _provider: dict
) -> dict:
    return {
        "client_kwargs": _pick(
            params, "temperature", "top_p", "presence_penalty"
        ),
        "extra_body": {},
    }


def _build_anthropic_params(params: dict[str, Any], provider: dict) -> dict:
    client_kwargs: dict[str, Any] = {}
    hyperparams = provider.get("hyperparameter_values", {})
    max_tokens = hyperparams.get("max_completion_tokens", 32768)
    if max_tokens is not None:
        client_kwargs["max_tokens"] = max_tokens

    thinking_enabled = params.get("thinking_enabled")
    if thinking_enabled is False:
        client_kwargs["thinking"] = {"type": "disabled"}
    elif thinking_enabled is True:
        budget = params.get("thinking_budget")
        if budget is None:
            client_kwargs["thinking"] = {"type": "adaptive"}
        else:
            if isinstance(budget, bool) or not isinstance(budget, (int, float)):
                raise ValueError("thinking_budget must be a number or null")
            budget = int(budget)
            if budget < 1024:
                raise ValueError("Anthropic thinking_budget must be at least 1024")
            if max_tokens is not None and budget >= int(max_tokens):
                raise ValueError("Anthropic thinking_budget must be less than max_tokens")
            client_kwargs["thinking"] = {
                "type": "enabled",
                "budget_tokens": budget,
            }

    effort = params.get("reasoning_effort")
    allowed_efforts = {"low", "medium", "high", "xhigh", "max"}
    if effort is not None:
        if effort not in allowed_efforts:
            raise ValueError(f"Unsupported Anthropic reasoning_effort: {effort}")
        client_kwargs["output_config"] = {"effort": effort}

    return {"client_kwargs": client_kwargs, "extra_body": {}}


def _pick(params: dict[str, Any], *names: str) -> dict[str, Any]:
    return {name: params[name] for name in names if params.get(name) is not None}


def _field_schema(
    names: tuple[str, ...],
    *,
    overrides: dict[str, dict[str, Any]] | None = None,
) -> dict[str, dict[str, Any]]:
    fields = {name: deepcopy(MODEL_PARAM_FIELDS[name]) for name in names}
    for name, values in (overrides or {}).items():
        fields[name].update(values)
    return fields


_OPENAI_COMMON_FIELDS = (
    "temperature",
    "top_p",
    "presence_penalty",
    "response_format",
    "stream_chunk_timeout",
)


PROVIDER_ADAPTERS: dict[str, dict[str, Any]] = {
    "openai_compatible": {
        "api_format": "openai",
        "model_params": _field_schema(_OPENAI_COMMON_FIELDS),
        "build_request": _build_openai_compatible_params,
    },
    "deepseek": {
        "api_format": "openai",
        "model_params": _field_schema(
            ("thinking_enabled", "reasoning_effort", *_OPENAI_COMMON_FIELDS)
        ),
        "build_request": _build_deepseek_params,
    },
    "mimo": {
        "api_format": "openai",
        "model_params": _field_schema(
            ("thinking_enabled", "reasoning_effort", *_OPENAI_COMMON_FIELDS)
        ),
        "build_request": _build_deepseek_params,
    },
    "qwen": {
        "api_format": "openai",
        "model_params": _field_schema(
            ("thinking_enabled", "thinking_budget", *_OPENAI_COMMON_FIELDS)
        ),
        "build_request": _build_qwen_params,
    },
    "openai": {
        "api_format": "openai_responses",
        "model_params": _field_schema(
            ("reasoning_effort", *_OPENAI_COMMON_FIELDS),
            overrides={
                "reasoning_effort": {
                    "options": ["low", "medium", "high", "xhigh"],
                    "default": "medium",
                }
            },
        ),
        "build_request": _build_openai_params,
    },
    "anthropic": {
        "api_format": "anthropic",
        "model_params": _field_schema(
            ("thinking_enabled", "reasoning_effort", "thinking_budget"),
            overrides={
                "reasoning_effort": {
                    "options": ["low", "medium", "high", "xhigh", "max"]
                },
                "thinking_budget": {"min": 1024},
            },
        ),
        "build_request": _build_anthropic_params,
    },
}


def normalize_provider_type(provider_type: str | None) -> str | None:
    """Return the canonical Provider type, including legacy aliases."""
    if not isinstance(provider_type, str):
        return None
    value = provider_type.strip().lower()
    if not value:
        return None
    value = PROVIDER_TYPE_ALIASES.get(value, value)
    return value if value in PROVIDER_ADAPTERS else None


def get_provider_adapter(provider_type: str | None) -> dict[str, Any] | None:
    """Return an adapter declaration without exposing the mutable registry."""
    adapter = PROVIDER_ADAPTERS.get(normalize_provider_type(provider_type) or "")
    return deepcopy(adapter) if adapter else None


def get_model_param_schema(provider_type: str | None) -> dict[str, dict[str, Any]]:
    adapter = PROVIDER_ADAPTERS.get(normalize_provider_type(provider_type) or "")
    return deepcopy(adapter.get("model_params", {})) if adapter else {}


def get_api_format(provider_type: str | None) -> str:
    adapter = PROVIDER_ADAPTERS.get(normalize_provider_type(provider_type) or "")
    return str(adapter.get("api_format", "openai")) if adapter else "openai"


def get_client_base_url(provider_type: str | None, base_url: str) -> str:
    """Translate the stored API root into the base URL expected by the SDK."""
    normalized = base_url.rstrip("/")
    if get_api_format(provider_type) == "anthropic" and normalized.endswith("/v1"):
        return normalized[:-3]
    return normalized


def build_provider_request(
    provider_type: str | None,
    params: dict[str, Any],
    provider: dict[str, Any],
) -> dict[str, Any]:
    adapter = PROVIDER_ADAPTERS.get(
        normalize_provider_type(provider_type) or ""
    )
    if not adapter:
        return _finalize_openai_request(
            {"client_kwargs": {}, "extra_body": {}}, params, provider
        )
    builder: Callable[[dict[str, Any], dict], dict] = adapter["build_request"]
    request = builder(params, provider)
    if adapter["api_format"] in {"openai", "openai_responses"}:
        return _finalize_openai_request(request, params, provider)
    return request


def _finalize_openai_request(
    request: dict[str, Any],
    params: dict[str, Any],
    provider: dict[str, Any],
) -> dict[str, Any]:
    client_kwargs = request.setdefault("client_kwargs", {})
    hyperparams = provider.get("hyperparameter_values", {})
    if "max_completion_tokens" in hyperparams:
        client_kwargs.setdefault("max_tokens", hyperparams["max_completion_tokens"])
    if "frequency_penalty" in hyperparams:
        client_kwargs.setdefault("frequency_penalty", hyperparams["frequency_penalty"])
    if "presence_penalty" in hyperparams:
        client_kwargs.setdefault("presence_penalty", hyperparams["presence_penalty"])

    if params.get("response_format"):
        request.setdefault("model_kwargs", {})["response_format"] = params[
            "response_format"
        ]

    timeout = params.get("stream_chunk_timeout")
    if timeout is not None:
        if isinstance(timeout, bool):
            raise ValueError("stream_chunk_timeout must be a finite positive number")
        try:
            timeout = float(timeout)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "stream_chunk_timeout must be a finite positive number"
            ) from exc
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("stream_chunk_timeout must be a finite positive number")
        client_kwargs["stream_chunk_timeout"] = timeout

    return request
