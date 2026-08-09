import asyncio
import json
from uuid import uuid4

import pytest
from langchain_anthropic import ChatAnthropic
from langchain_core.runnables.config import var_child_runnable_config

from src.core.llm_client import _merge_params, _wrap_llm_with_retry, create_llm
from src.core.model_manager import ModelManager
from src.core.provider_adapters import build_provider_request


class FakeModelManager:
    def get_default_params(self):
        return {
            "temperature": 0.7,
            "response_format": {"type": "json_object"},
        }

    def build_provider_request(
        self, _provider_id, params, provider, *, model_name=None
    ):
        return build_provider_request("gpt", params, provider)


def test_explicit_null_response_format_disables_global_json_mode():
    kwargs = {}

    merged = _merge_params(
        FakeModelManager(),
        "openai",
        "gpt-test",
        {"hyperparameter_values": {}},
        {"response_format": None},
        kwargs,
    )

    assert merged["response_format"] is None
    assert "model_kwargs" not in kwargs


def test_missing_response_format_still_inherits_global_default():
    kwargs = {}

    merged = _merge_params(
        FakeModelManager(),
        "openai",
        "gpt-test",
        {"hyperparameter_values": {}},
        {"temperature": 0.2},
        kwargs,
    )

    assert merged["response_format"] == {"type": "json_object"}
    assert kwargs["model_kwargs"]["response_format"] == {"type": "json_object"}


def test_stream_chunk_timeout_is_forwarded_from_agent_params():
    kwargs = {}

    merged = _merge_params(
        FakeModelManager(),
        "openai",
        "gpt-test",
        {"hyperparameter_values": {}},
        {"stream_chunk_timeout": 300},
        kwargs,
    )

    assert merged["stream_chunk_timeout"] == 300
    assert kwargs["stream_chunk_timeout"] == 300.0


@pytest.mark.parametrize("timeout", [0, -1, True, float("nan"), float("inf")])
def test_stream_chunk_timeout_rejects_non_positive_or_non_finite_values(timeout):
    with pytest.raises(ValueError, match="finite positive number"):
        _merge_params(
            FakeModelManager(),
            "openai",
            "gpt-test",
            {"hyperparameter_values": {}},
            {"stream_chunk_timeout": timeout},
            {},
        )


def test_anthropic_adapter_claims_and_maps_only_supported_fields():
    request = build_provider_request(
        "anthropic",
        {
            "thinking_enabled": True,
            "reasoning_effort": "medium",
            "thinking_budget": 2048,
            "temperature": 0.2,
            "top_p": 0.5,
            "presence_penalty": 1,
            "response_format": {"type": "json_object"},
            "stream_chunk_timeout": 300,
        },
        {"hyperparameter_values": {"max_completion_tokens": 8192}},
    )

    assert request == {
        "client_kwargs": {
            "max_tokens": 8192,
            "thinking": {"type": "enabled", "budget_tokens": 2048},
            "output_config": {"effort": "medium"},
        },
        "extra_body": {},
    }


def test_anthropic_adapter_uses_adaptive_thinking_without_budget():
    request = build_provider_request(
        "anthropic",
        {
            "thinking_enabled": True,
            "thinking_budget": None,
            "reasoning_effort": "high",
        },
        {"hyperparameter_values": {}},
    )

    assert request["client_kwargs"] == {
        "max_tokens": 32768,
        "thinking": {"type": "adaptive"},
        "output_config": {"effort": "high"},
    }


def test_anthropic_adapter_validates_manual_thinking_budget():
    with pytest.raises(ValueError, match="at least 1024"):
        build_provider_request(
            "anthropic",
            {"thinking_enabled": True, "thinking_budget": 512},
            {"hyperparameter_values": {"max_completion_tokens": 8192}},
        )


def test_openai_compatible_adapter_does_not_claim_vendor_specific_fields():
    request = build_provider_request(
        "openai_compatible",
        {
            "thinking_enabled": True,
            "reasoning_effort": "high",
            "thinking_budget": 4096,
            "temperature": 0.2,
            "response_format": {"type": "json_object"},
        },
        {
            "hyperparameter_values": {
                "max_completion_tokens": 8192,
                "frequency_penalty": 0.3,
            }
        },
    )

    assert request == {
        "client_kwargs": {
            "temperature": 0.2,
            "max_tokens": 8192,
            "frequency_penalty": 0.3,
        },
        "extra_body": {},
        "model_kwargs": {"response_format": {"type": "json_object"}},
    }


def test_create_llm_selects_native_anthropic_client(tmp_path, monkeypatch):
    config_path = tmp_path / "models_config.json"
    config_path.write_text(
        json.dumps({
            "default_params": {
                "thinking_enabled": True,
                "reasoning_effort": "high",
                "temperature": 0.7,
                "top_p": 1,
                "presence_penalty": 0,
                "thinking_budget": None,
            },
            "retry": {"max_retries": 0, "delays": []},
            "providers": {
                "anthropic": {
                    "name": "Anthropic",
                    "category": "anthropic",
                    "api_format": "anthropic",
                    "base_url": "https://api.anthropic.com/v1",
                    "api_key": "test-key",
                    "models": ["claude-sonnet-4-6"],
                    "hyperparameter_values": {"max_completion_tokens": 8192},
                }
            },
        }),
        encoding="utf-8",
    )
    manager = ModelManager(str(config_path))
    monkeypatch.setattr("src.core.model_manager.get_model_manager", lambda: manager)

    llm = create_llm(
        model_override="anthropic:claude-sonnet-4-6",
        model_params={
            "thinking_enabled": True,
            "reasoning_effort": "medium",
            "thinking_budget": 2048,
            "temperature": 0.2,
            "response_format": {"type": "json_object"},
        },
    )

    assert isinstance(llm, ChatAnthropic)
    assert llm.thinking == {"type": "enabled", "budget_tokens": 2048}
    assert llm.output_config == {"effort": "medium"}
    assert llm.max_tokens == 8192
    assert llm.temperature is None
    assert llm.model_kwargs == {}
    assert str(llm._client.base_url) == "https://api.anthropic.com"


def test_create_llm_uses_selected_openai_adapter_on_relay(tmp_path, monkeypatch):
    config_path = tmp_path / "models_config.json"
    config_path.write_text(
        json.dumps({
            "default_params": {
                "reasoning_effort": "high",
                "temperature": 0.7,
                "top_p": 1,
                "presence_penalty": 0,
            },
            "retry": {"max_retries": 0, "delays": []},
            "providers": {
                "determinflow-public": {
                    "name": "DeterminFlow 公益模型",
                    "provider_type": "openai_compatible",
                    "base_url": "https://relay.example.test/v1",
                    "api_key": "test-key",
                    "models": ["gpt-5.6-luna"],
                    "models_config": {
                        "gpt-5.6-luna": {
                            "provider_type": "openai",
                        }
                    },
                    "hyperparameter_values": {},
                }
            },
        }),
        encoding="utf-8",
    )
    manager = ModelManager(str(config_path))
    monkeypatch.setattr("src.core.model_manager.get_model_manager", lambda: manager)

    llm = create_llm(
        model_override="determinflow-public:gpt-5.6-luna",
        model_params={"reasoning_effort": "medium"},
    )

    assert llm.use_responses_api is True
    assert llm.reasoning_effort == "medium"
    assert str(llm.root_client.base_url) == "https://relay.example.test/v1/"


class FakeStreamingLlm:
    def __init__(self, *, fail_after_chunk: bool):
        self.fail_after_chunk = fail_after_chunk
        self.astream_calls = 0

    async def ainvoke(self, _input, *args, **kwargs):
        return None

    async def astream(self, _input, *args, **kwargs):
        self.astream_calls += 1
        if self.fail_after_chunk:
            yield "partial"
            raise RuntimeError("stream interrupted")
        if self.astream_calls == 1:
            raise RuntimeError("failed before first chunk")
        yield "complete"


def test_stream_retry_does_not_duplicate_partial_response():
    async def collect():
        llm = FakeStreamingLlm(fail_after_chunk=True)
        wrapped = _wrap_llm_with_retry(
            llm, {"max_retries": 2, "delays": [0, 0]}
        )
        chunks = []
        with pytest.raises(RuntimeError, match="stream interrupted"):
            async for chunk in wrapped.astream("input"):
                chunks.append(chunk)
        return llm.astream_calls, chunks

    calls, chunks = asyncio.run(collect())
    assert calls == 1
    assert chunks == ["partial"]


def test_stream_retry_is_safe_before_first_chunk():
    async def collect():
        llm = FakeStreamingLlm(fail_after_chunk=False)
        wrapped = _wrap_llm_with_retry(
            llm, {"max_retries": 1, "delays": [0]}
        )
        chunks = [chunk async for chunk in wrapped.astream("input")]
        return llm.astream_calls, chunks

    calls, chunks = asyncio.run(collect())
    assert calls == 2
    assert chunks == ["complete"]


class RecordingTokenCallback:
    def __init__(self):
        self.tokens = []

    def on_llm_new_token(self, token, **_kwargs):
        self.tokens.append(token)


class FakeAinvokeStreamingLlm:
    def __init__(self, *, fail_after_chunk: bool):
        self.fail_after_chunk = fail_after_chunk
        self.ainvoke_calls = 0

    async def ainvoke(self, _input, *args, **kwargs):
        self.ainvoke_calls += 1
        if self.fail_after_chunk or self.ainvoke_calls > 1:
            config = args[0] if args else kwargs.get("config", {})
            for callback in config.get("callbacks", []):
                callback.on_llm_new_token(
                    "partial" if self.fail_after_chunk else "complete",
                    run_id=uuid4(),
                )
        if self.fail_after_chunk or self.ainvoke_calls == 1:
            raise RuntimeError("ainvoke stream interrupted")
        return "complete"

    async def astream(self, _input, *args, **kwargs):
        yield "unused"


def test_ainvoke_retry_stops_after_stream_callback_emits_first_chunk(caplog):
    async def invoke():
        llm = FakeAinvokeStreamingLlm(fail_after_chunk=True)
        callback = RecordingTokenCallback()
        wrapped = _wrap_llm_with_retry(
            llm, {"max_retries": 2, "delays": [0, 0]}
        )
        with pytest.raises(RuntimeError, match="ainvoke stream interrupted") as exc:
            await wrapped.ainvoke(
                "input",
                config={"callbacks": [callback]},
            )
        return llm.ainvoke_calls, callback.tokens, exc.value

    calls, tokens, error = asyncio.run(invoke())

    assert calls == 1
    assert tokens == ["partial"]
    assert error.llm_partial_stream_emitted is True
    assert error.llm_provider_usage_status == "unavailable_on_failed_attempt"
    assert "provider_usage_status=unavailable_on_failed_attempt" in caplog.text


def test_ainvoke_retry_remains_safe_before_first_stream_callback(caplog):
    async def invoke():
        llm = FakeAinvokeStreamingLlm(fail_after_chunk=False)
        callback = RecordingTokenCallback()
        wrapped = _wrap_llm_with_retry(
            llm, {"max_retries": 1, "delays": [0]}
        )
        output = await wrapped.ainvoke(
            "input",
            config={"callbacks": [callback]},
        )
        return llm.ainvoke_calls, callback.tokens, output

    calls, tokens, output = asyncio.run(invoke())

    assert calls == 2
    assert tokens == ["complete"]
    assert output == "complete"
    assert "provider_usage_status=unavailable_on_failed_attempt" in caplog.text


def test_ainvoke_retry_preserves_implicit_parent_callbacks():
    async def invoke():
        llm = FakeAinvokeStreamingLlm(fail_after_chunk=True)
        callback = RecordingTokenCallback()
        wrapped = _wrap_llm_with_retry(
            llm, {"max_retries": 1, "delays": [0]}
        )
        context_token = var_child_runnable_config.set(
            {"callbacks": [callback]}
        )
        try:
            with pytest.raises(RuntimeError, match="ainvoke stream interrupted"):
                await wrapped.ainvoke("input")
        finally:
            var_child_runnable_config.reset(context_token)
        return llm.ainvoke_calls, callback.tokens

    calls, tokens = asyncio.run(invoke())

    assert calls == 1
    assert tokens == ["partial"]
