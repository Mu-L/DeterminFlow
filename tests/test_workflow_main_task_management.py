from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import src.workflow.manager as workflow_manager_module
from src.core.workspace_manager import WorkspaceManager
from src.session.context import set_session_context
from src.web.event_bus import event_bus
from src.workflow.definition import WorkflowDef, WorkflowNode, WorkflowVariable
from src.workflow.manager import WorkflowManager
from src.workflow.main_node_control_tools import (
    create_retry_node_tool,
    create_skip_node_tool,
)
from src.workflow.prompt_injector import build_workflow_summary_for_approval
from src.workflow.runtime_models import NodeExecutionState, WorkflowTask
from src.workflow.tools import (
    create_get_task_status_tool,
    create_set_workflow_variable_tool,
    create_stop_task_tool,
)


def _invoke(tool, **kwargs) -> dict:
    assert tool.coroutine is not None
    return json.loads(asyncio.run(tool.coroutine(**kwargs)))


class _MainToolManager:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []
        self.tasks = {
            ("wf-a", "task-a"): {
                "task_id": "task-a",
                "workflow_id": "wf-a",
                "name": "A",
                "status": "failed",
                "main_session_id": "main-1",
                "node_states": {
                    "node-a": {
                        "status": "failed",
                        "summary": "draft",
                        "error": "invalid",
                        "attempt_count": 2,
                        "automatic_retry_count": 1,
                        "available_actions": ["retry", "skip"],
                    }
                },
                "snapshot_definition": {"nodes": [{"id": "node-a"}]},
                "disabled_node_ids": [],
            },
            ("wf-b", "task-b"): {
                "task_id": "task-b",
                "workflow_id": "wf-b",
                "name": "B",
                "status": "pre_running",
                "main_session_id": "main-1",
                "node_states": {},
                "snapshot_definition": {"nodes": []},
                "disabled_node_ids": [],
            },
            ("wf-a", "task-other"): {
                "task_id": "task-other",
                "workflow_id": "wf-a",
                "name": "Other",
                "status": "running",
                "main_session_id": "main-2",
                "node_states": {},
            },
        }

    def get_workflow(self, workflow_id: str):
        if workflow_id not in {"wf-a", "wf-b"}:
            return None
        return {
            "definition": {
                "workflow_id": workflow_id,
                "name": workflow_id,
                "http_execution_policy": "public",
                "nodes": [],
                "edges": [],
                "variables": [],
            }
        }

    def get_task(self, workflow_id: str, task_id: str):
        return self.tasks.get((workflow_id, task_id))

    def set_workflow_variable(self, **kwargs):
        self.calls.append(("set_workflow_variable", kwargs))
        return {"success": True, **kwargs}

    async def retry_node(self, **kwargs):
        self.calls.append(("retry_node", kwargs))
        return {"success": True, **kwargs}

    async def skip_node(self, **kwargs):
        self.calls.append(("skip_node", kwargs))
        return {"success": True, **kwargs}


@pytest.fixture
def main_tool_context():
    manager = _MainToolManager()
    sessions = SimpleNamespace(sessions={
        "main-1": SimpleNamespace(workflow_id="wf-b", task_id="task-b"),
    })
    set_session_context(session_id="main-1")
    return manager, sessions


def test_explicit_task_ref_controls_a_task_other_than_recent_binding(main_tool_context):
    manager, sessions = main_tool_context
    result = _invoke(
        create_set_workflow_variable_tool(manager, sessions),
        workflow_id="wf-a",
        task_id="task-a",
        key="topic",
        value="海洋文明",
    )

    assert result["success"] is True
    assert manager.calls == [("set_workflow_variable", {
        "workflow_id": "wf-a",
        "task_id": "task-a",
        "key": "topic",
        "value": "海洋文明",
        "session_id": "main-1",
    })]


def test_partial_task_ref_and_cross_main_control_fail_closed(main_tool_context):
    manager, sessions = main_tool_context
    tool = create_set_workflow_variable_tool(manager, sessions)

    partial = _invoke(
        tool,
        workflow_id="wf-a",
        key="topic",
        value="x",
    )
    foreign = _invoke(
        tool,
        workflow_id="wf-a",
        task_id="task-other",
        key="topic",
        value="x",
    )

    assert partial["error"] == "task_ref_incomplete"
    assert foreign["error"] == "task_not_owned"
    assert manager.calls == []


def test_every_task_control_schema_exposes_complete_task_ref(main_tool_context):
    manager, sessions = main_tool_context
    tools = [
        create_set_workflow_variable_tool(manager, sessions),
        create_get_task_status_tool(manager, sessions),
        create_stop_task_tool(manager, sessions),
        create_retry_node_tool(manager, sessions),
        create_skip_node_tool(manager, sessions),
    ]

    for tool in tools:
        fields = tool.args_schema.model_fields
        assert "workflow_id" in fields
        assert "task_id" in fields


def test_status_exposes_recovery_fields_and_node_controls_keep_cas(main_tool_context):
    manager, sessions = main_tool_context
    status = _invoke(
        create_get_task_status_tool(manager, sessions),
        workflow_id="wf-a",
        task_id="task-a",
    )
    retried = _invoke(
        create_retry_node_tool(manager, sessions),
        workflow_id="wf-a",
        task_id="task-a",
        node_id="node-a",
        expected_attempt_count=2,
    )
    skipped = _invoke(
        create_skip_node_tool(manager, sessions),
        workflow_id="wf-a",
        task_id="task-a",
        node_id="node-a",
        expected_attempt_count=2,
    )

    node = status["node_states"]["node-a"]
    assert status["progress"] == {"completed": 0, "total": 1}
    assert node["error"] == "invalid"
    assert node["attempt_count"] == 2
    assert node["available_actions"] == ["retry", "skip"]
    assert retried["expected_attempt_count"] == 2
    assert skipped["expected_attempt_count"] == 2


def test_main_task_workspaces_are_isolated_or_explicitly_named_shared(tmp_path):
    manager = WorkspaceManager(base_dir=str(tmp_path / "workspaces"))

    isolated_a = manager.create_main_task_workspace("main-1", "task-a")
    isolated_b = manager.create_main_task_workspace("main-1", "task-b")
    shared_a = manager.create_main_task_workspace(
        "main-1", "task-a", mode="named_shared", workspace_ref="novel-1",
    )
    shared_b = manager.create_main_task_workspace(
        "main-1", "task-b", mode="named_shared", workspace_ref="novel-1",
    )

    assert isolated_a != isolated_b
    assert shared_a == shared_b
    assert isolated_a.is_relative_to(tmp_path / "workspaces" / "_main" / "main-1")


def test_one_main_can_create_and_list_multiple_persisted_tasks(tmp_path, monkeypatch):
    workflows_dir = tmp_path / "workflows"
    workflow_id = "wf-multi"
    workflow_dir = workflows_dir / workflow_id
    workflow_dir.mkdir(parents=True)
    (workflow_dir / "definition.json").write_text(
        json.dumps(WorkflowDef(workflow_id=workflow_id, name="multi").to_dict()),
        encoding="utf-8",
    )
    monkeypatch.setattr(workflow_manager_module, "WORKFLOWS_DIR", workflows_dir)
    monkeypatch.setattr("src.workflow.task_queries.WORKFLOWS_DIR", workflows_dir)
    sessions = SimpleNamespace(sessions={
        "main-1": SimpleNamespace(workflow_id="", task_id=""),
    })
    manager = WorkflowManager(sessions)
    manager._ws_manager = WorkspaceManager(base_dir=str(tmp_path / "workspaces"))

    first = manager.create_and_attach_task_for_session(workflow_id, "main-1")
    second = manager.create_and_attach_task_for_session(
        workflow_id,
        "main-1",
        main_takeover=True,
    )
    listed = manager.list_all_tasks(main_session_id="main-1")

    assert first["success"] is True
    assert second["success"] is True
    assert first["task_id"] != second["task_id"]
    assert {item["task_id"] for item in listed["tasks"]} == {
        first["task_id"], second["task_id"],
    }
    first_task = manager._load_task(workflow_id, first["task_id"])
    second_task = manager._load_task(workflow_id, second["task_id"])
    assert first_task is not None and second_task is not None
    assert first_task.main_takeover is False
    assert second_task.main_takeover is True
    assert first_task.workspace_mode == second_task.workspace_mode == "task_isolated"
    assert first_task.workspace_override != second_task.workspace_override
    assert sessions.sessions["main-1"].task_id == second["task_id"]


def test_workflow_task_main_takeover_defaults_off_and_round_trips():
    legacy = WorkflowTask.from_dict({
        "task_id": "task-legacy",
        "workflow_id": "wf-legacy",
        "main_session_id": "main-1",
    })
    takeover = WorkflowTask(
        task_id="task-takeover",
        workflow_id="wf-takeover",
        main_session_id="main-1",
        main_takeover=True,
    )

    assert legacy.main_takeover is False
    assert legacy.to_dict()["main_takeover"] is False
    assert WorkflowTask.from_dict(takeover.to_dict()).main_takeover is True


def test_legacy_agent_waiting_approval_preserves_main_takeover():
    restored = WorkflowTask.from_dict({
        "task_id": "task-waiting",
        "workflow_id": "wf-waiting",
        "main_session_id": "main-1",
        "snapshot_definition": {
            "nodes": [{"id": "writer", "node_type": "agent"}],
        },
        "node_states": {
            "writer": {
                "node_id": "writer",
                "status": "waiting_approval",
            },
        },
    })
    explicit_approval = WorkflowTask.from_dict({
        "task_id": "task-explicit-approval",
        "workflow_id": "wf-explicit-approval",
        "main_session_id": "main-1",
        "snapshot_definition": {
            "nodes": [{"id": "review", "node_type": "approval"}],
        },
        "node_states": {
            "review": {
                "node_id": "review",
                "status": "waiting_approval",
            },
        },
    })

    assert restored.main_takeover is True
    assert explicit_approval.main_takeover is False


def test_main_task_creation_rejects_unknown_parameters_nodes_and_schemes(
    tmp_path,
    monkeypatch,
):
    workflows_dir = tmp_path / "workflows"
    workflow_id = "wf-validated"
    workflow_dir = workflows_dir / workflow_id
    workflow_dir.mkdir(parents=True)
    definition = WorkflowDef(
        workflow_id=workflow_id,
        name="validated",
        nodes=[WorkflowNode(id="draft", label="草稿")],
        variables=[WorkflowVariable(key="topic", name="主题")],
    )
    (workflow_dir / "definition.json").write_text(
        json.dumps(definition.to_dict()),
        encoding="utf-8",
    )
    monkeypatch.setattr(workflow_manager_module, "WORKFLOWS_DIR", workflows_dir)
    sessions = SimpleNamespace(sessions={
        "main-1": SimpleNamespace(workflow_id="", task_id=""),
    })
    manager = WorkflowManager(sessions)
    manager._ws_manager = WorkspaceManager(base_dir=str(tmp_path / "workspaces"))

    unknown_parameter = manager.create_and_attach_task_for_session(
        workflow_id,
        "main-1",
        parameter_values={"missing": "value"},
    )
    unknown_node = manager.create_and_attach_task_for_session(
        workflow_id,
        "main-1",
        selected_node_ids=["missing"],
    )
    unknown_scheme = manager.create_and_attach_task_for_session(
        workflow_id,
        "main-1",
        scheme_id="missing",
    )

    assert unknown_parameter["error"] == "workflow_parameter_unknown"
    assert unknown_node["error"] == "workflow_node_unknown"
    assert unknown_scheme["error"] == "workflow_scheme_not_found"
    assert manager.list_all_tasks(main_session_id="main-1")["tasks"] == []


def test_task_result_only_exposes_artifacts_inside_task_workspace(tmp_path, monkeypatch):
    workflows_dir = tmp_path / "workflows"
    workflow_id = "wf-result"
    workflow_dir = workflows_dir / workflow_id
    workflow_dir.mkdir(parents=True)
    (workflow_dir / "definition.json").write_text(
        json.dumps(WorkflowDef(workflow_id=workflow_id).to_dict()),
        encoding="utf-8",
    )
    monkeypatch.setattr(workflow_manager_module, "WORKFLOWS_DIR", workflows_dir)
    manager = WorkflowManager(SimpleNamespace(sessions={}))
    manager._ws_manager = WorkspaceManager(base_dir=str(tmp_path / "workspaces"))
    workspace = manager._ws_manager.create_main_task_workspace("main-1", "task-result")
    artifact = workspace / "chapter.md"
    artifact.write_text("chapter", encoding="utf-8")
    outside = tmp_path / "secret.txt"
    outside.write_text("secret", encoding="utf-8")
    task = WorkflowTask(
        task_id="task-result",
        workflow_id=workflow_id,
        name="result",
        status="completed",
        main_session_id="main-1",
        workspace_override=str(workspace),
        workspace_mode="task_isolated",
        snapshot_definition={"nodes": [{"id": "write"}]},
        node_states={
            "write": NodeExecutionState(
                node_id="write",
                status="completed",
                summary="done",
                outputs={
                    "chapter": "第一章",
                    "_output_file": str(artifact),
                    "_json_output_file": str(outside),
                },
            )
        },
    )
    manager._save_task(task)

    result = manager.get_task_result(workflow_id, task.task_id)

    assert result is not None
    assert result["terminal"] is True
    assert result["nodes"]["write"]["outputs"] == {"chapter": "第一章"}
    assert [item["relative_path"] for item in result["artifacts"]] == ["chapter.md"]
    artifact_ref = result["artifacts"][0]["artifact_ref"]
    content = manager.read_task_artifact(
        workflow_id,
        task.task_id,
        artifact_ref,
        offset=1,
        limit=3,
    )
    assert content is not None
    assert content["success"] is True
    assert content["content"] == "hap"
    assert content["truncated"] is True


def test_task_event_is_sent_to_global_and_owning_main_channels(monkeypatch):
    async def scenario():
        emit_event = AsyncMock()
        emit_chat = AsyncMock()
        monkeypatch.setattr(event_bus, "emit_event", emit_event)
        monkeypatch.setattr(event_bus, "emit_chat", emit_chat)
        manager = WorkflowManager(SimpleNamespace(sessions={}))
        task = WorkflowTask(
            task_id="task-event",
            workflow_id="wf-event",
            status="running",
            main_session_id="main-1",
            main_takeover=True,
            snapshot_definition={"nodes": [{"id": "plan"}]},
        )

        manager._engine._push_wf_task_update("wf-event", task)
        await asyncio.sleep(0)

        global_payload = emit_event.await_args.args[0]
        chat_payload = emit_chat.await_args.args[0]
        assert global_payload["type"] == "wf_task_update"
        assert chat_payload["type"] == "workflow_task_update"
        assert chat_payload["session_id"] == "main-1"
        assert global_payload["main_takeover"] is True
        assert chat_payload["main_takeover"] is True
        assert chat_payload["progress"] == {"completed": 0, "total": 1}

    asyncio.run(scenario())


def test_approval_prompt_carries_complete_task_ref_and_attempt_count():
    definition = WorkflowDef(
        workflow_id="wf-approval",
        nodes=[WorkflowNode(id="draft", label="草稿")],
    )

    prompt = build_workflow_summary_for_approval(
        definition,
        "task-approval",
        "draft",
        "done",
        3,
    )

    assert 'workflow_id="wf-approval"' in prompt
    assert 'task_id="task-approval"' in prompt
    assert "expected_attempt_count=3" in prompt
