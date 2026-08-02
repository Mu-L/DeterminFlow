from __future__ import annotations

import asyncio
from collections import defaultdict
from copy import deepcopy
from types import SimpleNamespace

from src.workflow.definition import (
    ExecutionScheme,
    WorkflowDef,
    WorkflowEdge,
    WorkflowGateway,
    WorkflowNode,
    WorkflowTask,
    WorkflowVariable,
)
from src.workflow.failure_policy import (
    MANUAL_RETRY_TRIGGER,
    prepare_node_retry,
)
from src.workflow.nodes import BaseNodePlugin, NodeContext, NodeResult, registry
from src.workflow.nodes.subprocess import (
    SubprocessNode,
    _SUBPROCESS_SNAPSHOT_KEY,
)
from src.workflow.runtime_models import NodeExecutionState


class _RecoveryProbeNode(BaseNodePlugin):
    node_type = "subprocess_recovery_probe"
    calls: dict[str, int] = defaultdict(int)
    fail_once: set[str] = set()
    inputs: dict[str, list[dict[str, str]]] = defaultdict(list)
    messages: dict[str, list[str]] = defaultdict(list)
    environments: dict[str, list[dict[str, str]]] = defaultdict(list)

    async def execute(self, ctx: NodeContext) -> NodeResult:
        node_id = ctx.node_def.id
        type(self).calls[node_id] += 1
        type(self).inputs[node_id].append(dict(ctx.parameter_values))
        type(self).messages[node_id].append(ctx.node_def.first_message)
        type(self).environments[node_id].append(dict(ctx.owner_environment))
        if node_id in type(self).fail_once:
            type(self).fail_once.remove(node_id)
            return NodeResult(
                status="failed",
                error=f"failed once: {node_id}",
                outputs={"partial": "must-not-leak"},
            )
        return NodeResult(
            status="success",
            summary=f"completed: {node_id}",
            outputs={f"out_{node_id}": node_id},
        )


def _reset_probe(monkeypatch) -> None:
    monkeypatch.setitem(
        registry._plugins,
        _RecoveryProbeNode.node_type,
        _RecoveryProbeNode,
    )
    _RecoveryProbeNode.calls = defaultdict(int)
    _RecoveryProbeNode.fail_once = set()
    _RecoveryProbeNode.inputs = defaultdict(list)
    _RecoveryProbeNode.messages = defaultdict(list)
    _RecoveryProbeNode.environments = defaultdict(list)
    monkeypatch.setattr(
        SubprocessNode,
        "_push_child_node_status",
        staticmethod(lambda *_args: None),
    )


def _parent_definition_and_node(
    *,
    child_workflow_id: str,
    parameter_key: str,
) -> tuple[WorkflowDef, WorkflowNode]:
    node = WorkflowNode(
        id="subflow",
        node_type="subprocess",
        sub_workflow_id=child_workflow_id,
        sub_workflow_params={
            parameter_key: {
                "value": f"{{{{{parameter_key}}}}}",
                "use_default": False,
            },
        },
    )
    definition = WorkflowDef(
        workflow_id="wf-parent",
        nodes=[node],
        variables=[WorkflowVariable(key=parameter_key)],
    )
    return definition, node


def _context(
    *,
    definition: WorkflowDef,
    node: WorkflowNode,
    state: NodeExecutionState,
    parameter_values: dict[str, str],
    checkpoints: list[dict[str, str]],
    owner_environment: dict[str, str] | None = None,
    workflow_environment=None,
) -> NodeContext:
    async def checkpoint() -> None:
        checkpoints.append({
            child_id: child_state.status
            for child_id, child_state in state.child_states.items()
        })

    return NodeContext(
        definition=definition,
        node_def=node,
        node_state=state,
        parameter_values=parameter_values,
        workflow_id=definition.workflow_id,
        task_id="task-parent",
        session_manager=SimpleNamespace(sessions={}),
        checkpoint=checkpoint,
        owner_environment=owner_environment or {},
        workflow_environment=workflow_environment,
    )


def test_subprocess_resolves_environment_from_child_workflow_owner(
    monkeypatch,
) -> None:
    _reset_probe(monkeypatch)
    child = WorkflowDef(
        workflow_id="plugin-b-child",
        nodes=[WorkflowNode(
            id="probe",
            node_type=_RecoveryProbeNode.node_type,
        )],
    )
    parent, parent_node = _parent_definition_and_node(
        child_workflow_id=child.workflow_id,
        parameter_key="topic",
    )
    monkeypatch.setattr(
        SubprocessNode,
        "_load_child_definition",
        staticmethod(lambda _workflow_id: child),
    )
    resolved: list[str] = []

    def resolve_environment(workflow_id: str) -> dict[str, str]:
        resolved.append(workflow_id)
        return {"PLUGIN_B_SETTING": "owned-by-b"}

    for parent_environment in (
        {},
        {"PLUGIN_A_SECRET": "must-not-cross-owner"},  # pragma: allowlist secret
    ):
        result = asyncio.run(SubprocessNode().execute(_context(
            definition=parent,
            node=parent_node,
            state=NodeExecutionState(node_id=parent_node.id),
            parameter_values={"topic": "test"},
            checkpoints=[],
            owner_environment=parent_environment,
            workflow_environment=resolve_environment,
        )))
        assert result.status == "success"

    assert resolved == ["plugin-b-child", "plugin-b-child"]
    assert _RecoveryProbeNode.environments["probe"] == [
        {"PLUGIN_B_SETTING": "owned-by-b"},
        {"PLUGIN_B_SETTING": "owned-by-b"},
    ]


def test_nested_subprocess_re_resolves_each_workflow_owner(
    monkeypatch,
) -> None:
    _reset_probe(monkeypatch)
    grandchild = WorkflowDef(
        workflow_id="plugin-c-child",
        nodes=[WorkflowNode(
            id="probe",
            node_type=_RecoveryProbeNode.node_type,
        )],
    )
    child = WorkflowDef(
        workflow_id="plugin-b-child",
        nodes=[WorkflowNode(
            id="nested",
            node_type="subprocess",
            sub_workflow_id=grandchild.workflow_id,
        )],
    )
    parent_node = WorkflowNode(
        id="subflow",
        node_type="subprocess",
        sub_workflow_id=child.workflow_id,
    )
    parent = WorkflowDef(workflow_id="user-parent", nodes=[parent_node])
    definitions = {
        child.workflow_id: child,
        grandchild.workflow_id: grandchild,
    }
    monkeypatch.setattr(
        SubprocessNode,
        "_load_child_definition",
        staticmethod(lambda workflow_id: definitions[workflow_id]),
    )

    def resolve_environment(workflow_id: str) -> dict[str, str]:
        return {
            "plugin-b-child": {"PLUGIN_B_SETTING": "owned-by-b"},
            "plugin-c-child": {"PLUGIN_C_SETTING": "owned-by-c"},
        }[workflow_id]

    result = asyncio.run(SubprocessNode().execute(_context(
        definition=parent,
        node=parent_node,
        state=NodeExecutionState(node_id=parent_node.id),
        parameter_values={},
        checkpoints=[],
        workflow_environment=resolve_environment,
    )))

    assert result.status == "success"
    assert _RecoveryProbeNode.environments["probe"] == [
        {"PLUGIN_C_SETTING": "owned-by-c"},
    ]


def test_subprocess_retry_skips_completed_child_and_uses_frozen_definition(
    monkeypatch,
) -> None:
    _reset_probe(monkeypatch)
    child = WorkflowDef(
        workflow_id="wf-child",
        nodes=[
            WorkflowNode(
                id="prepare",
                node_type=_RecoveryProbeNode.node_type,
                first_message="prepare-v1",
            ),
            WorkflowNode(
                id="disabled",
                node_type=_RecoveryProbeNode.node_type,
            ),
            WorkflowNode(
                id="finish",
                node_type=_RecoveryProbeNode.node_type,
                first_message="finish-v1",
            ),
        ],
        variables=[WorkflowVariable(key="topic", default="child-default")],
        execution_schemes=[ExecutionScheme(
            id="selected",
            name="selected",
            selected_node_ids=["prepare", "finish"],
        )],
    )
    parent, parent_node = _parent_definition_and_node(
        child_workflow_id=child.workflow_id,
        parameter_key="topic",
    )
    parent_node.sub_scheme_id = "selected"
    monkeypatch.setattr(
        SubprocessNode,
        "_load_child_definition",
        staticmethod(lambda _workflow_id: child),
    )
    _RecoveryProbeNode.fail_once = {"finish"}
    state = NodeExecutionState(node_id=parent_node.id)
    first_checkpoints: list[dict[str, str]] = []

    first = asyncio.run(SubprocessNode().execute(_context(
        definition=parent,
        node=parent_node,
        state=state,
        parameter_values={"topic": "original-topic"},
        checkpoints=first_checkpoints,
    )))

    assert first.status == "failed"
    assert first.outputs == {}
    assert _RecoveryProbeNode.calls == {"prepare": 1, "finish": 1}
    assert state.child_states["prepare"].status == "completed"
    assert state.child_states["prepare"].attempt_count == 1
    assert state.child_states["prepare"].attempt_history[-1]["status"] == "completed"
    assert state.child_states["disabled"].status == "skipped"
    assert state.child_states["finish"].status == "failed"
    assert state.child_states["finish"].attempt_count == 1
    assert state.child_states["finish"].attempt_history[-1]["status"] == "failed"
    assert state.child_states["finish"].input_snapshot["topic"] == "original-topic"
    assert state.child_states["finish"].outputs == {}
    assert any(item.get("prepare") == "running" for item in first_checkpoints)
    assert any(item.get("prepare") == "completed" for item in first_checkpoints)
    assert any(item.get("finish") == "running" for item in first_checkpoints)
    assert any(item.get("finish") == "failed" for item in first_checkpoints)

    snapshot = state.input_snapshot[_SUBPROCESS_SNAPSHOT_KEY]
    assert snapshot["resolved_child_params"] == {"topic": "original-topic"}
    assert snapshot["disabled_node_ids"] == ["disabled"]
    frozen_finish = next(
        item for item in snapshot["child_definition"]["nodes"]
        if item["id"] == "finish"
    )
    assert frozen_finish["first_message"] == "finish-v1"

    state = WorkflowTask.from_dict(WorkflowTask(
        workflow_id=parent.workflow_id,
        node_states={parent_node.id: state},
    ).to_dict()).node_states[parent_node.id]
    state.status = "failed"
    state.error = first.error
    retry_state = prepare_node_retry(
        state,
        trigger=MANUAL_RETRY_TRIGGER,
    )
    child.get_node("finish").first_message = "finish-live-v2"

    def reject_live_definition(_workflow_id: str) -> WorkflowDef:
        raise AssertionError("重试不应读取实时子流程定义")

    monkeypatch.setattr(
        SubprocessNode,
        "_load_child_definition",
        staticmethod(reject_live_definition),
    )
    retry_checkpoints: list[dict[str, str]] = []
    retried = asyncio.run(SubprocessNode().execute(_context(
        definition=parent,
        node=parent_node,
        state=retry_state,
        parameter_values={"topic": "changed-topic"},
        checkpoints=retry_checkpoints,
    )))

    assert retried.status == "success"
    assert _RecoveryProbeNode.calls == {"prepare": 1, "finish": 2}
    assert _RecoveryProbeNode.messages["finish"] == ["finish-v1", "finish-v1"]
    assert [item["topic"] for item in _RecoveryProbeNode.inputs["finish"]] == [
        "original-topic",
        "original-topic",
    ]
    assert retry_state.child_states["finish"].status == "completed"
    assert retry_state.child_states["prepare"].attempt_count == 1
    assert retry_state.child_states["finish"].attempt_count == 2
    assert [
        attempt["status"]
        for attempt in retry_state.child_states["finish"].attempt_history
    ] == ["failed", "completed"]
    assert retry_state.child_states["finish"].input_snapshot["topic"] == "original-topic"
    assert any(item.get("finish") == "running" for item in retry_checkpoints)
    assert any(item.get("finish") == "completed" for item in retry_checkpoints)


def test_subprocess_condition_multi_node_branch_resumes_frozen_choice(
    monkeypatch,
) -> None:
    _reset_probe(monkeypatch)
    child = WorkflowDef(
        workflow_id="wf-child-condition",
        nodes=[
            WorkflowNode(id=node_id, node_type=_RecoveryProbeNode.node_type)
            for node_id in ["seed", "yes_1", "yes_2", "no_1", "join"]
        ],
        edges=[
            WorkflowEdge(source="__start__", target="seed"),
            WorkflowEdge(source="seed", target="choice"),
            WorkflowEdge(
                source="choice",
                target="yes_1",
                condition={
                    "expression": "{{route}} == yes",
                    "is_default": False,
                },
            ),
            WorkflowEdge(
                source="choice",
                target="no_1",
                condition={"expression": "", "is_default": True},
            ),
            WorkflowEdge(source="yes_1", target="yes_2"),
            WorkflowEdge(source="yes_2", target="join"),
            WorkflowEdge(source="no_1", target="join"),
            WorkflowEdge(source="join", target="__end__"),
        ],
        variables=[WorkflowVariable(key="route")],
        gateways=[WorkflowGateway(id="choice", gateway_type="condition")],
    )
    parent, parent_node = _parent_definition_and_node(
        child_workflow_id=child.workflow_id,
        parameter_key="route",
    )
    monkeypatch.setattr(
        SubprocessNode,
        "_load_child_definition",
        staticmethod(lambda _workflow_id: child),
    )
    _RecoveryProbeNode.fail_once = {"join"}
    state = NodeExecutionState(node_id=parent_node.id)

    first = asyncio.run(SubprocessNode().execute(_context(
        definition=parent,
        node=parent_node,
        state=state,
        parameter_values={"route": "yes"},
        checkpoints=[],
    )))

    assert first.status == "failed"
    assert _RecoveryProbeNode.calls == {
        "seed": 1,
        "yes_1": 1,
        "yes_2": 1,
        "join": 1,
    }
    snapshot = state.input_snapshot[_SUBPROCESS_SNAPSHOT_KEY]
    assert snapshot["condition_choices"] == {"choice": "yes_1"}
    assert state.child_states["no_1"].status == "skipped"

    restored_state = WorkflowTask.from_dict(WorkflowTask(
        workflow_id=parent.workflow_id,
        node_states={parent_node.id: state},
    ).to_dict()).node_states[parent_node.id]
    restored_state.status = "failed"
    retry_state = prepare_node_retry(
        restored_state,
        trigger=MANUAL_RETRY_TRIGGER,
    )
    retried = asyncio.run(SubprocessNode().execute(_context(
        definition=parent,
        node=parent_node,
        state=retry_state,
        parameter_values={"route": "no"},
        checkpoints=[],
    )))

    assert retried.status == "success"
    assert _RecoveryProbeNode.calls == {
        "seed": 1,
        "yes_1": 1,
        "yes_2": 1,
        "join": 2,
    }
    assert retry_state.child_states["yes_1"].status == "completed"
    assert retry_state.child_states["yes_2"].status == "completed"
    assert retry_state.child_states["join"].status == "completed"
    assert retry_state.child_states["no_1"].status == "skipped"


def test_subprocess_parallel_gateway_fails_with_diagnostic(monkeypatch) -> None:
    _reset_probe(monkeypatch)
    child = WorkflowDef(
        workflow_id="wf-child-parallel",
        nodes=[WorkflowNode(id="work", node_type=_RecoveryProbeNode.node_type)],
        edges=[
            WorkflowEdge(source="__start__", target="parallel"),
            WorkflowEdge(source="parallel", target="work"),
            WorkflowEdge(source="work", target="__end__"),
        ],
        gateways=[WorkflowGateway(id="parallel", gateway_type="parallel")],
    )
    parent, parent_node = _parent_definition_and_node(
        child_workflow_id=child.workflow_id,
        parameter_key="topic",
    )
    monkeypatch.setattr(
        SubprocessNode,
        "_load_child_definition",
        staticmethod(lambda _workflow_id: deepcopy(child)),
    )

    result = asyncio.run(SubprocessNode().execute(_context(
        definition=parent,
        node=parent_node,
        state=NodeExecutionState(node_id=parent_node.id),
        parameter_values={"topic": "value"},
        checkpoints=[],
    )))

    assert result.status == "failed"
    assert result.error.startswith("subprocess_control_flow_unsupported:")
    assert "parallel" in result.error
    assert _RecoveryProbeNode.calls == {}
