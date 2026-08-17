import asyncio
from types import SimpleNamespace

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from src.compression.checker import CompressionChecker, CompressionStrategy
from src.compression.strategies.full import FullCompactStrategy
from src.compression.strategies.micro import MicroCompactStrategy
from src.compression.strategies.reactive import ReactiveCompactStrategy
from src.core.graph_builder import _make_llm_node
from src.core.utils import trim_langchain_messages


def _tool_call(call_id: str, name: str = "read_file") -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[{"id": call_id, "name": name, "args": {"path": call_id}}],
    )


def _tool_result(call_id: str, content: str) -> ToolMessage:
    return ToolMessage(
        content=content,
        tool_call_id=call_id,
        name="read_file",
        additional_kwargs={"source": "test"},
    )


def test_micro_preserves_recent_tool_results_but_clips_oversized_history(monkeypatch):
    strategy = MicroCompactStrategy()
    monkeypatch.setattr(
        strategy.config_manager,
        "get_micro_compact_config",
        lambda: {
            "maxToolResults": 13,
            "toolResultTokenRatio": 0.01,
            "keepRecentToolResults": 5,
            "placeholder": "[Content compacted]",
        },
    )
    old_content = "old-head-" + ("x" * 8000) + "-old-tail"
    messages = [SystemMessage(content="system")]
    for index in range(6):
        call_id = f"call-{index}"
        messages.extend(
            [
                _tool_call(call_id),
                _tool_result(
                    call_id,
                    old_content if index == 0 else f"recent-result-{index}",
                ),
            ]
        )

    compacted = asyncio.run(strategy.execute(messages, max_context_tokens=1000))
    tool_results = [msg for msg in compacted if isinstance(msg, ToolMessage)]

    assert "old-head-" in tool_results[0].content
    assert "-old-tail" in tool_results[0].content
    assert "Tool result clipped" in tool_results[0].content
    assert [msg.content for msg in tool_results[1:]] == [
        f"recent-result-{index}" for index in range(1, 6)
    ]
    assert tool_results[0].additional_kwargs == {"source": "test"}


def test_emergency_tool_compaction_clips_recent_results_only_when_request_is_over_limit(
    monkeypatch,
):
    strategy = MicroCompactStrategy()
    monkeypatch.setattr(
        strategy.config_manager,
        "get_micro_compact_config",
        lambda: {
            "maxToolResults": 13,
            "toolResultTokenRatio": 0.20,
            "keepRecentToolResults": 5,
            "placeholder": "[Content compacted]",
        },
    )
    giant = "new-head-" + ("z" * 12000) + "-new-tail"
    messages = [
        SystemMessage(content="system"),
        _tool_call("latest"),
        _tool_result("latest", giant),
    ]

    normal = asyncio.run(strategy.execute(messages, max_context_tokens=1000))
    emergency = strategy.compact_for_request(messages, max_context_tokens=1000)

    assert normal[-1].content == giant
    assert emergency[-1].content != giant
    assert "new-head-" in emergency[-1].content
    assert "-new-tail" in emergency[-1].content
    assert "Tool result clipped" in emergency[-1].content


def test_tool_result_clipping_respects_existing_mixed_language_estimator():
    content = "中文开头" + ("内容" * 3000) + "english-tail"

    clipped = MicroCompactStrategy._clip_content(content, target_tokens=120)

    from src.core.utils import estimate_tokens

    assert estimate_tokens(clipped) <= 120
    assert clipped.startswith("中文开头")
    assert clipped.endswith("english-tail")


def test_checker_prioritizes_oversized_historical_tool_result_before_full(monkeypatch):
    checker = CompressionChecker()
    monkeypatch.setattr(
        checker.model_manager,
        "get_model_info",
        lambda _model=None: {"maxContextTokens": 1000},
    )
    monkeypatch.setattr(
        checker.config_manager,
        "get_general_config",
        lambda: {"compactionThreshold": 0.8},
    )
    monkeypatch.setattr(
        checker.config_manager,
        "get_micro_compact_config",
        lambda: {
            "maxToolResults": 13,
            "toolResultTokenRatio": 0.1,
            "keepRecentToolResults": 5,
        },
    )
    messages = []
    for index in range(6):
        messages.append(
            _tool_result(
                f"call-{index}",
                ("x" * 4000) if index == 0 else f"recent-{index}",
            )
        )

    decision = checker.pre_check(messages)

    assert decision.strategy == CompressionStrategy.MICRO
    assert decision.details["oversized_historical_tool_results"] == 1


def test_full_compact_reuses_latest_checkpoint_and_only_new_delta(monkeypatch):
    strategy = FullCompactStrategy()
    captured = {}

    monkeypatch.setattr(
        strategy.config_manager,
        "get_full_compact_config",
        lambda: {
            "keepRecentTokens": 8,
            "maxRetryCount": 0,
            "summaryTokenBudget": 100,
        },
    )

    async def fake_generate(messages, **_kwargs):
        captured["messages"] = messages
        return "new checkpoint"

    monkeypatch.setattr(strategy, "_generate_summary", fake_generate)
    messages = [
        SystemMessage(content="system"),
        HumanMessage(content="raw history that must not return"),
        AIMessage(content="raw answer that must not return"),
        AIMessage(content="<summary>previous checkpoint</summary>"),
        HumanMessage(content="delta one"),
        AIMessage(content="delta answer"),
        HumanMessage(content="latest question"),
        AIMessage(content="latest answer"),
    ]

    compacted = asyncio.run(
        strategy.execute(messages, model_override="test:model")
    )

    summary_source = captured["messages"]
    assert summary_source[0].content == "<summary>previous checkpoint</summary>"
    assert all("raw history" not in str(msg.content) for msg in summary_source)
    assert all("raw answer" not in str(msg.content) for msg in summary_source)
    assert any("delta one" in str(msg.content) for msg in summary_source)
    assert compacted[1].content == "<summary>\nnew checkpoint\n</summary>"


def test_full_split_keeps_complete_round_for_recent_protected_tool_result(monkeypatch):
    strategy = FullCompactStrategy()
    monkeypatch.setattr(
        strategy.config_manager,
        "get_micro_compact_config",
        lambda: {"keepRecentToolResults": 5},
    )
    messages = [
        SystemMessage(content="system"),
        HumanMessage(content="old question"),
        AIMessage(content="old answer"),
        HumanMessage(content="tool question"),
        _tool_call("latest"),
        _tool_result("latest", "x" * 10000),
        AIMessage(content="tool answer"),
    ]

    compressible, recent = strategy._split_messages(messages, keep_recent_tokens=1)

    assert [msg.content for msg in compressible] == ["old question", "old answer"]
    assert recent[0].content == "tool question"
    assert any(isinstance(msg, ToolMessage) for msg in recent)


def test_compressor_overflow_only_clips_tool_results(monkeypatch):
    strategy = FullCompactStrategy()
    monkeypatch.setattr(
        strategy.model_manager,
        "get_model_info",
        lambda _model=None: {"maxContextTokens": 500},
    )
    monkeypatch.setattr(
        strategy.micro_strategy.config_manager,
        "get_micro_compact_config",
        lambda: {
            "toolResultTokenRatio": 0.2,
            "keepRecentToolResults": 5,
        },
    )
    messages = [
        AIMessage(content="<summary>previous checkpoint</summary>"),
        HumanMessage(content="keep this user delta verbatim"),
        _tool_call("giant"),
        _tool_result("giant", "tool-head-" + ("x" * 6000) + "-tool-tail"),
    ]

    request = strategy._prepare_compressor_request(
        messages,
        system_prompt="compressor system",
        model_override="test:model",
    )

    assert request is not None
    serialized = request[1].content
    assert "keep this user delta verbatim" in serialized
    assert "tool-head-" in serialized
    assert "-tool-tail" in serialized
    assert "Tool result clipped" in serialized


def test_compressor_does_not_truncate_non_tool_messages_to_force_a_request(monkeypatch):
    strategy = FullCompactStrategy()
    monkeypatch.setattr(
        strategy.model_manager,
        "get_model_info",
        lambda _model=None: {"maxContextTokens": 100},
    )
    messages = [HumanMessage(content="must-stay-verbatim-" + ("x" * 4000))]

    request = strategy._prepare_compressor_request(
        messages,
        system_prompt="compressor system",
        model_override="test:model",
    )

    assert request is None


def test_reactive_discards_one_complete_round_and_preserves_checkpoint():
    strategy = ReactiveCompactStrategy()
    checkpoint = AIMessage(content="<summary>checkpoint</summary>")
    messages = [
        SystemMessage(content="system"),
        HumanMessage(content="raw history"),
        AIMessage(content="raw answer"),
        checkpoint,
        HumanMessage(content="old delta"),
        AIMessage(content="old delta answer"),
        HumanMessage(content="latest"),
        AIMessage(content="latest answer"),
    ]

    compacted = strategy.discard_oldest_round(messages)

    assert compacted[0].content == "system"
    assert compacted[1] is checkpoint
    assert [msg.content for msg in compacted[2:]] == ["latest", "latest answer"]


def test_hard_trim_preserves_latest_checkpoint_instead_of_restoring_raw_history():
    checkpoint = AIMessage(content="<summary>checkpoint</summary>")
    messages = [
        SystemMessage(content="system"),
        HumanMessage(content="raw history"),
        AIMessage(content="raw answer"),
        checkpoint,
        HumanMessage(content="x" * 1000),
    ]

    trimmed = trim_langchain_messages(messages, max_tokens=20)

    assert checkpoint in trimmed
    assert all("raw history" not in str(msg.content) for msg in trimmed)
    assert all("raw answer" not in str(msg.content) for msg in trimmed)


def test_llm_node_retries_only_current_request_after_context_overflow(monkeypatch):
    class FakeBoundModel:
        def __init__(self):
            self.calls = []

        async def ainvoke(self, messages):
            self.calls.append(messages)
            if len(self.calls) == 1:
                raise RuntimeError("context overflow")
            return AIMessage(content="ok")

    class FakeModel:
        def __init__(self):
            self.bound = FakeBoundModel()

        def bind_tools(self, _tools, strict=True):
            assert strict is True
            return self.bound

    monkeypatch.setattr(
        "src.core.model_manager.get_model_manager",
        lambda: SimpleNamespace(
            get_model_info=lambda _model=None: {"maxContextTokens": 100000}
        ),
    )
    model = FakeModel()
    node = _make_llm_node(model, [SimpleNamespace(name="read_file")])
    messages = [
        SystemMessage(content="system"),
        AIMessage(content="<summary>checkpoint</summary>"),
        HumanMessage(content="old delta"),
        AIMessage(content="old delta answer"),
        HumanMessage(content="current"),
    ]

    result = asyncio.run(
        node(
            {
                "messages": messages,
                "session_id": "session-1",
                "agent_type": "main",
                "remaining_rounds": 2,
                "metadata": {"model_id": "test:model"},
            }
        )
    )

    assert result["messages"][0].content == "ok"
    assert len(model.bound.calls) == 2
    assert model.bound.calls[1][1].content == "<summary>checkpoint</summary>"
    assert all("old delta" not in str(msg.content) for msg in model.bound.calls[1])


def test_llm_node_preflight_clips_a_just_returned_oversized_tool_result(monkeypatch):
    class CapturingModel:
        def __init__(self):
            self.calls = []

        async def ainvoke(self, messages):
            self.calls.append(messages)
            return AIMessage(content="ok")

    monkeypatch.setattr(
        "src.core.model_manager.get_model_manager",
        lambda: SimpleNamespace(
            get_model_info=lambda _model=None: {"maxContextTokens": 500}
        ),
    )
    model = CapturingModel()
    node = _make_llm_node(model, [])
    giant = "latest-head-" + ("z" * 8000) + "-latest-tail"

    result = asyncio.run(
        node(
            {
                "messages": [
                    SystemMessage(content="system"),
                    _tool_call("latest"),
                    _tool_result("latest", giant),
                ],
                "session_id": "session-giant-tool",
                "agent_type": "main",
                "remaining_rounds": 2,
                "metadata": {"model_id": "test:model"},
            }
        )
    )

    sent_tool_result = next(
        msg for msg in model.calls[0] if isinstance(msg, ToolMessage)
    )
    assert result["messages"][0].content == "ok"
    assert sent_tool_result.content != giant
    assert "latest-head-" in sent_tool_result.content
    assert "-latest-tail" in sent_tool_result.content


def test_compressor_llm_node_never_reactive_discards_history(monkeypatch):
    class FailingModel:
        async def ainvoke(self, messages):
            raise RuntimeError("context overflow")

    node = _make_llm_node(FailingModel(), [])
    messages = [
        SystemMessage(content="system"),
        AIMessage(content="<summary>checkpoint</summary>"),
        HumanMessage(content="delta"),
    ]

    try:
        asyncio.run(
            node(
                {
                    "messages": messages,
                    "session_id": "compressor-1",
                    "agent_type": "compressor",
                    "remaining_rounds": 1,
                    "metadata": {"model_id": "test:model"},
                }
            )
        )
    except RuntimeError as exc:
        assert "context overflow" in str(exc)
    else:
        raise AssertionError("compressor context overflow must be propagated")
