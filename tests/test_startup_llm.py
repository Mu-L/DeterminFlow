from __future__ import annotations

import asyncio

import pytest

from src.core import llm_client


class _FakeLLM:
    def __init__(self) -> None:
        self.bound_tools = None
        self.bind_options = None

    def bind_tools(self, tools, **kwargs):
        self.bound_tools = tools
        self.bind_options = kwargs
        return self

    async def ainvoke(self, value, **_kwargs):
        return f"ready:{value}"


def test_startup_llm_defers_missing_api_key_until_first_model_call(monkeypatch) -> None:
    fake_llm = _FakeLLM()
    attempts = 0

    def fake_create_llm(**_kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise llm_client.ModelCredentialNotConfiguredError("missing test key")
        return fake_llm

    monkeypatch.setattr(llm_client, "create_llm", fake_create_llm)

    startup_llm = llm_client.create_startup_llm(streaming=True)
    bound = startup_llm.bind_tools(["tool-a"], strict=True)
    result = asyncio.run(bound.ainvoke("hello"))

    assert result == "ready:hello"
    assert attempts == 2
    assert fake_llm.bound_tools == ["tool-a"]
    assert fake_llm.bind_options == {"strict": True}

    assert asyncio.run(bound.ainvoke("again")) == "ready:again"
    assert attempts == 2


def test_deferred_startup_llm_remains_retryable_after_an_unconfigured_call(
    monkeypatch,
) -> None:
    fake_llm = _FakeLLM()
    attempts = 0

    def fake_create_llm(**_kwargs):
        nonlocal attempts
        attempts += 1
        if attempts <= 2:
            raise llm_client.ModelCredentialNotConfiguredError("missing test key")
        return fake_llm

    monkeypatch.setattr(llm_client, "create_llm", fake_create_llm)

    startup_llm = llm_client.create_startup_llm(streaming=True)

    with pytest.raises(llm_client.ModelCredentialNotConfiguredError):
        asyncio.run(startup_llm.ainvoke("before-configuration"))

    assert asyncio.run(startup_llm.ainvoke("after-configuration")) == (
        "ready:after-configuration"
    )
    assert attempts == 3


def test_startup_llm_defers_missing_provider_configuration(monkeypatch) -> None:
    attempts = 0

    def fake_create_llm(**_kwargs):
        nonlocal attempts
        attempts += 1
        raise llm_client.ModelConfigurationNotAvailableError("no provider")

    monkeypatch.setattr(llm_client, "create_llm", fake_create_llm)

    startup_llm = llm_client.create_startup_llm(streaming=True)

    assert isinstance(startup_llm, llm_client.DeferredChatModel)
    assert attempts == 1
    with pytest.raises(llm_client.ModelConfigurationNotAvailableError):
        asyncio.run(startup_llm.ainvoke("requires-model"))
    assert attempts == 2
