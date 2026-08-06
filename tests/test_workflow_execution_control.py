from __future__ import annotations

import asyncio
import json
from pathlib import Path

import src.workflow.manager as workflow_manager_module
from src.workflow.definition import WorkflowTask
from src.workflow.execution_control import ExecutionControl
from src.workflow.manager import WorkflowManager
from src.workflow.runtime import WorkflowRuntimeFacade


class _SessionManager:
    def __init__(self) -> None:
        self.sessions: dict = {}

    def get_session(self, _session_id: str):
        return None


def _manager(tmp_path: Path, monkeypatch) -> WorkflowManager:
    data_dir = tmp_path / "data"
    workflows_dir = data_dir / "workflows"
    monkeypatch.setattr(workflow_manager_module, "DATA_DIR", data_dir)
    monkeypatch.setattr(workflow_manager_module, "WORKFLOWS_DIR", workflows_dir)
    return WorkflowManager(_SessionManager())


def test_missing_state_defaults_to_normal_and_invalid_state_fails_closed(
    tmp_path: Path,
) -> None:
    control = ExecutionControl(tmp_path / "data")

    default = control.read()
    assert default["mode"] == "normal"
    assert default["accepting_new_tasks"] is True
    assert default["source"] == "default"

    control.path.parent.mkdir(parents=True)
    control.path.write_text("not-json", encoding="utf-8")

    invalid = control.read()
    assert invalid["mode"] == "draining"
    assert invalid["accepting_new_tasks"] is False
    assert invalid["state_valid"] is False
    assert invalid["reason"] == "invalid_control_state"


def test_control_writes_atomically_and_activity_is_fail_closed(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    workflows_dir = data_dir / "workflows"
    tasks_dir = workflows_dir / "wf-one" / "tasks"
    tasks_dir.mkdir(parents=True)
    (tasks_dir / "running.json").write_text(
        json.dumps({"status": "running"}), encoding="utf-8",
    )
    (tasks_dir / "done.json").write_text(
        json.dumps({"status": "completed"}), encoding="utf-8",
    )
    (tasks_dir / "broken.json").write_text("{", encoding="utf-8")
    control = ExecutionControl(data_dir, workflows_dir)

    drained = control.write(
        "draining", reason="release:test", retry_after_seconds=45,
    )
    activity = control.activity()

    assert drained["mode"] == "draining"
    assert drained["retry_after_seconds"] == 45
    assert control.path.stat().st_mode & 0o777 == 0o600
    assert activity["active_task_count"] == 1
    assert activity["active_status_counts"]["running"] == 1
    assert activity["unreadable_task_files"] == 1
    assert activity["quiescent"] is False

    resumed = control.write("normal", reason="release:complete")
    assert resumed["accepting_new_tasks"] is True


def test_manager_blocks_new_task_creation_and_existing_pending_start_while_draining(
    tmp_path: Path, monkeypatch,
) -> None:
    manager = _manager(tmp_path, monkeypatch)
    workflow_id = "wf-drain"
    created = manager.create_workflow({
        "workflow_id": workflow_id,
        "name": "Drain test",
        "nodes": [],
        "edges": [],
    })
    assert created["definition"]["workflow_id"] == workflow_id
    pending = manager.create_task(workflow_id)
    assert pending is not None
    manager._execution_control.write("draining", reason="release:test")

    blocked = asyncio.run(manager.run_task(workflow_id, pending["task_id"]))
    blocked_create = manager.create_task(workflow_id)

    assert blocked["success"] is False
    assert blocked["error"] == "runtime_draining"
    assert blocked_create is not None
    assert blocked_create["error"] == "runtime_draining"
    assert manager.get_task(workflow_id, pending["task_id"])["status"] == "pending"
    before = list((tmp_path / "data" / "workflows" / workflow_id / "tasks").glob("*.json"))
    compat = asyncio.run(manager.create_and_run_task(workflow_id))
    after = list((tmp_path / "data" / "workflows" / workflow_id / "tasks").glob("*.json"))
    assert compat["error"] == "runtime_draining"
    assert after == before


def test_resume_pending_task_and_facade_status_remain_available(
    tmp_path: Path, monkeypatch,
) -> None:
    manager = _manager(tmp_path, monkeypatch)
    workflow_id = "wf-resume"
    definition = {
        "workflow_id": workflow_id,
        "name": "Resume test",
        "nodes": [],
        "edges": [],
    }
    assert manager.create_workflow(definition)["definition"]["workflow_id"] == workflow_id
    task = WorkflowTask(
        task_id="task-resume",
        workflow_id=workflow_id,
        name="Resume",
        status="resume_pending",
        snapshot_definition=definition,
    )
    manager._save_task(task)
    manager._execution_control.write("draining", reason="release:test")

    async def run_and_wait() -> dict:
        result = await manager.run_task(workflow_id, task.task_id)
        await manager._running_tasks[task.task_id]
        return result

    result = asyncio.run(run_and_wait())
    facade = WorkflowRuntimeFacade(manager)

    assert result["success"] is True
    assert facade.get_execution_control()["mode"] == "draining"
