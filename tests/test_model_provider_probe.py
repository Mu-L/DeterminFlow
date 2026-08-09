from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.core import llm_client as llm_client_module
from src.core import model_probe
from src.core.llm_client import ModelConfigurationNotAvailableError
from src.core.model_manager import ModelManager
from src.core.model_probe import ModelProbeError
from src.web.api_routes import router


class _ProbeModelManager:
    def get_model_capabilities(self, model: str) -> dict:
        assert model == "provider-a:model-a"
        return {
            "model_params": {
                "thinking_enabled": {},
                "reasoning_effort": {},
            },
            "reasoning_efforts": ["low", "medium", "high"],
        }


class _FakeProbeLlm:
    def __init__(self, output) -> None:
        self.output = output
        self.prompts: list[str] = []

    async def ainvoke(self, prompt: str):
        self.prompts.append(prompt)
        if isinstance(self.output, Exception):
            raise self.output
        return self.output


def test_probe_runs_one_minimal_inference_without_retries(monkeypatch) -> None:
    output = SimpleNamespace(
        content=[{"type": "text", "text": "OK"}],
        response_metadata={
            "token_usage": {
                "prompt_tokens": 7,
                "completion_tokens": 2,
                "total_tokens": 9,
                "completion_tokens_details": {"reasoning_tokens": 1},
            }
        },
        usage_metadata=None,
    )
    llm = _FakeProbeLlm(output)
    captured = {}

    def fake_create_llm(**kwargs):
        captured.update(kwargs)
        return llm

    monkeypatch.setattr(model_probe, "get_model_manager", _ProbeModelManager)
    monkeypatch.setattr(model_probe, "create_llm", fake_create_llm)

    result = asyncio.run(model_probe.probe_configured_model("provider-a:model-a"))

    assert llm.prompts == [model_probe.PROBE_PROMPT]
    assert captured == {
        "model_override": "provider-a:model-a",
        "streaming": False,
        "model_params": {
            "thinking_enabled": False,
            "reasoning_effort": "low",
        },
        "provider_retries_enabled": False,
        "max_tokens": model_probe.PROBE_MAX_OUTPUT_TOKENS,
        "timeout": model_probe.PROBE_TIMEOUT_SECONDS,
    }
    assert result["ok"] is True
    assert result["model"] == "provider-a:model-a"
    assert result["content"] == "OK"
    assert result["latency_ms"] >= 0
    assert result["usage"] == {
        "prompt_tokens": 7,
        "completion_tokens": 2,
        "total_tokens": 9,
        "reasoning_tokens": 1,
    }


def test_probe_uses_usage_metadata_and_derives_total(monkeypatch) -> None:
    output = SimpleNamespace(
        content="OK",
        response_metadata={},
        usage_metadata={
            "input_tokens": 5,
            "output_tokens": 1,
            "input_token_details": {"cache_read": 2},
        },
    )
    monkeypatch.setattr(model_probe, "get_model_manager", _ProbeModelManager)
    monkeypatch.setattr(
        model_probe,
        "create_llm",
        lambda **_kwargs: _FakeProbeLlm(output),
    )

    result = asyncio.run(model_probe.probe_configured_model("provider-a:model-a"))

    assert result["usage"] == {
        "prompt_tokens": 5,
        "completion_tokens": 1,
        "total_tokens": 6,
        "cached_tokens": 2,
    }


def test_probe_rejects_reasoning_only_empty_final_content(monkeypatch) -> None:
    output = SimpleNamespace(
        content="",
        additional_kwargs={"reasoning_content": "thinking"},
        response_metadata={},
        usage_metadata={},
    )
    monkeypatch.setattr(model_probe, "get_model_manager", _ProbeModelManager)
    monkeypatch.setattr(
        model_probe,
        "create_llm",
        lambda **_kwargs: _FakeProbeLlm(output),
    )

    with pytest.raises(ModelProbeError) as exc_info:
        asyncio.run(model_probe.probe_configured_model("provider-a:model-a"))

    assert exc_info.value.code == "empty_output"
    assert exc_info.value.status_code == 502


def test_probe_timeout_is_single_attempt_and_sanitized(monkeypatch) -> None:
    class SlowLlm:
        calls = 0

        async def ainvoke(self, _prompt: str):
            self.calls += 1
            await asyncio.sleep(1)

    llm = SlowLlm()
    monkeypatch.setattr(model_probe, "get_model_manager", _ProbeModelManager)
    monkeypatch.setattr(model_probe, "create_llm", lambda **_kwargs: llm)

    with pytest.raises(ModelProbeError) as exc_info:
        asyncio.run(
            model_probe.probe_configured_model(
                "provider-a:model-a",
                timeout_seconds=0.001,
            )
        )

    assert llm.calls == 1
    assert exc_info.value.code == "provider_timeout"
    assert exc_info.value.status_code == 504


def test_probe_does_not_expose_raw_provider_error(monkeypatch) -> None:
    monkeypatch.setattr(model_probe, "get_model_manager", _ProbeModelManager)
    monkeypatch.setattr(
        model_probe,
        "create_llm",
        lambda **_kwargs: _FakeProbeLlm(
            RuntimeError("api_key=should-not-leak https://secret.example.test")
        ),
    )

    with pytest.raises(ModelProbeError) as exc_info:
        asyncio.run(model_probe.probe_configured_model("provider-a:model-a"))

    assert exc_info.value.code == "provider_error"
    assert "should-not-leak" not in exc_info.value.message
    assert "secret.example.test" not in exc_info.value.message


def test_probe_maps_missing_config_without_exposing_internal_detail(monkeypatch) -> None:
    monkeypatch.setattr(model_probe, "get_model_manager", _ProbeModelManager)

    def unavailable(**_kwargs):
        raise ModelConfigurationNotAvailableError("private provider detail")

    monkeypatch.setattr(model_probe, "create_llm", unavailable)

    with pytest.raises(ModelProbeError) as exc_info:
        asyncio.run(model_probe.probe_configured_model("provider-a:model-a"))

    assert exc_info.value.code == "model_not_configured"
    assert "private provider detail" not in exc_info.value.message


@pytest.mark.parametrize("model", ["model-a", ":model-a", "provider-a:"])
def test_probe_requires_canonical_model_reference(model: str) -> None:
    with pytest.raises(ModelProbeError) as exc_info:
        asyncio.run(model_probe.probe_configured_model(model))

    assert exc_info.value.code == "invalid_model_reference"
    assert exc_info.value.status_code == 422


def test_probe_api_preserves_stable_error_shape(monkeypatch) -> None:
    async def fail_probe(_model: str):
        raise ModelProbeError("provider_timeout", "供应商推理请求超时", 504)

    monkeypatch.setattr(model_probe, "probe_configured_model", fail_probe)
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    response = client.post(
        "/api/model-providers/models/probe",
        json={"model": "provider-a:model-a"},
    )

    assert response.status_code == 504
    assert response.json() == {
        "detail": {
            "code": "provider_timeout",
            "message": "供应商推理请求超时",
        }
    }


def test_probe_api_rejects_provider_credentials_in_request() -> None:
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    response = client.post(
        "/api/model-providers/models/probe",
        json={
            "model": "provider-a:model-a",
            "base_url": "https://untrusted.example.test/v1",
            "api_key": "must-not-be-accepted",
        },
    )

    assert response.status_code == 422


def test_create_llm_disables_sdk_and_core_retries(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config_path = tmp_path / "models_config.json"
    config_path.write_text(
        json.dumps({
            "default_params": {},
            "retry": {"max_retries": 3, "delays": [1, 2, 3]},
            "providers": {
                "provider-a": {
                    "name": "Provider A",
                    "provider_type": "openai_compatible",
                    "base_url": "https://provider.example.test/v1",
                    "api_key": "test-key",
                    "models": ["model-a"],
                    "hyperparameter_values": {},
                }
            },
        }),
        encoding="utf-8",
    )
    manager = ModelManager(str(config_path))
    captured = {}

    class FakeChatOpenAI:
        def __init__(self, **kwargs) -> None:
            captured.update(kwargs)

    def unexpected_retry_wrapper(*_args, **_kwargs):
        raise AssertionError("Core retry wrapper must not be installed")

    monkeypatch.setattr(
        "src.core.model_manager.get_model_manager",
        lambda: manager,
    )
    monkeypatch.setattr(llm_client_module, "ChatOpenAI", FakeChatOpenAI)
    monkeypatch.setattr(
        llm_client_module,
        "_wrap_llm_with_retry",
        unexpected_retry_wrapper,
    )

    llm_client_module.create_llm(
        model_override="provider-a:model-a",
        streaming=False,
        provider_retries_enabled=False,
    )

    assert captured["max_retries"] == 0
