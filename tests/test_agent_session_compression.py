import asyncio
from types import SimpleNamespace

from src.agent.session import AgentSession


def test_compression_uses_session_model_for_check_and_execution(monkeypatch):
    calls = {}

    class Checker:
        def pre_check(self, *, messages, model_override):
            calls["check_model"] = model_override
            return SimpleNamespace(
                strategy=SimpleNamespace(value="full"),
                reason="test",
            )

    class Scheduler:
        async def execute(self, *, model_override, messages, **_kwargs):
            calls["execution_model"] = model_override
            return messages

    monkeypatch.setattr(
        "src.agent.session.get_compression_checker", lambda: Checker()
    )
    monkeypatch.setattr(
        "src.agent.session.get_compression_scheduler", lambda: Scheduler()
    )
    session = AgentSession(session_id="compression-model-test")
    session.model_id = "openai:gpt-5.6-sol"

    asyncio.run(session._check_and_compress_messages())

    assert calls == {
        "check_model": "openai:gpt-5.6-sol",
        "execution_model": "openai:gpt-5.6-sol",
    }
