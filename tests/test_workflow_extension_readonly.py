from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import src.workflow.manager as workflow_manager_module
from src.web.workflow_routes import (
    ScriptContentRequest,
    delete_script,
    update_script_content,
)
from src.workflow.definition import WorkflowDef, WorkflowTask, WorkflowVariable
from src.workflow.manager import WorkflowManager


class _SessionManager:
    def __init__(self):
        self.sessions = {}

    def get_session(self, _session_id: str):
        return None


class _ExtensionManager:
    def __init__(self, disabled_owner: str):
        self.disabled_owner = disabled_owner

    def workflow_environment(self, _workflow_dir: Path) -> dict[str, str]:
        return {}

    def workflow_owner_enabled(self, workflow_dir: Path) -> bool:
        marker_path = workflow_dir / ".extension.json"
        if not marker_path.exists():
            return True
        owner = json.loads(marker_path.read_text(encoding="utf-8")).get("owner")
        return owner != self.disabled_owner


def test_disabled_extension_workflow_history_is_read_only(tmp_path, monkeypatch):
    workflows_dir = tmp_path / "workflows"
    workflow_id = "wf-extension-history"
    workflow_dir = workflows_dir / workflow_id
    tasks_dir = workflow_dir / "tasks"
    tasks_dir.mkdir(parents=True)
    monkeypatch.setattr(workflow_manager_module, "WORKFLOWS_DIR", workflows_dir)

    definition = WorkflowDef(
        workflow_id=workflow_id,
        name="扩展历史工作流",
        variables=[WorkflowVariable(key="topic", name="主题")],
    )
    definition_file = workflow_dir / "definition.json"
    definition_file.write_text(
        json.dumps(definition.to_dict(), ensure_ascii=False),
        encoding="utf-8",
    )
    (workflow_dir / ".extension.json").write_text(
        json.dumps({"owner": "disabled-extension"}),
        encoding="utf-8",
    )
    task = WorkflowTask(
        task_id="task-history",
        workflow_id=workflow_id,
        name="历史任务",
        status="completed",
        snapshot_definition=None,
        snapshot_variables=[variable.to_dict() for variable in definition.variables],
        parameter_values={"topic": "原始值"},
    )
    task_file = tasks_dir / f"{task.task_id}.json"
    task_file.write_text(
        json.dumps(task.to_dict(), ensure_ascii=False),
        encoding="utf-8",
    )

    manager = WorkflowManager(
        _SessionManager(),
        extension_manager=_ExtensionManager("disabled-extension"),
    )

    assert manager.get_workflow(workflow_id) is None
    assert manager.get_workflow_execution_policy(workflow_id) == "public"
    assert manager.list_workflows() == []
    assert manager.list_tasks(workflow_id)["tasks"][0]["task_id"] == task.task_id
    history = manager.get_task_with_definition(workflow_id, task.task_id)
    assert history is not None
    assert history["task"]["parameter_values"] == {"topic": "原始值"}
    assert history["definition"]["name"] == "扩展历史工作流"

    original_task_content = task_file.read_text(encoding="utf-8")
    original_definition_content = definition_file.read_text(encoding="utf-8")

    async def assert_writes_are_blocked():
        assert manager.create_task(workflow_id) is None
        assert not (await manager.run_task(workflow_id, task.task_id))["success"]
        assert not (await manager.create_and_run_task(workflow_id))["success"]
        assert not (await manager.stop_task(workflow_id, task.task_id))["success"]
        assert not (await manager.stop_workflow(workflow_id))["success"]
        assert not (await manager.pre_start_task(workflow_id))["success"]
        assert not (await manager.start_pre_running_task(workflow_id, task.task_id))["success"]

    asyncio.run(assert_writes_are_blocked())

    assert manager.update_task_variables(
        workflow_id, task.task_id, {"topic": "新值"},
    ) is False
    assert not manager.create_workflow({
        "workflow_id": workflow_id,
        "name": "尝试覆盖扩展工作流",
    })["success"]
    assert not manager.set_workflow_variable(
        workflow_id, task.task_id, "topic", "新值",
    )["success"]
    assert not manager.create_and_attach_task_for_session(
        workflow_id, "session-main",
    )["success"]
    assert not manager.approve_node(
        workflow_id, task.task_id, "approval", True,
    )["success"]
    assert manager.delete_workflow(workflow_id) is False

    script_dir = workflow_dir / "script"
    script_dir.mkdir()
    script_file = script_dir / "owned.py"
    script_file.write_text("original = True\n", encoding="utf-8")
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(workflow_manager=manager),
        ),
    )
    with pytest.raises(HTTPException) as update_error:
        asyncio.run(update_script_content(
            workflow_id,
            "owned",
            request,
            ScriptContentRequest(content="changed = True\n"),
            type="python",
        ))
    assert update_error.value.status_code == 409
    with pytest.raises(HTTPException) as delete_error:
        asyncio.run(delete_script(workflow_id, "owned", request, type="python"))
    assert delete_error.value.status_code == 409

    assert task_file.read_text(encoding="utf-8") == original_task_content
    assert definition_file.read_text(encoding="utf-8") == original_definition_content
    assert script_file.read_text(encoding="utf-8") == "original = True\n"
    assert workflow_dir.exists()


def test_create_workflow_never_overwrites_existing_directory(tmp_path, monkeypatch):
    workflows_dir = tmp_path / "workflows"
    workflow_id = "wf-existing-public"
    workflow_dir = workflows_dir / workflow_id
    workflow_dir.mkdir(parents=True)
    monkeypatch.setattr(workflow_manager_module, "WORKFLOWS_DIR", workflows_dir)
    original = WorkflowDef(workflow_id=workflow_id, name="原定义").to_dict()
    definition_path = workflow_dir / "definition.json"
    definition_path.write_text(json.dumps(original), encoding="utf-8")
    manager = WorkflowManager(_SessionManager())

    result = manager.create_workflow({
        "workflow_id": workflow_id,
        "name": "覆盖尝试",
    })

    assert result["success"] is False
    assert result["error"] == "workflow_already_exists"
    assert json.loads(definition_path.read_text(encoding="utf-8")) == original


def test_stop_task_discards_pending_task_file(tmp_path, monkeypatch):
    workflows_dir = tmp_path / "workflows"
    workflow_id = "wf-pending"
    task_id = "task-pending"
    task_path = workflows_dir / workflow_id / "tasks" / f"{task_id}.json"
    task_path.parent.mkdir(parents=True)
    monkeypatch.setattr(workflow_manager_module, "WORKFLOWS_DIR", workflows_dir)
    WorkflowManager(_SessionManager())._save_task(WorkflowTask(
        task_id=task_id,
        workflow_id=workflow_id,
        name="未启动",
        status="pending",
    ))
    manager = WorkflowManager(_SessionManager())

    result = asyncio.run(manager.stop_task(workflow_id, task_id))

    assert result["success"] is True
    assert not task_path.exists()


def test_stop_task_preserves_concurrent_completed_status(tmp_path, monkeypatch):
    workflows_dir = tmp_path / "workflows"
    workflow_id = "wf-stop-race"
    task_id = "task-stop-race"
    monkeypatch.setattr(workflow_manager_module, "WORKFLOWS_DIR", workflows_dir)
    manager = WorkflowManager(_SessionManager())
    manager._save_task(WorkflowTask(
        task_id=task_id,
        workflow_id=workflow_id,
        name="并发完成任务",
        status="running",
    ))

    async def scenario():
        async def completes_while_stopping():
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                task = manager._load_task(workflow_id, task_id)
                task.status = "completed"
                manager._save_task(task)

        running = asyncio.create_task(completes_while_stopping())
        await asyncio.sleep(0)
        manager._running_tasks[task_id] = running
        result = await manager.stop_task(workflow_id, task_id)
        return result

    result = asyncio.run(scenario())

    assert result["success"] is True
    assert manager._load_task(workflow_id, task_id).status == "completed"


@pytest.mark.parametrize(
    ("stored_workflow_id", "stored_task_id"),
    [
        ("wf-other", "task-identity"),
        ("wf-identity", "task-other"),
    ],
)
def test_load_task_rejects_persisted_identity_mismatch(
    tmp_path,
    monkeypatch,
    stored_workflow_id,
    stored_task_id,
):
    workflows_dir = tmp_path / "workflows"
    workflow_id = "wf-identity"
    task_id = "task-identity"
    task_path = workflows_dir / workflow_id / "tasks" / f"{task_id}.json"
    task_path.parent.mkdir(parents=True)
    monkeypatch.setattr(workflow_manager_module, "WORKFLOWS_DIR", workflows_dir)
    task_path.write_text(
        json.dumps(
            WorkflowTask(
                task_id=stored_task_id,
                workflow_id=stored_workflow_id,
                name="身份漂移任务",
            ).to_dict()
        ),
        encoding="utf-8",
    )

    assert WorkflowManager(_SessionManager())._load_task(workflow_id, task_id) is None
