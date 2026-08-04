from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import src.config as config_module
import src.core.llm_client as llm_client_module
import src.workflow.manager as workflow_manager_module
import src.workflow.task_recovery as task_recovery_module
from src.core.workspace_manager import WorkspaceManager
from src.web.workflow_node_control_routes import router
from src.workflow.definition import (
    NodeExecutionState,
    WorkflowDef,
    WorkflowNode,
    WorkflowTask,
)
from src.workflow.failure_policy import AUTO_RETRY_TRIGGER
from src.workflow.manager import WorkflowManager
from src.workflow.nodes import BaseNodePlugin, NodeContext, NodeResult, registry


class _WorkflowMainSession:
    def __init__(self, session_id: str):
        self.session_id = session_id
        self.status = "running"

    async def async_save(self):
        return None


class _SessionManager:
    def __init__(self):
        self.sessions = {}
        self.main_session_id = ""

    def get_session(self, _session_id: str):
        return None

    async def init_workflow_main(self, **_kwargs):
        session = _WorkflowMainSession(
            f"workflow-main-{len(self.sessions) + 1}"
        )
        return session


def _manager(tmp_path, monkeypatch) -> WorkflowManager:
    workflows_dir = tmp_path / "workflows"
    monkeypatch.setattr(config_module, "WORKFLOWS_DIR", workflows_dir)
    monkeypatch.setattr(workflow_manager_module, "WORKFLOWS_DIR", workflows_dir)
    monkeypatch.setattr(task_recovery_module, "WORKFLOWS_DIR", workflows_dir)
    manager = WorkflowManager(_SessionManager())
    manager._ws_manager = WorkspaceManager(
        base_dir=str(tmp_path / "workspace-root")
    )
    manager._engine.set_workspace_manager(manager._ws_manager)
    return manager


def _save_task(
    manager: WorkflowManager,
    *,
    workflow_id: str = "wf-retry",
    task_id: str = "task-retry",
    task_status: str = "failed",
    node_status: str = "failed",
    node: WorkflowNode | None = None,
) -> WorkflowTask:
    definition = WorkflowDef(
        workflow_id=workflow_id,
        nodes=[node or WorkflowNode(id="writer", label="写作")],
    )
    state = NodeExecutionState(
        node_id="writer",
        status=node_status,
        session_id="session-old",
        started_at="2026-07-22T08:00:00+00:00",
        completed_at="2026-07-22T08:01:00+00:00",
        summary="部分结果",
        error="upstream unavailable",
        outputs={"draft": "partial"},
        stdout="partial stdout",
        stderr="partial stderr",
        attempt_count=1,
        automatic_retry_count=1,
        input_snapshot={"topic": "原始主题"},
    )
    task = WorkflowTask(
        task_id=task_id,
        workflow_id=workflow_id,
        name="重试测试",
        status=task_status,
        completed_at="2026-07-22T08:01:00+00:00",
        snapshot_definition=definition.to_dict(),
        parameter_values={"topic": "原始主题"},
        node_states={"writer": state},
    )
    manager._save_task(task)
    return task


def test_manual_retry_preserves_task_snapshot_and_parameters(
    tmp_path, monkeypatch,
) -> None:
    manager = _manager(tmp_path, monkeypatch)
    original = _save_task(manager)
    launches: list[tuple[str, str]] = []

    async def fake_run(workflow_id: str, task_id: str):
        launches.append((workflow_id, task_id))
        await asyncio.sleep(0)
        return {"success": True, "workflow_id": workflow_id, "task_id": task_id}

    manager.run_task = fake_run
    async def scenario():
        return await asyncio.gather(
            manager.retry_node(
                original.workflow_id, original.task_id, "writer", 1,
            ),
            manager.retry_node(
                original.workflow_id, original.task_id, "writer", 1,
            ),
        )

    first, second = asyncio.run(scenario())

    assert first["success"] is True
    assert second["error"] == "node_control_conflict"
    assert launches == [(original.workflow_id, original.task_id)]
    saved = manager._load_task(original.workflow_id, original.task_id)
    assert saved.snapshot_definition == original.snapshot_definition
    assert saved.parameter_values == {"topic": "原始主题"}
    assert saved.status == "resume_pending"
    state = saved.node_states["writer"]
    assert state.status == "pending"
    assert state.automatic_retry_count == 0
    assert state.next_attempt_trigger == "manual_retry"
    assert state.input_snapshot == {"topic": "原始主题"}
    assert state.outputs == {}
    assert state.stdout == ""
    assert state.stderr == ""


def test_manual_skip_clears_partial_outputs_and_resumes(
    tmp_path, monkeypatch,
) -> None:
    manager = _manager(tmp_path, monkeypatch)
    original = _save_task(
        manager,
        task_status="retry_waiting",
        node_status="retry_waiting",
    )
    state = manager._load_task(original.workflow_id, original.task_id).node_states[
        "writer"
    ]
    state.next_retry_at = (
        datetime.now(timezone.utc) + timedelta(minutes=10)
    ).isoformat()
    task = manager._load_task(original.workflow_id, original.task_id)
    task.node_states["writer"] = state
    manager._save_task(task)

    async def fake_run(workflow_id: str, task_id: str):
        return {"success": True, "workflow_id": workflow_id, "task_id": task_id}

    manager.run_task = fake_run
    result = asyncio.run(manager.skip_node(
        original.workflow_id, original.task_id, "writer", 1,
    ))

    assert result["success"] is True
    saved = manager._load_task(original.workflow_id, original.task_id)
    state = saved.node_states["writer"]
    assert state.status == "skipped"
    assert state.is_skipped is True
    assert state.outputs == {}
    assert state.stdout == ""
    assert state.stderr == ""
    assert state.next_retry_at is None


def test_node_control_rejects_terminal_task_and_stale_attempt(
    tmp_path, monkeypatch,
) -> None:
    manager = _manager(tmp_path, monkeypatch)
    stopped = _save_task(
        manager,
        workflow_id="wf-stopped",
        task_id="task-stopped",
        task_status="stopped",
        node_status="failed",
    )
    stale = _save_task(
        manager,
        workflow_id="wf-stale",
        task_id="task-stale",
        task_status="failed",
        node_status="failed",
    )
    launches = 0

    async def fake_run(_workflow_id: str, _task_id: str):
        nonlocal launches
        launches += 1
        return {"success": True}

    manager.run_task = fake_run
    stopped_result = asyncio.run(manager.retry_node(
        stopped.workflow_id, stopped.task_id, "writer", 1,
    ))
    stale_result = asyncio.run(manager.skip_node(
        stale.workflow_id, stale.task_id, "writer", 0,
    ))

    assert stopped_result["error"] == "node_control_conflict"
    assert stale_result["error"] == "node_control_stale"
    assert launches == 0
    assert manager._load_task(stopped.workflow_id, stopped.task_id).status == "stopped"
    assert manager._load_task(stale.workflow_id, stale.task_id).node_states[
        "writer"
    ].status == "failed"


def test_due_retry_activation_does_not_double_count_automatic_retry(
    tmp_path, monkeypatch,
) -> None:
    manager = _manager(tmp_path, monkeypatch)
    task = _save_task(
        manager,
        task_status="retry_waiting",
        node_status="retry_waiting",
    )
    saved = manager._load_task(task.workflow_id, task.task_id)
    saved.node_states["writer"].next_retry_at = (
        datetime.now(timezone.utc) - timedelta(seconds=1)
    ).isoformat()
    saved.node_states["writer"].next_attempt_trigger = AUTO_RETRY_TRIGGER
    manager._save_task(saved)
    launches = 0

    async def fake_run(workflow_id: str, task_id: str):
        nonlocal launches
        launches += 1
        return {"success": True, "workflow_id": workflow_id, "task_id": task_id}

    manager.run_task = fake_run
    manager._retry_timer_generations[task.task_id] = 7
    asyncio.run(manager._activate_due_retries(
        task.workflow_id, task.task_id, generation=7
    ))

    activated = manager._load_task(task.workflow_id, task.task_id)
    state = activated.node_states["writer"]
    assert launches == 1
    assert state.status == "pending"
    assert state.session_id == "session-old"
    assert state.summary == "部分结果"
    assert state.automatic_retry_count == 1
    assert state.next_attempt_trigger == AUTO_RETRY_TRIGGER
    assert state.next_retry_at is None
    assert activated.status == "resume_pending"


def test_failed_retry_launch_remains_recoverable(
    tmp_path, monkeypatch,
) -> None:
    manager = _manager(tmp_path, monkeypatch)
    task = _save_task(
        manager,
        task_status="retry_waiting",
        node_status="retry_waiting",
    )
    saved = manager._load_task(task.workflow_id, task.task_id)
    saved.node_states["writer"].next_retry_at = (
        datetime.now(timezone.utc) - timedelta(seconds=1)
    ).isoformat()
    saved.node_states["writer"].next_attempt_trigger = AUTO_RETRY_TRIGGER
    manager._save_task(saved)
    launches = 0

    async def failed_run(_workflow_id: str, _task_id: str):
        nonlocal launches
        launches += 1
        return {"success": False, "message": "launch unavailable"}

    manager.run_task = failed_run

    async def scenario():
        manager._retry_timer_generations[task.task_id] = 4
        await manager._activate_due_retries(
            task.workflow_id, task.task_id, generation=4,
        )
        stranded = manager._load_task(task.workflow_id, task.task_id)
        recovered_summary = await manager.recover_workflow_tasks()
        await manager.shutdown_task_recovery()
        return stranded, recovered_summary

    stranded, recovered_summary = asyncio.run(scenario())

    assert stranded.status == "resume_pending"
    assert stranded.node_states["writer"].status == "pending"
    assert recovered_summary["scanned"] == 1
    assert launches == 2


class _AutomaticRetryProbeNode(BaseNodePlugin):
    node_type = "automatic_retry_probe"
    calls = 0

    async def execute(self, _ctx: NodeContext) -> NodeResult:
        type(self).calls += 1
        if type(self).calls == 1:
            return NodeResult(status="failed", error="transient upstream error")
        return NodeResult(status="success", outputs={"result": "recovered"})


class _MainTakeoverParentProbeNode(BaseNodePlugin):
    node_type = "main_takeover_parent_probe"
    parent_ids: list[str] = []

    async def execute(self, ctx: NodeContext) -> NodeResult:
        type(self).parent_ids.append(ctx.parent_id)
        return NodeResult(status="success")


def test_main_takeover_routes_nodes_to_owner_without_completing_chat_main(
    tmp_path, monkeypatch,
) -> None:
    monkeypatch.setattr(llm_client_module, "create_llm", lambda **_kwargs: object())
    manager = _manager(tmp_path, monkeypatch)
    workflow_id = "wf-main-takeover-parent"
    task_id = "task-main-takeover-parent"
    owner = SimpleNamespace(
        session_id="main-owner",
        status="running",
        workflow_id="",
        task_id="",
        runtime_scope="interactive",
    )
    manager._session_manager.sessions[owner.session_id] = owner
    definition = WorkflowDef(
        workflow_id=workflow_id,
        nodes=[WorkflowNode(
            id="probe",
            node_type=_MainTakeoverParentProbeNode.node_type,
        )],
    )
    manager._save_task(WorkflowTask(
        workflow_id=workflow_id,
        task_id=task_id,
        status="pending",
        snapshot_definition=definition.to_dict(),
        main_session_id=owner.session_id,
        main_takeover=True,
    ))
    _MainTakeoverParentProbeNode.parent_ids = []
    registry.register(_MainTakeoverParentProbeNode, owner="test-main-takeover-parent")

    async def scenario():
        try:
            started = await manager.run_task(workflow_id, task_id)
            for _ in range(200):
                current = manager._load_task(workflow_id, task_id)
                running = manager._running_tasks.get(task_id)
                if (
                    current is not None
                    and current.status == "completed"
                    and (running is None or running.done())
                ):
                    break
                await asyncio.sleep(0.01)
            return started, manager._load_task(workflow_id, task_id)
        finally:
            await manager.shutdown_task_recovery()
            registry.unregister_owner("test-main-takeover-parent")

    started, completed = asyncio.run(scenario())

    assert started["success"] is True
    assert completed.status == "completed"
    assert _MainTakeoverParentProbeNode.parent_ids == [owner.session_id]
    assert owner.status == "running"


def test_main_takeover_fails_before_start_when_owner_is_not_resident(
    tmp_path, monkeypatch,
) -> None:
    manager = _manager(tmp_path, monkeypatch)
    workflow_id = "wf-main-takeover-missing"
    task_id = "task-main-takeover-missing"
    definition = WorkflowDef(
        workflow_id=workflow_id,
        nodes=[WorkflowNode(id="writer", node_type="agent")],
    )
    manager._save_task(WorkflowTask(
        workflow_id=workflow_id,
        task_id=task_id,
        status="pending",
        snapshot_definition=definition.to_dict(),
        main_session_id="main-missing",
        main_takeover=True,
    ))

    result = asyncio.run(manager.run_task(workflow_id, task_id))
    persisted = manager._load_task(workflow_id, task_id)

    assert result["success"] is False
    assert result["error"] == "main_takeover_unavailable"
    assert persisted.status == "pending"


def test_manager_runs_zero_delay_automatic_retry_end_to_end(
    tmp_path, monkeypatch,
) -> None:
    monkeypatch.setattr(
        llm_client_module,
        "create_llm",
        lambda **_kwargs: object(),
    )
    manager = _manager(tmp_path, monkeypatch)
    workflow_id = "wf-auto-integration"
    task_id = "task-auto-integration"
    definition = WorkflowDef(
        workflow_id=workflow_id,
        nodes=[WorkflowNode(
            id="probe",
            node_type=_AutomaticRetryProbeNode.node_type,
            auto_retry_count=1,
            auto_retry_interval_seconds=0,
        )],
    )
    task = WorkflowTask(
        workflow_id=workflow_id,
        task_id=task_id,
        status="pending",
        snapshot_definition=definition.to_dict(),
    )
    manager._save_task(task)
    _AutomaticRetryProbeNode.calls = 0
    registry.register(_AutomaticRetryProbeNode, owner="test-task-recovery")

    async def scenario():
        try:
            started = await manager.run_task(workflow_id, task_id)
            for _ in range(200):
                current = manager._load_task(workflow_id, task_id)
                running = manager._running_tasks.get(task_id)
                if (
                    current is not None
                    and current.status == "completed"
                    and (running is None or running.done())
                ):
                    break
                await asyncio.sleep(0.01)
            current = manager._load_task(workflow_id, task_id)
            await manager.shutdown_task_recovery()
            return started, current
        finally:
            registry.unregister_owner("test-task-recovery")

    started, completed = asyncio.run(scenario())

    assert started["success"] is True
    assert completed.status == "completed"
    assert _AutomaticRetryProbeNode.calls == 2
    state = completed.node_states["probe"]
    assert state.status == "completed"
    assert state.attempt_count == 2
    assert state.automatic_retry_count == 1
    assert [item["status"] for item in state.attempt_history] == [
        "failed", "completed",
    ]
    assert state.outputs == {"result": "recovered"}


def test_startup_recovery_closes_running_attempt_and_schedules_policy_retry(
    tmp_path, monkeypatch,
) -> None:
    manager = _manager(tmp_path, monkeypatch)
    node = WorkflowNode(
        id="writer",
        label="写作",
        auto_retry_count=2,
        auto_retry_interval_seconds=60,
    )
    task = _save_task(
        manager,
        task_status="running",
        node_status="running",
        node=node,
    )

    async def scenario():
        summary = await manager.recover_workflow_tasks()
        recovered = manager._load_task(task.workflow_id, task.task_id)
        await manager.shutdown_task_recovery()
        return summary, recovered

    summary, recovered = asyncio.run(scenario())

    assert summary == {
        "scanned": 1,
        "resumed": 0,
        "scheduled": 1,
        "failed": 0,
        "errors": 0,
    }
    assert recovered.status == "retry_waiting"
    state = recovered.node_states["writer"]
    assert state.status == "retry_waiting"
    assert state.automatic_retry_count == 2
    assert state.next_attempt_trigger == AUTO_RETRY_TRIGGER
    assert state.next_retry_at is not None
    assert state.attempt_history[-1]["status"] == "failed"
    assert "workflow_process_interrupted" in state.attempt_history[-1]["error"]


def test_startup_recovery_schedules_retry_before_parallel_terminal_failure(
    tmp_path, monkeypatch,
) -> None:
    manager = _manager(tmp_path, monkeypatch)
    definition = WorkflowDef(
        workflow_id="wf-parallel-mixed",
        nodes=[
            WorkflowNode(id="retryable", auto_retry_count=1),
            WorkflowNode(id="terminal"),
        ],
    )
    due_at = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
    task = WorkflowTask(
        task_id="task-parallel-mixed",
        workflow_id=definition.workflow_id,
        status="retry_waiting",
        snapshot_definition=definition.to_dict(),
        node_states={
            "retryable": NodeExecutionState(
                node_id="retryable",
                status="retry_waiting",
                attempt_count=1,
                automatic_retry_count=1,
                next_retry_at=due_at,
                next_attempt_trigger=AUTO_RETRY_TRIGGER,
            ),
            "terminal": NodeExecutionState(
                node_id="terminal",
                status="failed",
                attempt_count=1,
                error="terminal failure",
            ),
        },
    )
    manager._save_task(task)

    async def scenario():
        summary = await manager.recover_workflow_tasks()
        recovered = manager._load_task(task.workflow_id, task.task_id)
        await manager.shutdown_task_recovery()
        return summary, recovered

    summary, recovered = asyncio.run(scenario())

    assert summary["scheduled"] == 1
    assert summary["failed"] == 0
    assert recovered.status == "retry_waiting"
    assert recovered.node_states["retryable"].next_retry_at == due_at
    assert recovered.node_states["terminal"].status == "failed"


@pytest.mark.parametrize("task_status", ["resume_pending", "running"])
def test_startup_recovery_resumes_activated_retry_before_parallel_failure(
    tmp_path, monkeypatch, task_status,
) -> None:
    manager = _manager(tmp_path, monkeypatch)
    definition = WorkflowDef(
        workflow_id=f"wf-activated-mixed-{task_status}",
        nodes=[
            WorkflowNode(id="retryable", auto_retry_count=1),
            WorkflowNode(id="terminal"),
        ],
    )
    task = WorkflowTask(
        task_id=f"task-activated-mixed-{task_status}",
        workflow_id=definition.workflow_id,
        status=task_status,
        snapshot_definition=definition.to_dict(),
        node_states={
            "retryable": NodeExecutionState(
                node_id="retryable",
                status="pending",
                attempt_count=1,
                automatic_retry_count=1,
                next_attempt_trigger=AUTO_RETRY_TRIGGER,
                input_snapshot={"topic": "frozen"},
            ),
            "terminal": NodeExecutionState(
                node_id="terminal",
                status="failed",
                attempt_count=1,
                error="terminal failure",
            ),
        },
    )
    manager._save_task(task)
    launches: list[tuple[str, str]] = []

    async def fake_run(workflow_id: str, task_id: str):
        launches.append((workflow_id, task_id))
        return {"success": True}

    manager.run_task = fake_run
    summary = asyncio.run(manager.recover_workflow_tasks())

    recovered = manager._load_task(task.workflow_id, task.task_id)
    assert summary["resumed"] == 1
    assert summary["failed"] == 0
    assert launches == [(task.workflow_id, task.task_id)]
    assert recovered.status == "resume_pending"
    assert recovered.node_states["retryable"].status == "pending"
    assert recovered.node_states["retryable"].input_snapshot == {
        "topic": "frozen",
    }
    assert recovered.node_states["terminal"].status == "failed"


def test_startup_recovery_marks_unretryable_interrupted_node_failed(
    tmp_path, monkeypatch,
) -> None:
    manager = _manager(tmp_path, monkeypatch)
    task = _save_task(
        manager,
        task_status="running",
        node_status="running",
    )

    summary = asyncio.run(manager.recover_workflow_tasks())

    assert summary["failed"] == 1
    recovered = manager._load_task(task.workflow_id, task.task_id)
    assert recovered.status == "failed"
    assert recovered.node_states["writer"].status == "failed"
    assert "workflow_process_interrupted" in recovered.node_states["writer"].error


def test_startup_recovery_closes_running_subprocess_child(
    tmp_path, monkeypatch,
) -> None:
    manager = _manager(tmp_path, monkeypatch)
    node = WorkflowNode(
        id="writer",
        node_type="subprocess",
        auto_retry_count=2,
        auto_retry_interval_seconds=60,
    )
    task = _save_task(
        manager,
        task_status="running",
        node_status="running",
        node=node,
    )
    saved = manager._load_task(task.workflow_id, task.task_id)
    saved.node_states["writer"].child_states = {
        "completed-child": NodeExecutionState(
            node_id="completed-child",
            status="completed",
            attempt_count=1,
            outputs={"stable": "value"},
        ),
        "running-child": NodeExecutionState(
            node_id="running-child",
            status="running",
            attempt_count=1,
            started_at="2026-07-22T08:00:30+00:00",
            input_snapshot={"topic": "frozen"},
        ),
    }
    manager._save_task(WorkflowTask.from_dict(saved.to_dict()))

    async def scenario():
        summary = await manager.recover_workflow_tasks()
        recovered = manager._load_task(task.workflow_id, task.task_id)
        await manager.shutdown_task_recovery()
        return summary, recovered

    summary, recovered = asyncio.run(scenario())

    assert summary["scheduled"] == 1
    parent_state = recovered.node_states["writer"]
    assert parent_state.status == "retry_waiting"
    assert parent_state.child_states["completed-child"].status == "completed"
    assert parent_state.child_states["completed-child"].outputs == {
        "stable": "value",
    }
    interrupted = parent_state.child_states["running-child"]
    assert interrupted.status == "failed"
    assert interrupted.input_snapshot == {"topic": "frozen"}
    assert interrupted.attempt_history[-1]["status"] == "failed"
    assert "workflow_process_interrupted" in interrupted.error


def test_startup_recovery_closes_waiting_approval_subprocess_child(
    tmp_path, monkeypatch,
) -> None:
    manager = _manager(tmp_path, monkeypatch)
    node = WorkflowNode(
        id="writer",
        node_type="subprocess",
        auto_retry_count=0,
        fail_auto_skip=True,
    )
    task = _save_task(
        manager,
        task_status="running",
        node_status="running",
        node=node,
    )
    saved = manager._load_task(task.workflow_id, task.task_id)
    saved.node_states["writer"].child_states = {
        "approval-child": NodeExecutionState(
            node_id="approval-child",
            status="waiting_approval",
            attempt_count=1,
            started_at="2026-07-22T08:00:30+00:00",
            input_snapshot={"request": "frozen"},
        ),
    }
    manager._save_task(WorkflowTask.from_dict(saved.to_dict()))
    launches = 0

    async def fake_run(_workflow_id: str, _task_id: str):
        nonlocal launches
        launches += 1
        return {"success": True}

    manager.run_task = fake_run

    async def scenario():
        summary = await manager.recover_workflow_tasks()
        recovered = manager._load_task(task.workflow_id, task.task_id)
        await manager.shutdown_task_recovery()
        return summary, recovered

    summary, recovered = asyncio.run(scenario())

    assert summary["resumed"] == 1
    assert launches == 1
    parent_state = recovered.node_states["writer"]
    assert parent_state.status == "pending"
    assert parent_state.is_skipped is False
    assert parent_state.automatic_retry_count == 1
    interrupted = parent_state.child_states[
        "approval-child"
    ]
    assert interrupted.status == "pending"
    assert interrupted.input_snapshot == {"request": "frozen"}
    assert interrupted.attempt_history[-1]["status"] == "failed"
    assert "等待人工审批" in interrupted.attempt_history[-1]["error"]
    assert interrupted.next_attempt_trigger == "recovery_reissue"


def test_startup_recovery_reissues_waiting_approval_without_retry_budget(
    tmp_path, monkeypatch,
) -> None:
    manager = _manager(tmp_path, monkeypatch)
    task = _save_task(
        manager,
        task_status="running",
        node_status="waiting_approval",
    )
    launches = 0

    async def fake_run(workflow_id: str, task_id: str):
        nonlocal launches
        launches += 1
        return {"success": True, "workflow_id": workflow_id, "task_id": task_id}

    manager.run_task = fake_run
    summary = asyncio.run(manager.recover_workflow_tasks())

    recovered = manager._load_task(task.workflow_id, task.task_id)
    state = recovered.node_states["writer"]
    assert summary["resumed"] == 1
    assert launches == 1
    assert state.status == "pending"
    assert state.automatic_retry_count == 1
    assert state.next_attempt_trigger == "recovery_reissue"
    assert state.attempt_history[-1]["status"] == "failed"
    assert "等待人工审批" in state.attempt_history[-1]["error"]


def test_stop_task_cancels_retry_waiting_timer_and_persists_stopped(
    tmp_path, monkeypatch,
) -> None:
    manager = _manager(tmp_path, monkeypatch)
    task = _save_task(
        manager,
        task_status="retry_waiting",
        node_status="retry_waiting",
    )

    async def scenario():
        timer = asyncio.create_task(asyncio.sleep(3600))
        manager._retry_timers[task.task_id] = timer
        result = await manager.stop_task(task.workflow_id, task.task_id)
        await asyncio.sleep(0)
        return result, timer.cancelled()

    result, timer_cancelled = asyncio.run(scenario())

    stopped = manager._load_task(task.workflow_id, task.task_id)
    assert result["success"] is True
    assert timer_cancelled is True
    assert stopped.status == "stopped"
    assert stopped.completed_at is not None


def test_stop_workflow_includes_retry_waiting_tasks(
    tmp_path, monkeypatch,
) -> None:
    manager = _manager(tmp_path, monkeypatch)
    task = _save_task(
        manager,
        task_status="retry_waiting",
        node_status="retry_waiting",
    )

    result = asyncio.run(manager.stop_workflow(task.workflow_id))

    assert result["success"] is True
    assert result["message"] == "已停止 1 个任务"
    assert manager._load_task(task.workflow_id, task.task_id).status == "stopped"


class _RouteManager:
    def __init__(self, result: dict):
        self.result = result

    def get_workflow_execution_policy(self, _workflow_id: str) -> str:
        return "public"

    async def retry_node(self, *_args):
        return deepcopy(self.result)

    async def skip_node(self, *_args):
        return deepcopy(self.result)


def _client(result: dict) -> TestClient:
    app = FastAPI()
    app.state.workflow_manager = _RouteManager(result)
    app.include_router(router)
    return TestClient(app)


def test_node_control_api_maps_state_conflict_to_http_409() -> None:
    response = _client({
        "success": False,
        "error": "node_control_conflict",
        "message": "节点状态为 completed",
        "workflow_id": "wf-demo",
        "task_id": "task-demo",
        "node_id": "writer",
    }).post(
        "/api/workflows/wf-demo/tasks/task-demo/nodes/writer/retry",
        json={"expected_attempt_count": 1},
    )

    assert response.status_code == 409
    assert response.json()["detail"]["error"] == "node_control_conflict"


def test_node_control_api_maps_stale_attempt_to_http_409() -> None:
    response = _client({
        "success": False,
        "error": "node_control_stale",
        "message": "节点已产生新的执行尝试",
        "workflow_id": "wf-demo",
        "task_id": "task-demo",
        "node_id": "writer",
    }).post(
        "/api/workflows/wf-demo/tasks/task-demo/nodes/writer/retry",
        json={"expected_attempt_count": 1},
    )

    assert response.status_code == 409
    assert response.json()["detail"]["error"] == "node_control_stale"


def test_node_control_api_returns_successful_skip() -> None:
    response = _client({
        "success": True,
        "workflow_id": "wf-demo",
        "task_id": "task-demo",
        "node_id": "writer",
        "status": "skipped",
    }).post(
        "/api/workflows/wf-demo/tasks/task-demo/nodes/writer/skip",
        json={"expected_attempt_count": 1},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "skipped"
