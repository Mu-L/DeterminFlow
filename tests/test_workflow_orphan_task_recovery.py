from __future__ import annotations

import asyncio

import src.workflow.manager as workflow_manager_module
from src.workflow.definition import WorkflowTask
from src.workflow.manager import WorkflowManager


class _SessionManager:
    sessions = {}

    def get_session(self, _session_id: str):
        return None


def test_stop_task_terminalizes_persisted_running_task_without_local_runner(
    tmp_path, monkeypatch
):
    workflows_dir = tmp_path / "workflows"
    workflow_id = "wf-restarted"
    task_id = "task-orphaned"
    monkeypatch.setattr(workflow_manager_module, "WORKFLOWS_DIR", workflows_dir)
    before_restart = WorkflowManager(_SessionManager())
    before_restart._save_task(
        WorkflowTask(
            task_id=task_id,
            workflow_id=workflow_id,
            name="进程重启遗留任务",
            status="running",
        )
    )

    after_restart = WorkflowManager(_SessionManager())
    result = asyncio.run(after_restart.stop_task(workflow_id, task_id))

    task = after_restart._load_task(workflow_id, task_id)
    assert result == {
        "success": True,
        "message": "进程重启遗留任务已停止",
        "task_id": task_id,
    }
    assert task is not None
    assert task.status == "stopped"
    assert task.completed_at is not None
