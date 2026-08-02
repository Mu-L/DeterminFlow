from __future__ import annotations

import asyncio
import hashlib
import json
from types import SimpleNamespace

import pytest

import src.config as config
import src.workflow.manager as workflow_manager_module
from src.agent.definition import AgentDefinition
from src.agent.session_manager import SessionManager
from src.core.workspace_manager import WorkspaceManager
from src.session.prompt_builder import PromptBuilder
from src.workflow.definition import (
    NodeExecutionState,
    WorkflowDef,
    WorkflowNode,
    WorkflowVariable,
)
from src.workflow.manager import WorkflowManager
from src.workflow.nodes.agent import AgentNode
from src.workflow.nodes.base import NodeContext
from src.workflow.nodes.script import ScriptNode
from src.workflow.runtime_guards import RUNTIME_GUARD_KEY, RUNTIME_GUARD_SCHEMA
from src.workflow.script_library import (
    ScriptLibraryCatalog,
    ScriptLibraryConflictError,
)
from src.extension_api.registrar import OwnedPath


class _MutablePromptManager:
    def __init__(self):
        self.content = "original prompt"

    def get_sections(self, prompt_template):
        return [{
            "name": prompt_template,
            "content": self.content,
            "enabled": True,
        }]


class _MutableModelManager:
    def __init__(self):
        self.provider_revision = "v1"

    def get_provider(self, provider_id):
        return {
            "provider_id": provider_id,
            "base_url": f"https://{self.provider_revision}.example.test",
            "api_key": "private-test-value",  # pragma: allowlist secret
        }

    def get_default_params(self):
        return {"reasoning_effort": "high", "max_completion_tokens": 32000}

    def get_retry_config(self):
        return {"max_retries": 2}


class _ScriptOnlySessionManager:
    sessions = {}


def _write_workflow(workflows_dir, definition: WorkflowDef) -> None:
    workflow_dir = workflows_dir / definition.workflow_id
    workflow_dir.mkdir(parents=True, exist_ok=True)
    (workflow_dir / "definition.json").write_text(
        json.dumps(definition.to_dict(), ensure_ascii=False),
        encoding="utf-8",
    )


def test_task_freezes_actual_inline_script_and_script_node_rejects_drift(
    tmp_path,
    monkeypatch,
):
    workflows_dir = tmp_path / "workflows"
    workflow_id = "wf-runtime-script-guard"
    node = WorkflowNode(
        id="validate",
        node_type="script",
        node_params={
            "script_source": "inline",
            "script_type": "python",
            "script_name": "validate",
        },
    )
    definition = WorkflowDef(workflow_id=workflow_id, nodes=[node])
    _write_workflow(workflows_dir, definition)
    script_dir = workflows_dir / workflow_id / "script"
    script_dir.mkdir()
    script_path = script_dir / "validate.py"
    original_bytes = b"print('<script_out>original</script_out>')\n"
    script_path.write_bytes(original_bytes)
    monkeypatch.setattr(workflow_manager_module, "WORKFLOWS_DIR", workflows_dir)
    monkeypatch.setattr(config, "WORKFLOWS_DIR", workflows_dir)

    manager = WorkflowManager(_ScriptOnlySessionManager())
    execution_identity = manager.get_workflow_execution_identity(workflow_id)
    created = manager.create_task(workflow_id)

    assert execution_identity == {
        "schema_version": "workflow_execution_identity.v1",
        "workflow_id": workflow_id,
        "definition_version": 1,
        "inline_scripts": [{
            "node_id": node.id,
            "script_name": "validate",
            "script_type": "python",
            "content_sha256": hashlib.sha256(original_bytes).hexdigest(),
        }],
    }
    assert created is not None
    task = manager._load_task(workflow_id, created["task_id"])
    snapshot = WorkflowDef.from_dict(task.snapshot_definition)
    frozen_node = snapshot.get_node(node.id)
    guard = frozen_node.node_params[RUNTIME_GUARD_KEY]
    assert guard == {
        "schema_version": RUNTIME_GUARD_SCHEMA,
        "inline_script_sha256": hashlib.sha256(original_bytes).hexdigest(),
        "inline_script_dependencies": [],
    }

    marker = tmp_path / "should-not-exist"
    script_path.write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).write_text('ran')\n",
        encoding="utf-8",
    )
    assert manager.get_workflow_execution_identity(workflow_id)[
        "inline_scripts"
    ][0]["content_sha256"] != execution_identity["inline_scripts"][0][
        "content_sha256"
    ]
    assert manager.update_task_variables(workflow_id, task.task_id, {})
    task = manager._load_task(workflow_id, task.task_id)
    snapshot = WorkflowDef.from_dict(task.snapshot_definition)
    frozen_node = snapshot.get_node(node.id)
    assert frozen_node.node_params[RUNTIME_GUARD_KEY][
        "inline_script_sha256"
    ] == hashlib.sha256(original_bytes).hexdigest()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    result = asyncio.run(ScriptNode().execute(NodeContext(
        definition=snapshot,
        node_def=frozen_node,
        node_state=NodeExecutionState(node_id=node.id),
        shared_ws=workspace,
        workflow_id=workflow_id,
        task_id=task.task_id,
    )))

    assert result.status == "failed"
    assert "inline script 已漂移" in result.error
    assert not marker.exists()


def test_inline_script_dependency_is_frozen_and_rechecked(
    tmp_path,
    monkeypatch,
):
    workflows_dir = tmp_path / "workflows"
    workflow_id = "wf-runtime-script-dependency"
    node = WorkflowNode(
        id="prepare",
        node_type="script",
        node_params={
            "script_source": "inline",
            "script_type": "python",
            "script_name": "prepare",
            "script_dependencies": ["taxonomy.py"],
        },
    )
    definition = WorkflowDef(workflow_id=workflow_id, nodes=[node])
    _write_workflow(workflows_dir, definition)
    script_dir = workflows_dir / workflow_id / "script"
    script_dir.mkdir()
    script_path = script_dir / "prepare.py"
    dependency_path = script_dir / "taxonomy.py"
    script_path.write_text(
        "print('<script_out>ok</script_out>')\n",
        encoding="utf-8",
    )
    original_dependency = b"VALUE = 1\n"
    dependency_path.write_bytes(original_dependency)
    monkeypatch.setattr(workflow_manager_module, "WORKFLOWS_DIR", workflows_dir)
    monkeypatch.setattr(config, "WORKFLOWS_DIR", workflows_dir)

    manager = WorkflowManager(_ScriptOnlySessionManager())
    identity = manager.get_workflow_execution_identity(workflow_id)
    created = manager.create_task(workflow_id)
    task = manager._load_task(workflow_id, created["task_id"])
    snapshot = WorkflowDef.from_dict(task.snapshot_definition)
    frozen = snapshot.get_node(node.id)
    expected_dependency = {
        "path": "taxonomy.py",
        "content_sha256": hashlib.sha256(original_dependency).hexdigest(),
    }

    assert identity["inline_scripts"][0]["dependencies"] == [
        expected_dependency
    ]
    assert frozen.node_params[RUNTIME_GUARD_KEY][
        "inline_script_dependencies"
    ] == [expected_dependency]

    dependency_path.write_text("VALUE = 2\n", encoding="utf-8")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    result = asyncio.run(
        ScriptNode().execute(
            NodeContext(
                definition=snapshot,
                node_def=frozen,
                node_state=NodeExecutionState(node_id=node.id),
                shared_ws=workspace,
                workflow_id=workflow_id,
                task_id=task.task_id,
            )
        )
    )

    assert result.status == "failed"
    assert "dependency 已漂移" in result.error


def _write_library_script(
    root,
    *,
    group: str = "novel",
    script_name: str = "validate",
    content: bytes = b"print('<script_out>ok</script_out>')\n",
):
    script_dir = root / group / script_name
    script_dir.mkdir(parents=True)
    script_path = script_dir / f"{script_name}.py"
    script_path.write_bytes(content)
    return script_path


def test_script_library_rejects_duplicate_group_and_name(tmp_path):
    user_root = tmp_path / "user-library"
    first_root = tmp_path / "first-library"
    second_root = tmp_path / "second-library"
    _write_library_script(first_root)
    _write_library_script(second_root)

    with pytest.raises(
        ScriptLibraryConflictError,
        match="novel/validate.*first-plugin.*second-plugin",
    ):
        ScriptLibraryCatalog(
            user_root,
            [
                OwnedPath("first-plugin", first_root),
                OwnedPath("second-plugin", second_root),
            ],
        )


def test_library_script_attestation_binds_owner_revision_and_files(tmp_path):
    user_root = tmp_path / "user-library"
    plugin_root = tmp_path / "plugin-library"
    script_path = _write_library_script(plugin_root)
    shared_path = plugin_root / "novel" / "shared.py"
    shared_path.write_bytes(b"VALUE = 1\n")
    catalog = ScriptLibraryCatalog(
        user_root,
        [OwnedPath("novel-workflows", plugin_root)],
        owner_revision=lambda owner: (
            "commit-123:package-sha-456"
            if owner == "novel-workflows"
            else None
        ),
    )

    attestation = catalog.attest("novel", "validate", "python")

    assert attestation["schema_version"] == "script_library_attestation.v1"
    assert attestation["owner"] == "novel-workflows"
    assert attestation["revision"] == "commit-123:package-sha-456"
    assert attestation["group"] == "novel"
    assert attestation["script_name"] == "validate"
    assert attestation["script_type"] == "python"
    assert attestation["entrypoint_sha256"] == hashlib.sha256(
        script_path.read_bytes()
    ).hexdigest()
    assert {item["path"] for item in attestation["files"]} == {
        "shared.py",
        "validate/validate.py",
    }
    assert catalog.verify_attestation(attestation) == attestation

    shared_path.write_bytes(b"VALUE = 2\n")
    with pytest.raises(RuntimeError, match="已漂移"):
        catalog.verify_attestation(attestation)


def test_library_script_task_freezes_and_rechecks_attestation(
    tmp_path,
    monkeypatch,
):
    import src.workflow.script_library as script_library_module

    workflows_dir = tmp_path / "workflows"
    plugin_root = tmp_path / "plugin-library"
    script_path = _write_library_script(plugin_root)
    catalog = ScriptLibraryCatalog(
        tmp_path / "user-library",
        [OwnedPath("novel-workflows", plugin_root)],
        owner_revision=lambda _owner: "commit-123:package-sha-456",
    )
    monkeypatch.setattr(script_library_module, "_catalog", catalog)

    workflow_id = "wf-library-script-guard"
    node = WorkflowNode(
        id="validate",
        node_type="script",
        node_params={
            "script_source": "library",
            "script_type": "python",
            "script_group": "novel",
            "script_name": "validate",
        },
    )
    definition = WorkflowDef(workflow_id=workflow_id, nodes=[node])
    _write_workflow(workflows_dir, definition)
    monkeypatch.setattr(workflow_manager_module, "WORKFLOWS_DIR", workflows_dir)
    monkeypatch.setattr(config, "WORKFLOWS_DIR", workflows_dir)

    manager = WorkflowManager(_ScriptOnlySessionManager())
    created = manager.create_task(workflow_id)
    assert created is not None
    task = manager._load_task(workflow_id, created["task_id"])
    snapshot = WorkflowDef.from_dict(task.snapshot_definition)
    frozen_node = snapshot.get_node(node.id)
    frozen = frozen_node.node_params[RUNTIME_GUARD_KEY][
        "library_script_attestation"
    ]
    assert frozen["owner"] == "novel-workflows"
    assert frozen["revision"] == "commit-123:package-sha-456"

    script_path.write_bytes(
        b"print('<script_out>changed</script_out>')\n"
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    result = asyncio.run(ScriptNode().execute(NodeContext(
        definition=snapshot,
        node_def=frozen_node,
        node_state=NodeExecutionState(node_id=node.id),
        shared_ws=workspace,
        workflow_id=workflow_id,
        task_id=task.task_id,
    )))

    assert result.status == "failed"
    assert "Script Library 已漂移" in result.error


def test_plugin_library_attestation_requires_revision(tmp_path):
    plugin_root = tmp_path / "plugin-library"
    _write_library_script(plugin_root)
    catalog = ScriptLibraryCatalog(
        tmp_path / "user-library",
        [OwnedPath("novel-workflows", plugin_root)],
    )

    with pytest.raises(RuntimeError, match="revision"):
        catalog.attest("novel", "validate", "python")


def test_library_script_execution_without_task_attestation_fails_closed(
    tmp_path,
    monkeypatch,
):
    import src.workflow.script_library as script_library_module

    plugin_root = tmp_path / "plugin-library"
    _write_library_script(plugin_root)
    monkeypatch.setattr(
        script_library_module,
        "_catalog",
        ScriptLibraryCatalog(
            tmp_path / "user-library",
            [OwnedPath("novel-workflows", plugin_root)],
            owner_revision=lambda _owner: "commit-123:package-sha-456",
        ),
    )
    node = WorkflowNode(
        id="validate",
        node_type="script",
        node_params={
            "script_source": "library",
            "script_type": "python",
            "script_group": "novel",
            "script_name": "validate",
        },
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    result = asyncio.run(ScriptNode().execute(NodeContext(
        definition=WorkflowDef(
            workflow_id="wf-unguarded-library",
            nodes=[node],
        ),
        node_def=node,
        node_state=NodeExecutionState(node_id=node.id),
        shared_ws=workspace,
        workflow_id="wf-unguarded-library",
        task_id="task-unguarded",
    )))

    assert result.status == "failed"
    assert "运行身份守卫无效" in result.error


@pytest.mark.parametrize("drift_source", ["agent", "prompt", "model"])
def test_agent_node_rejects_mid_task_execution_identity_drift(
    drift_source,
    tmp_path,
    monkeypatch,
):
    workflows_dir = tmp_path / "workflows"
    workflow_id = f"wf-runtime-agent-{drift_source}"
    agent_definition = AgentDefinition(
        agent_type="test.writer",
        description="runtime guard test",
        prompt_template="test-writer",
        tools=[],
        model="openai:default-model",
        max_turns=8,
        model_params={"reasoning_effort": "high"},
    )
    prompt_manager = _MutablePromptManager()
    model_manager = _MutableModelManager()
    monkeypatch.setattr(
        "src.agent.definition.get_agent_definition",
        lambda agent_type: (
            agent_definition if agent_type == agent_definition.agent_type else None
        ),
    )
    monkeypatch.setattr(
        "src.core.model_manager.get_model_manager",
        lambda: model_manager,
    )
    monkeypatch.setattr(workflow_manager_module, "WORKFLOWS_DIR", workflows_dir)

    session_manager = SessionManager()
    session_manager.set_builders(PromptBuilder(prompt_manager), object())
    node = WorkflowNode(
        id="writer",
        node_type="agent",
        agent_type=agent_definition.agent_type,
        first_message="write",
        model_override="{{model_id}}",
    )
    definition = WorkflowDef(
        workflow_id=workflow_id,
        nodes=[node],
    )
    _write_workflow(workflows_dir, definition)
    manager = WorkflowManager(session_manager)
    created = manager.create_task(
        workflow_id,
        parameter_values={"model_id": "openai:runtime-model"},
    )

    assert created is not None
    task = manager._load_task(workflow_id, created["task_id"])
    snapshot = WorkflowDef.from_dict(task.snapshot_definition)
    frozen_node = snapshot.get_node(node.id)
    guard = frozen_node.node_params[RUNTIME_GUARD_KEY]
    assert guard["schema_version"] == RUNTIME_GUARD_SCHEMA
    assert guard["agent_type"] == agent_definition.agent_type
    assert guard["model_override"] == "openai:runtime-model"
    assert len(guard["effective_agent_definition_sha256"]) == 64

    original_guard_sha256 = guard["effective_agent_definition_sha256"]
    assert manager.update_task_variables(
        workflow_id,
        task.task_id,
        {"model_id": "openai:runtime-model-v2"},
    )
    task = manager._load_task(workflow_id, task.task_id)
    snapshot = WorkflowDef.from_dict(task.snapshot_definition)
    frozen_node = snapshot.get_node(node.id)
    guard = frozen_node.node_params[RUNTIME_GUARD_KEY]
    assert guard["model_override"] == "openai:runtime-model-v2"
    assert guard["effective_agent_definition_sha256"] != original_guard_sha256

    if drift_source == "agent":
        agent_definition.max_turns += 1
    elif drift_source == "prompt":
        prompt_manager.content = "changed prompt"
    else:
        model_manager.provider_revision = "v2"

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    result = asyncio.run(AgentNode().execute(NodeContext(
        definition=snapshot,
        node_def=frozen_node,
        node_state=NodeExecutionState(node_id=node.id),
        shared_ws=workspace,
        parameter_values={"model_id": "openai:runtime-model-v2"},
        workflow_id=workflow_id,
        task_id=task.task_id,
        session_manager=session_manager,
    )))

    assert result.status == "failed"
    assert "运行身份已漂移" in result.error
    assert session_manager.sessions == {}


def test_attached_task_freezes_defaults_after_explicit_parameter_overrides(
    tmp_path,
    monkeypatch,
):
    workflows_dir = tmp_path / "workflows"
    workflow_id = "wf-runtime-attached-defaults"
    agent_definition = AgentDefinition(
        agent_type="test.writer",
        description="runtime guard parameter precedence test",
        prompt_template="test-writer",
        tools=[],
        model="openai:default-model",
    )
    monkeypatch.setattr(
        "src.agent.definition.get_agent_definition",
        lambda agent_type: (
            agent_definition if agent_type == agent_definition.agent_type else None
        ),
    )
    monkeypatch.setattr(
        "src.core.model_manager.get_model_manager",
        lambda: _MutableModelManager(),
    )
    monkeypatch.setattr(workflow_manager_module, "WORKFLOWS_DIR", workflows_dir)

    session_manager = SessionManager()
    session_manager.set_builders(PromptBuilder(_MutablePromptManager()), object())
    session_manager.sessions["session-main"] = SimpleNamespace(
        workflow_id="",
        task_id="",
    )
    definition = WorkflowDef(
        workflow_id=workflow_id,
        nodes=[
            WorkflowNode(
                id="default-writer",
                node_type="agent",
                agent_type=agent_definition.agent_type,
                model_override="{{default_model}}",
            ),
            WorkflowNode(
                id="overridden-writer",
                node_type="agent",
                agent_type=agent_definition.agent_type,
                model_override="{{overridden_model}}",
            ),
        ],
        variables=[
            WorkflowVariable(
                key="default_model",
                default="openai:default-frozen",
            ),
            WorkflowVariable(
                key="overridden_model",
                default="openai:default-overridden",
            ),
        ],
    )
    _write_workflow(workflows_dir, definition)
    manager = WorkflowManager(session_manager)
    manager._ws_manager = WorkspaceManager(
        base_dir=str(tmp_path / "workflow-workspaces")
    )

    created = manager.create_and_attach_task_for_session(
        workflow_id,
        "session-main",
        parameter_values={"overridden_model": "openai:explicit-frozen"},
    )

    assert created["success"] is True
    task = manager._load_task(workflow_id, created["task_id"])
    assert task.parameter_values == {
        "default_model": "openai:default-frozen",
        "overridden_model": "openai:explicit-frozen",
    }
    snapshot = WorkflowDef.from_dict(task.snapshot_definition)
    guards = {
        node.id: node.node_params[RUNTIME_GUARD_KEY]
        for node in snapshot.nodes
    }
    assert guards["default-writer"]["model_override"] == (
        "openai:default-frozen"
    )
    assert guards["overridden-writer"]["model_override"] == (
        "openai:explicit-frozen"
    )


def test_attached_task_guard_failure_persists_no_task_or_workspace(
    tmp_path,
    monkeypatch,
):
    workflows_dir = tmp_path / "workflows"
    workflow_id = "wf-runtime-attached-invalid"
    definition = WorkflowDef(
        workflow_id=workflow_id,
        nodes=[WorkflowNode(
            id="writer",
            node_type="agent",
            agent_type="test.writer",
            model_override="{{missing_model}}",
        )],
    )
    _write_workflow(workflows_dir, definition)
    monkeypatch.setattr(workflow_manager_module, "WORKFLOWS_DIR", workflows_dir)
    session_manager = SessionManager()
    session_manager.sessions["session-main"] = SimpleNamespace(
        workflow_id="",
        task_id="",
    )
    workspace_root = tmp_path / "workflow-workspaces"
    manager = WorkflowManager(session_manager)
    manager._ws_manager = WorkspaceManager(base_dir=str(workspace_root))

    result = manager.create_and_attach_task_for_session(
        workflow_id,
        "session-main",
    )

    assert result["success"] is False
    assert "无法在 Task 创建时冻结动态 model_override" in result["message"]
    tasks_dir = workflows_dir / workflow_id / "tasks"
    assert not tasks_dir.exists()
    assert not (workspace_root / workflow_id).exists()
