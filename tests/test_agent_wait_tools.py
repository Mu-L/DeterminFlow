from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

import src.workflow.manager as workflow_manager_module
import src.workflow.task_queries as task_queries_module
from src.agent.session import AgentSession
from src.agent.session_manager import SessionManager
from src.mcp.tool_adapter import create_session_tools
from src.session.context import set_session_context
from src.workflow.definition import WorkflowDef
from src.workflow.main_result_tools import (
    create_get_node_messages_tool,
    create_get_task_result_tool,
)
from src.workflow.manager import WorkflowManager
from src.workflow.runtime_models import NodeExecutionState, WorkflowTask
from src.workflow.tools import create_get_task_status_tool


def _json_result(raw: str) -> dict:
    return json.loads(raw)


async def _cancel(task: asyncio.Task) -> None:
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


def test_check_sub_progress_waits_for_terminal_and_returns_bounded_result():
    async def scenario():
        manager = SessionManager()
        session = AgentSession(
            session_type="sub",
            parent_id="main-1",
            task_description="inspect",
        )
        session.status = "running"
        manager.sessions[session.session_id] = session
        keep_active = asyncio.create_task(asyncio.Event().wait())
        manager._sub_tasks[session.session_id] = keep_active

        waiter = asyncio.create_task(manager.check_sub_progress(
            session_id=session.session_id,
            wait_for="terminal_or_attention",
            timeout_seconds=1,
        ))
        await asyncio.sleep(0)
        assert manager._session_changes.waiter_count(session.session_id) == 1

        final_output = "x" * 20_050
        session.record.append({"type": "assistant", "content": final_output})
        session.status = "completed"
        manager._signal_session_update(session.session_id)

        result = await waiter
        assert result["wait_outcome"] == "terminal"
        assert result["terminal"] is True
        assert result["attention_required"] is False
        assert result["sessions"][0]["last_message"] == final_output[:200]
        assert result["sessions"][0]["final_output"] == final_output[:20_000]
        assert result["sessions"][0]["final_output_truncated"] is True
        assert manager._session_changes.waiter_count(session.session_id) == 0
        await _cancel(keep_active)

    asyncio.run(scenario())


def test_check_sub_progress_wait_requires_target_and_cleans_up_cancellation():
    async def scenario():
        manager = SessionManager()
        missing_target = await manager.check_sub_progress(
            wait_for="change",
            timeout_seconds=1,
        )
        assert missing_target["error"] == "session_id_required_for_wait"

        session = AgentSession(session_type="sub", parent_id="main-1")
        session.status = "running"
        manager.sessions[session.session_id] = session
        keep_active = asyncio.create_task(asyncio.Event().wait())
        manager._sub_tasks[session.session_id] = keep_active
        waiter = asyncio.create_task(manager.check_sub_progress(
            session_id=session.session_id,
            wait_for="terminal_or_attention",
            timeout_seconds=None,
        ))
        await asyncio.sleep(0)
        assert manager._session_changes.waiter_count(session.session_id) == 1
        await _cancel(waiter)
        assert manager._session_changes.waiter_count(session.session_id) == 0
        await _cancel(keep_active)

    asyncio.run(scenario())


def test_check_sub_progress_schema_keeps_immediate_default_and_exposes_wait():
    manager = SessionManager()
    check_tool = next(
        tool for tool in create_session_tools(manager)
        if tool.name == "check_sub_progress"
    )

    fields = check_tool.args_schema.model_fields
    assert fields["wait_for"].default == "none"
    assert fields["timeout_seconds"].default == 0


def _workflow_wait_fixture(tmp_path, monkeypatch):
    workflows_dir = tmp_path / "workflows"
    workflow_id = "wf-wait"
    workflow_dir = workflows_dir / workflow_id
    workflow_dir.mkdir(parents=True)
    (workflow_dir / "definition.json").write_text(
        json.dumps(WorkflowDef(
            workflow_id=workflow_id,
            name="wait",
        ).to_dict()),
        encoding="utf-8",
    )
    monkeypatch.setattr(workflow_manager_module, "WORKFLOWS_DIR", workflows_dir)
    monkeypatch.setattr(task_queries_module, "WORKFLOWS_DIR", workflows_dir)
    sessions = SimpleNamespace(sessions={
        "main-1": SimpleNamespace(workflow_id=workflow_id, task_id="task-wait"),
    })
    manager = WorkflowManager(sessions)
    task = WorkflowTask(
        task_id="task-wait",
        workflow_id=workflow_id,
        name="wait",
        status="running",
        main_session_id="main-1",
        snapshot_definition={"nodes": [{"id": "write"}]},
        node_states={
            "write": NodeExecutionState(node_id="write", status="running"),
        },
    )
    manager._save_task(task)
    set_session_context(session_id="main-1")
    return manager, sessions, task


def test_workflow_status_and_result_waiters_share_terminal_update(
    tmp_path,
    monkeypatch,
):
    async def scenario():
        manager, sessions, task = _workflow_wait_fixture(tmp_path, monkeypatch)
        status_tool = create_get_task_status_tool(manager, sessions)
        result_tool = create_get_task_result_tool(manager, sessions)
        kwargs = {
            "workflow_id": task.workflow_id,
            "task_id": task.task_id,
            "wait_for": "terminal_or_attention",
            "timeout_seconds": 1,
        }

        status_waiter = asyncio.create_task(status_tool.coroutine(**kwargs))
        result_waiter = asyncio.create_task(result_tool.coroutine(**kwargs))
        await asyncio.sleep(0)
        key = manager._task_change_key(task.workflow_id, task.task_id)
        assert manager._task_changes.waiter_count(key) == 2

        task.status = "completed"
        task.completed_at = "2026-08-05T12:00:00+08:00"
        task.node_states["write"] = NodeExecutionState(
            node_id="write",
            status="completed",
            summary="done",
            outputs={"text": "result"},
        )
        manager._save_task(task)
        manager._push_task_update(task.workflow_id, task)

        status = _json_result(await status_waiter)
        result = _json_result(await result_waiter)
        assert status["wait_outcome"] == "terminal"
        assert status["terminal"] is True
        assert status["progress"] == {"completed": 1, "total": 1}
        assert result["wait_outcome"] == "terminal"
        assert result["terminal"] is True
        assert result["nodes"]["write"]["outputs"] == {"text": "result"}
        assert manager._task_changes.waiter_count(key) == 0

    asyncio.run(scenario())


def test_workflow_wait_returns_for_attention_and_timeout(tmp_path, monkeypatch):
    async def scenario():
        manager, sessions, task = _workflow_wait_fixture(tmp_path, monkeypatch)
        status_tool = create_get_task_status_tool(manager, sessions)

        task.node_states["write"].status = "waiting_approval"
        manager._save_task(task)
        attention = _json_result(await status_tool.coroutine(
            workflow_id=task.workflow_id,
            task_id=task.task_id,
            wait_for="terminal_or_attention",
            timeout_seconds=None,
        ))
        assert attention["wait_outcome"] == "attention_required"
        assert attention["terminal"] is False
        assert attention["attention_required"] is True

        task.node_states["write"].status = "running"
        manager._save_task(task)
        timed_out = _json_result(await status_tool.coroutine(
            workflow_id=task.workflow_id,
            task_id=task.task_id,
            wait_for="change",
            timeout_seconds=0.01,
        ))
        assert timed_out["wait_outcome"] == "timeout"
        assert timed_out["terminal"] is False
        key = manager._task_change_key(task.workflow_id, task.task_id)
        assert manager._task_changes.waiter_count(key) == 0

    asyncio.run(scenario())


def test_workflow_wait_fields_do_not_leak_to_node_message_schema(
    tmp_path,
    monkeypatch,
):
    manager, sessions, _task = _workflow_wait_fixture(tmp_path, monkeypatch)
    status_fields = create_get_task_status_tool(
        manager, sessions,
    ).args_schema.model_fields
    result_fields = create_get_task_result_tool(
        manager, sessions,
    ).args_schema.model_fields
    message_fields = create_get_node_messages_tool(
        manager, sessions,
    ).args_schema.model_fields

    assert status_fields["wait_for"].default == "none"
    assert result_fields["wait_for"].default == "none"
    assert "wait_for" not in message_fields
    assert "timeout_seconds" not in message_fields
