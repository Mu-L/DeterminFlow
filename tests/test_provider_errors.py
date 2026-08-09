from __future__ import annotations

import pytest

from src.core.provider_errors import (
    normalize_provider_error_messages,
    present_session_error,
)
from src.core.llm_client import ModelCredentialNotConfiguredError


class _ProviderFailure(Exception):
    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.body = {"error": {"code": code}} if code else {}
        self.status_code = status_code
        self.llm_provider_usage_status = "unavailable_on_failed_attempt"


@pytest.mark.parametrize(
    ("error", "expected_code"),
    [
        (
            _ProviderFailure(
                "relay rejected secret-key-123",
                code="insufficient_user_quota",
                status_code=403,
            ),
            "quota_exhausted",
        ),
        (
            _ProviderFailure(
                "public credential quota exceeded",
                code="pre_consume_token_quota_failed",
                status_code=403,
            ),
            "quota_exhausted",
        ),
        (_ProviderFailure("limited", status_code=429), "rate_limited"),
        (_ProviderFailure("unauthorized", status_code=401), "authentication_failed"),
        (_ProviderFailure("upstream failed", status_code=503), "service_unavailable"),
        (_ProviderFailure("unrecognized provider failure"), "unknown"),
    ],
)
def test_provider_failures_are_classified_without_exposing_raw_messages(
    error: Exception,
    expected_code: str,
) -> None:
    provider = {
        "error_messages": {
            expected_code: f"display:{expected_code}",
        }
    }

    presented = present_session_error(error, provider)

    assert presented.code == expected_code
    assert presented.message == f"display:{expected_code}"
    assert "secret-key-123" not in presented.message


def test_non_provider_failure_uses_generic_session_message() -> None:
    presented = present_session_error(RuntimeError("private runtime detail"))

    assert presented.code == "session_failed"
    assert presented.message == "会话运行失败，请稍后再试"


def test_missing_provider_credential_uses_authentication_copy() -> None:
    presented = present_session_error(
        ModelCredentialNotConfiguredError("private provider id"),
        {"error_messages": {"authentication_failed": "公益模型授权已失效"}},
    )

    assert presented.code == "authentication_failed"
    assert presented.message == "公益模型授权已失效"


def test_invalid_provider_mapping_falls_back_to_generic_provider_copy() -> None:
    error = _ProviderFailure("limited", status_code=429)

    presented = present_session_error(
        error,
        {"error_messages": {"unexpected": "should not render"}},
    )

    assert presented.code == "rate_limited"
    assert presented.message == "模型请求过于频繁，请稍后再试"


def test_provider_error_message_mapping_is_trimmed_and_validated() -> None:
    assert normalize_provider_error_messages(
        {"quota_exhausted": "  公益模型额度已用完  "}
    ) == {"quota_exhausted": "公益模型额度已用完"}

    with pytest.raises(ValueError, match="Unsupported provider error message code"):
        normalize_provider_error_messages({"unsupported": "message"})
    with pytest.raises(ValueError, match="1-200 characters"):
        normalize_provider_error_messages({"unknown": " "})
    with pytest.raises(ValueError, match="1-200 characters"):
        normalize_provider_error_messages({"unknown": "x" * 201})
