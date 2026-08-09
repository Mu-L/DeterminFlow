"""User-safe presentation for model Provider failures."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


PROVIDER_ERROR_CODES = frozenset(
    {
        "quota_exhausted",
        "rate_limited",
        "authentication_failed",
        "service_unavailable",
        "unknown",
    }
)

_DEFAULT_PROVIDER_MESSAGES = {
    "quota_exhausted": "模型额度或余额不足",
    "rate_limited": "模型请求过于频繁，请稍后再试",
    "authentication_failed": "模型服务凭据无效",
    "service_unavailable": "模型服务暂时不可用，请稍后再试",
    "unknown": "模型调用失败，请稍后再试",
}

_QUOTA_CODES = {
    "balance_insufficient",
    "billing_hard_limit_reached",
    "credit_balance_too_low",
    "insufficient_balance",
    "insufficient_quota",
    "insufficient_user_quota",
    "pre_consume_token_quota_failed",
    "public_credential_quota_exceeded",
    "quota_exhausted",
}
_RATE_LIMIT_CODES = {
    "rate_limit_exceeded",
    "rate_limited",
    "too_many_requests",
}
_AUTHENTICATION_CODES = {
    "authentication_error",
    "authentication_failed",
    "invalid_api_key",
    "permission_denied",
    "unauthorized",
}
_SERVICE_CODES = {
    "api_connection_error",
    "internal_server_error",
    "overloaded_error",
    "request_timeout",
    "service_unavailable",
    "timeout",
}

_PROVIDER_EXCEPTION_NAMES = {
    "apiconnectionerror",
    "apistatuserror",
    "apitimeouterror",
    "authenticationerror",
    "badrequesterror",
    "internalservererror",
    "modelconfigurationnotavailableerror",
    "modelcredentialnotconfigurederror",
    "permissiondeniederror",
    "ratelimiterror",
}


@dataclass(frozen=True)
class PresentedError:
    code: str
    message: str


def normalize_provider_error_messages(value: Any) -> dict[str, str]:
    """Validate the optional Provider-owned error presentation mapping."""
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("error_messages must be an object")

    normalized: dict[str, str] = {}
    for raw_code, raw_message in value.items():
        code = str(raw_code or "").strip()
        if code not in PROVIDER_ERROR_CODES:
            raise ValueError(f"Unsupported provider error message code: {raw_code}")
        if not isinstance(raw_message, str):
            raise ValueError(f"Provider error message {code} must be a string")
        message = raw_message.strip()
        if not message or len(message) > 200:
            raise ValueError(
                f"Provider error message {code} must contain 1-200 characters"
            )
        normalized[code] = message
    return normalized


def present_session_error(
    error: Exception,
    provider_config: dict[str, Any] | None = None,
) -> PresentedError:
    """Return a safe error for persistence and user-facing session surfaces."""
    code = classify_provider_error(error)
    if code is None:
        return PresentedError(
            code="session_failed",
            message="会话运行失败，请稍后再试",
        )

    messages: dict[str, str] = {}
    if provider_config:
        try:
            messages = normalize_provider_error_messages(
                provider_config.get("error_messages")
            )
        except ValueError:
            messages = {}
    return PresentedError(
        code=code,
        message=messages.get(code) or _DEFAULT_PROVIDER_MESSAGES[code],
    )


def classify_provider_error(error: Exception) -> str | None:
    """Normalize SDK and relay signals without exposing their raw messages."""
    if not _is_provider_error(error):
        return None

    raw_code = _extract_error_code(error)
    status_code = _extract_status_code(error)
    exception_name = type(error).__name__.lower()
    message = str(error).lower()

    if raw_code in _QUOTA_CODES or _contains_any(
        message,
        (
            "insufficient balance",
            "insufficient quota",
            "public credential quota exceeded",
            "subscription quota insufficient",
            "余额不足",
            "额度不足",
        ),
    ):
        return "quota_exhausted"
    if (
        raw_code in _RATE_LIMIT_CODES
        or exception_name == "ratelimiterror"
        or status_code == 429
        or _contains_any(message, ("rate limit", "too many requests", "请求过于频繁"))
    ):
        return "rate_limited"
    if (
        raw_code in _AUTHENTICATION_CODES
        or exception_name
        in {
            "authenticationerror",
            "modelcredentialnotconfigurederror",
            "permissiondeniederror",
        }
        or status_code in {401, 403}
        or _contains_any(message, ("invalid api key", "authentication failed", "unauthorized"))
    ):
        return "authentication_failed"
    if (
        raw_code in _SERVICE_CODES
        or exception_name
        in {"apiconnectionerror", "apitimeouterror", "internalservererror"}
        or (status_code is not None and status_code >= 500)
        or _contains_any(
            message,
            ("service unavailable", "connection error", "timed out", "timeout"),
        )
    ):
        return "service_unavailable"
    return "unknown"


def _is_provider_error(error: Exception) -> bool:
    if getattr(error, "llm_provider_usage_status", None):
        return True
    exception_type = type(error)
    module = exception_type.__module__.lower()
    name = exception_type.__name__.lower()
    return (
        module.startswith(
            ("openai", "anthropic", "langchain_openai", "langchain_anthropic")
        )
        or module == "src.core.llm_client"
        or name in _PROVIDER_EXCEPTION_NAMES
    )


def _extract_error_code(error: Exception) -> str:
    candidates: list[Any] = [getattr(error, "code", None)]
    for body in _error_bodies(error):
        candidates.append(body.get("code"))
        nested = body.get("error")
        if isinstance(nested, dict):
            candidates.append(nested.get("code"))
    for candidate in candidates:
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip().lower().replace("-", "_").replace(" ", "_")
    return ""


def _extract_status_code(error: Exception) -> int | None:
    candidates = [
        getattr(error, "status_code", None),
        getattr(getattr(error, "response", None), "status_code", None),
    ]
    for body in _error_bodies(error):
        candidates.append(body.get("status"))
        nested = body.get("error")
        if isinstance(nested, dict):
            candidates.append(nested.get("status"))
    for candidate in candidates:
        if isinstance(candidate, int) and not isinstance(candidate, bool):
            return candidate
    return None


def _error_bodies(error: Exception) -> list[dict[str, Any]]:
    bodies: list[dict[str, Any]] = []
    body = getattr(error, "body", None)
    if isinstance(body, dict):
        bodies.append(body)
    response = getattr(error, "response", None)
    if response is not None:
        try:
            response_body = response.json()
        except (AttributeError, TypeError, ValueError):
            response_body = None
        if isinstance(response_body, dict):
            bodies.append(response_body)
    return bodies


def _contains_any(value: str, needles: tuple[str, ...]) -> bool:
    return any(needle in value for needle in needles)
