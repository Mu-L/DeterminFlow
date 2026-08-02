import asyncio
from uuid import uuid4

import pytest
from langchain_core.runnables.config import var_child_runnable_config

from src.core.llm_client import _merge_params, _wrap_llm_with_retry


class FakeModelManager:
    def get_default_params(self):
        return {
            "temperature": 0.7,
            "response_format": {"type": "json_object"},
        }

    def get_category_params_config(self, _provider_id):
        return {"chat_openai_params": ["temperature"]}

    def build_extra_body(self, _provider_id, _params):
        return {}


def test_explicit_null_response_format_disables_global_json_mode():
    kwargs = {}

    merged = _merge_params(
        FakeModelManager(),
        "openai",
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
            {"hyperparameter_values": {}},
            {"stream_chunk_timeout": timeout},
            {},
        )


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
