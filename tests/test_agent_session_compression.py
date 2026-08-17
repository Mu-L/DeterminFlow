import asyncio
from types import SimpleNamespace

from src.agent.session import AgentSession
from src.compression.checker import CompressionDecision, CompressionStrategy


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


def test_compression_runs_micro_then_full_when_context_still_exceeds_threshold(
    monkeypatch,
):
    calls = []

    class Checker:
        def pre_check(self, *, messages, model_override):
            return SimpleNamespace(
                strategy=SimpleNamespace(value="micro"),
                reason="tool result pressure",
            )

        def full_check(self, *, messages, model_override):
            assert messages == ["after-micro"]
            return SimpleNamespace(
                strategy=SimpleNamespace(value="full"),
                reason="context pressure remains",
            )

        def hard_limit_exceeded(self, *, messages, model_override):
            return False

    class Scheduler:
        async def execute(self, *, decision, messages, **_kwargs):
            calls.append(decision.strategy.value)
            if decision.strategy.value == "micro":
                return ["after-micro"]
            return ["after-full"]

    monkeypatch.setattr(
        "src.agent.session.get_compression_checker", lambda: Checker()
    )
    monkeypatch.setattr(
        "src.agent.session.get_compression_scheduler", lambda: Scheduler()
    )
    session = AgentSession(session_id="compression-chain-test")
    session.lc_messages = ["before"]

    asyncio.run(session._check_and_compress_messages())

    assert calls == ["micro", "full"]
    assert session.lc_messages == ["after-full"]


def test_hard_limit_reactive_compaction_remeasures_after_each_round(monkeypatch):
    calls = []

    class Checker:
        def pre_check(self, *, messages, model_override):
            return CompressionDecision(CompressionStrategy.NONE, "none")

        def hard_limit_exceeded(self, *, messages, model_override):
            return len(messages) > 2

        def get_context_stats(self, messages, model_override):
            return {"max_tokens": 100}

    class Scheduler:
        config_manager = SimpleNamespace(
            get_reactive_compact_config=lambda: {"maxRetryCount": 5}
        )

        def compact_tool_results_for_request(self, messages, **_kwargs):
            return messages

        async def execute(self, *, decision, messages, **_kwargs):
            calls.append(decision.strategy.value)
            return messages[1:]

    monkeypatch.setattr(
        "src.agent.session.get_compression_checker", lambda: Checker()
    )
    monkeypatch.setattr(
        "src.agent.session.get_compression_scheduler", lambda: Scheduler()
    )
    session = AgentSession(session_id="compression-reactive-chain")
    session.lc_messages = ["one", "two", "three", "four"]

    asyncio.run(session._check_and_compress_messages())

    assert calls == ["reactive", "reactive"]
    assert session.lc_messages == ["three", "four"]


def test_compressor_never_enters_full_or_reactive_session_compaction(monkeypatch):
    calls = []

    class Checker:
        def pre_check(self, *, messages, model_override):
            return CompressionDecision(CompressionStrategy.FULL, "over threshold")

        def hard_limit_exceeded(self, *, messages, model_override):
            return True

        def get_context_stats(self, messages, model_override):
            return {"max_tokens": 100}

    class Scheduler:
        config_manager = SimpleNamespace(
            get_reactive_compact_config=lambda: {"maxRetryCount": 5}
        )

        def compact_tool_results_for_request(self, messages, **_kwargs):
            calls.append("emergency-micro")
            return messages

        async def execute(self, **_kwargs):
            calls.append("forbidden")
            return _kwargs["messages"]

    monkeypatch.setattr(
        "src.agent.session.get_compression_checker", lambda: Checker()
    )
    monkeypatch.setattr(
        "src.agent.session.get_compression_scheduler", lambda: Scheduler()
    )
    session = AgentSession(
        session_id="compression-agent-no-recursion",
        agent_type="compressor",
    )
    session.lc_messages = ["checkpoint", "delta"]

    asyncio.run(session._check_and_compress_messages())

    assert calls == ["emergency-micro"]
    assert session.lc_messages == ["checkpoint", "delta"]
