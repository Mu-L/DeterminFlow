from __future__ import annotations

import asyncio
from types import SimpleNamespace

from src.workflow.definition import WorkflowDef, WorkflowNode
from src.workflow.nodes.agent import AgentNode
from src.workflow.nodes.base import NodeContext
from src.workflow.runtime_models import NodeExecutionState


def _run_node(node: WorkflowNode, session_manager) -> object:
    return asyncio.run(
        AgentNode().execute(
            NodeContext(
                definition=WorkflowDef(
                    workflow_id="wf-output-recovery",
                    nodes=[node],
                ),
                node_def=node,
                node_state=NodeExecutionState(node_id=node.id),
                session_manager=session_manager,
            )
        )
    )


def test_ungated_output_does_not_fall_back_to_older_assistant_message() -> None:
    class SessionManager:
        def __init__(self):
            self.sessions = {}

        async def create_sub_session(self, **kwargs):
            session_id = "latest-empty"
            self.sessions[session_id] = SimpleNamespace(
                record=[
                    {"type": "assistant", "content": "older result"},
                    {"type": "assistant", "content": ""},
                ],
                get_cumulative_token_usage=lambda: None,
            )
            kwargs["on_auto_complete"](session_id, "done", "success", "")
            return {"success": True, "session_id": session_id}

    result = _run_node(
        WorkflowNode(
            id="writer",
            node_type="agent",
            first_message="write",
            output_variable="draft",
        ),
        SessionManager(),
    )

    assert result.status == "completed"
    assert result.outputs == {}


def test_empty_output_retry_disables_tools_and_recounts_tokens(monkeypatch) -> None:
    usage = {"total": 10}
    configured_tools: list[list] = []

    class Session:
        model_id = "provider:model"
        model_params = {"temperature": 0.2}

        def __init__(self):
            self.record = [
                {"type": "assistant", "content": "older result"},
                {"type": "assistant", "content": ""},
            ]

        def setup_graph(self, *, llm, tools):
            assert llm == "retry-llm"
            configured_tools.append(tools)

        async def send_message(self, content, **kwargs):
            assert "最终正文" in content
            assert kwargs["max_rounds"] == 1
            assert kwargs["source"] == "workflow_empty_output_retry"
            self.record.append({"type": "assistant", "content": "new result"})
            usage["total"] = 25

        def get_cumulative_token_usage(self):
            return {"provider:model": {"total_tokens": usage["total"]}}

    class SessionManager:
        def __init__(self):
            self.sessions = {}

        async def create_sub_session(self, **kwargs):
            session_id = "retry-empty"
            self.sessions[session_id] = Session()
            kwargs["on_auto_complete"](session_id, "done", "success", "")
            return {"success": True, "session_id": session_id}

    monkeypatch.setattr(
        "src.core.llm_client.create_llm",
        lambda **kwargs: "retry-llm",
    )
    result = _run_node(
        WorkflowNode(
            id="writer",
            node_type="agent",
            first_message="write",
            output_variable="draft",
            require_non_empty_output=True,
            retry_empty_output_in_session=True,
        ),
        SessionManager(),
    )

    assert result.status == "completed"
    assert result.outputs == {"draft": "new result"}
    assert result.token_usage == {
        "provider:model": {"total_tokens": 25},
    }
    assert configured_tools == [[]]


def test_json_retry_reads_the_true_latest_empty_message(monkeypatch) -> None:
    class Session:
        session_id = "json-retry"
        model_id = "provider:model"
        model_params = {}

        def __init__(self):
            self.record = [
                {"type": "assistant", "content": '{"body":"older valid"}'},
                {"type": "assistant", "content": "{"},
            ]

        def setup_graph(self, *, llm, tools):
            assert llm == "retry-llm"
            assert tools == []

        async def send_message(self, *_args, **_kwargs):
            self.record.append({"type": "assistant", "content": ""})

    manager = SimpleNamespace(sessions={"json-retry": Session()})
    monkeypatch.setattr(
        "src.core.llm_client.create_llm",
        lambda **_kwargs: "retry-llm",
    )
    result = asyncio.run(
        AgentNode()._prepare_json_output(
            sm=manager,
            session_id="json-retry",
            raw_output="{",
            node_params={
                "json_repair_policy": "retry_only",
                "json_retry_count": 1,
            },
            output_file_path="result.json",
        )
    )

    assert result["success"] is False
    assert "重试 1 次后仍校验失败" in result["error"]


def test_last_assistant_message_does_not_skip_latest_empty_message() -> None:
    from src.agent.session import AgentSession
    from src.agent.session_catalog import SessionMetadata

    session = AgentSession(session_id="latest-assistant")
    session.record = [
        {"type": "assistant", "content": "older result"},
        {"type": "assistant", "content": ""},
    ]

    assert session.get_last_assistant_message() == ""
    assert SessionMetadata.from_data({
        "session_id": "latest-assistant",
        "record": session.record,
    }).last_message == ""


def test_latest_assistant_message_normalizes_structured_text_blocks() -> None:
    from src.agent.session import AgentSession
    from src.agent.session_catalog import SessionMetadata

    content = [
        {"type": "thinking", "thinking": "internal"},
        {"type": "text", "text": "final "},
        {"type": "text_delta", "text": "answer"},
    ]
    session = AgentSession(session_id="structured-assistant")
    session.record = [{"type": "assistant", "content": content}]
    manager = SimpleNamespace(sessions={session.session_id: session})

    assert session.get_last_assistant_message() == "final answer"
    assert AgentNode._get_latest_ai_message(manager, session.session_id) == "final answer"
    assert SessionMetadata.from_data({
        "session_id": session.session_id,
        "record": session.record,
    }).last_message == "final answer"
