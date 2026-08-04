from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

import src.config as config
from src.web.workflow_routes import (
    PreStartRequest,
    WorkflowCreateRequest,
    WorkflowUpdateRequest,
    _ensure_http_mutation_allowed,
    create_workflow as create_workflow_route,
    pre_start_workflow,
    stop_task,
    stop_workflow,
)
from src.workflow.definition import NodeExecutionState, WorkflowDef, WorkflowNode
from src.workflow.nodes import registry
from src.workflow.nodes.base import BaseNodePlugin, NodeContext, NodeResult
from src.workflow.nodes.subprocess import SubprocessNode


class FakeManager:
    def __init__(self, policy: str):
        self.policy = policy

    def get_workflow(self, workflow_id: str):
        return {
            "definition": {
                "workflow_id": workflow_id,
                "http_execution_policy": self.policy,
            }
        }

    def get_workflow_execution_policy(self, workflow_id: str):
        return self.policy

    async def stop_workflow(self, workflow_id: str):
        raise AssertionError("internal-only workflow stop must not reach manager")

    async def stop_task(self, workflow_id: str, task_id: str):
        raise AssertionError("internal-only task stop must not reach manager")


def _request(policy: str):
    return SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(workflow_manager=FakeManager(policy))
        )
    )


def test_internal_only_workflow_rejects_raw_http_mutation():
    with pytest.raises(HTTPException) as captured:
        _ensure_http_mutation_allowed(_request("internal_only"), "wf-sensitive")

    assert captured.value.status_code == 403
    assert captured.value.detail["error"] == "workflow_internal_only"


def test_public_workflow_keeps_existing_lan_ui_mutation_path():
    manager = _ensure_http_mutation_allowed(_request("public"), "wf-user")

    assert isinstance(manager, FakeManager)


def test_pre_start_forwards_explicit_main_takeover():
    class PreStartManager(FakeManager):
        def __init__(self):
            super().__init__("public")
            self.calls = []

        async def pre_start_task(self, workflow_id: str, **kwargs):
            self.calls.append((workflow_id, kwargs))
            return {
                "success": True,
                "task_id": "task-1",
                "session_id": "main-1",
                "main_takeover": kwargs["main_takeover"],
            }

    manager = PreStartManager()
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(workflow_manager=manager),
        ),
    )

    result = asyncio.run(pre_start_workflow(
        "wf-user",
        request,
        PreStartRequest(main_takeover=True),
    ))

    assert result["main_takeover"] is True
    assert manager.calls == [("wf-user", {
        "workspace_override": None,
        "main_takeover": True,
    })]
    assert PreStartRequest().main_takeover is False


def test_unknown_http_execution_policy_fails_closed():
    with pytest.raises(HTTPException) as captured:
        _ensure_http_mutation_allowed(_request("unexpected"), "wf-corrupt")

    assert captured.value.status_code == 403
    assert captured.value.detail["error"] == (
        "workflow_execution_policy_invalid"
    )


def test_unavailable_http_execution_policy_fails_closed():
    with pytest.raises(HTTPException) as captured:
        _ensure_http_mutation_allowed(_request("unavailable"), "wf-corrupt")

    assert captured.value.status_code == 403
    assert captured.value.detail["error"] == "workflow_execution_policy_invalid"


@pytest.mark.parametrize(
    "operation",
    (
        lambda request: stop_workflow("wf-sensitive", request),
        lambda request: stop_task("wf-sensitive", "task-1", request),
    ),
)
def test_internal_only_policy_also_guards_stop_endpoints(operation):
    with pytest.raises(HTTPException) as captured:
        asyncio.run(operation(_request("internal_only")))

    assert captured.value.status_code == 403


def test_http_execution_policy_round_trips_and_validates():
    internal = WorkflowDef.from_dict(
        {"workflow_id": "wf-sensitive", "http_execution_policy": "internal_only"}
    )
    invalid = WorkflowDef.from_dict(
        {"workflow_id": "wf-invalid", "http_execution_policy": "unknown"}
    )

    assert internal.to_dict()["http_execution_policy"] == "internal_only"
    assert invalid.validate() == [
        "http_execution_policy 必须是 public 或 internal_only"
    ]


@pytest.mark.parametrize(
    "request_model", (WorkflowCreateRequest, WorkflowUpdateRequest)
)
def test_http_definition_requests_cannot_self_grant_internal_only_policy(
    request_model,
):
    with pytest.raises(ValidationError, match="public"):
        request_model(
            name="privilege escalation",
            http_execution_policy="internal_only",
        )

    assert request_model(name="ordinary").model_dump()[
        "http_execution_policy"
    ] == "public"


def test_create_workflow_rejects_invalid_retry_policy_before_persisting():
    class ValidatingManager(FakeManager):
        def __init__(self):
            super().__init__("public")
            self.create_calls = 0

        def validate_workflow(self, data: dict) -> dict:
            errors = WorkflowDef.from_dict(data).validate()
            return {"valid": not errors, "errors": errors}

        def create_workflow(self, _data: dict) -> dict:
            self.create_calls += 1
            raise AssertionError("非法定义不应持久化")

    manager = ValidatingManager()
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(workflow_manager=manager),
        ),
    )
    body = WorkflowCreateRequest(
        name="invalid retry workflow",
        nodes=[{
            "id": "writer",
            "node_type": "agent",
            "auto_retry_count": 1,
            "auto_retry_interval_seconds": {"invalid": True},
        }],
    )

    with pytest.raises(HTTPException) as captured:
        asyncio.run(create_workflow_route(request, body))

    assert captured.value.status_code == 400
    assert "自动重试间隔" in str(captured.value.detail)
    assert manager.create_calls == 0


class _PolicyProbeNode(BaseNodePlugin):
    node_type = "policy_probe"
    calls = 0

    async def execute(self, ctx: NodeContext) -> NodeResult:
        del ctx
        type(self).calls += 1
        return NodeResult(status="success", summary="probe executed")


def _write_workflow(workflows_dir, definition: WorkflowDef) -> None:
    target = workflows_dir / definition.workflow_id / "definition.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(definition.to_dict(), ensure_ascii=False),
        encoding="utf-8",
    )


def _probe_definition(workflow_id: str, policy: str) -> WorkflowDef:
    return WorkflowDef(
        workflow_id=workflow_id,
        http_execution_policy=policy,
        nodes=[WorkflowNode(id="probe", node_type="policy_probe")],
    )


def _run_subprocess(parent_policy: str, child_workflow_id: str) -> NodeResult:
    node = WorkflowNode(
        id="subflow",
        node_type="subprocess",
        sub_workflow_id=child_workflow_id,
    )
    parent = WorkflowDef(
        workflow_id="wf-parent",
        http_execution_policy=parent_policy,
        nodes=[node],
    )
    return asyncio.run(SubprocessNode().execute(NodeContext(
        definition=parent,
        node_def=node,
        node_state=NodeExecutionState(node_id=node.id),
        workflow_id=parent.workflow_id,
        task_id="task-policy",
        session_manager=SimpleNamespace(sessions={}),
    )))


@pytest.fixture
def subprocess_policy_runtime(tmp_path, monkeypatch):
    workflows_dir = tmp_path / "workflows"
    workflows_dir.mkdir()
    monkeypatch.setattr(config, "WORKFLOWS_DIR", workflows_dir)
    monkeypatch.setitem(registry._plugins, _PolicyProbeNode.node_type, _PolicyProbeNode)
    monkeypatch.setattr(
        SubprocessNode,
        "_push_child_node_status",
        staticmethod(lambda *_args: None),
    )
    _PolicyProbeNode.calls = 0
    return workflows_dir


def test_public_parent_cannot_inline_internal_only_child(
    subprocess_policy_runtime,
):
    _write_workflow(
        subprocess_policy_runtime,
        _probe_definition("wf-internal-child", "internal_only"),
    )

    result = _run_subprocess("public", "wf-internal-child")

    assert result.status == "failed"
    assert result.error == (
        "workflow_internal_only: public 工作流 wf-parent 不能执行 "
        "internal_only 子流程 wf-internal-child"
    )
    assert _PolicyProbeNode.calls == 0


def test_public_parent_can_inline_public_child(subprocess_policy_runtime):
    _write_workflow(
        subprocess_policy_runtime,
        _probe_definition("wf-public-child", "public"),
    )

    result = _run_subprocess("public", "wf-public-child")

    assert result.status == "success"
    assert _PolicyProbeNode.calls == 1


def test_internal_parent_can_inline_internal_only_child(
    subprocess_policy_runtime,
):
    _write_workflow(
        subprocess_policy_runtime,
        _probe_definition("wf-internal-child", "internal_only"),
    )

    result = _run_subprocess("internal_only", "wf-internal-child")

    assert result.status == "success"
    assert _PolicyProbeNode.calls == 1


def test_nested_public_wrapper_cannot_reach_internal_grandchild(
    subprocess_policy_runtime,
):
    internal_leaf = _probe_definition("wf-internal-leaf", "internal_only")
    public_wrapper = WorkflowDef(
        workflow_id="wf-public-wrapper",
        http_execution_policy="public",
        nodes=[WorkflowNode(
            id="nested_subflow",
            node_type="subprocess",
            sub_workflow_id=internal_leaf.workflow_id,
        )],
    )
    _write_workflow(subprocess_policy_runtime, internal_leaf)
    _write_workflow(subprocess_policy_runtime, public_wrapper)

    result = _run_subprocess("public", public_wrapper.workflow_id)

    assert result.status == "failed"
    assert "wf-public-wrapper" in result.error
    assert "wf-internal-leaf" in result.error
    assert _PolicyProbeNode.calls == 0


def test_subprocess_rejects_unknown_child_policy_fail_closed(
    subprocess_policy_runtime,
):
    _write_workflow(
        subprocess_policy_runtime,
        _probe_definition("wf-invalid-child", "unexpected"),
    )

    result = _run_subprocess("internal_only", "wf-invalid-child")

    assert result.status == "failed"
    assert result.error == (
        "workflow_execution_policy_invalid: 子流程 wf-invalid-child "
        "的 http_execution_policy 无效"
    )
    assert _PolicyProbeNode.calls == 0
