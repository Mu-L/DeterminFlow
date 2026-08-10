from __future__ import annotations

import asyncio
import json
import threading
from collections import defaultdict
from types import SimpleNamespace

import pytest

import src.config as config_module
from src.workflow.definition import (
    NodeExecutionState,
    WorkflowDef,
    WorkflowEdge,
    WorkflowGateway,
    WorkflowNode,
    WorkflowRunRecord,
    WorkflowTask,
)
from src.workflow.engine import WorkflowEngine
from src.workflow.failure_policy import activate_scheduled_retry
from src.workflow.nodes import BaseNodePlugin, NodeContext, NodeResult, registry
from src.workflow.nodes.approval import ApprovalNode


class _RetryProbeNode(BaseNodePlugin):
    node_type = "retry_probe"
    calls: dict[str, int] = defaultdict(int)
    inputs: dict[str, list[dict[str, str]]] = defaultdict(list)
    fail_once: set[tuple[str, str]] = set()
    always_fail: set[str] = set()

    async def execute(self, ctx: NodeContext) -> NodeResult:
        node_id = ctx.node_def.id
        type(self).calls[node_id] += 1
        type(self).inputs[node_id].append(dict(ctx.parameter_values))
        loop_value = ctx.parameter_values.get("item", "")
        fail_key = (node_id, loop_value)
        should_fail_once = (
            fail_key in type(self).fail_once
            and type(self).calls[node_id] == 1
        )
        if node_id in type(self).always_fail or should_fail_once:
            return NodeResult(
                status="failed",
                error=f"failed:{node_id}:{loop_value}",
                outputs={"partial": "must-not-leak"},
            )
        return NodeResult(
            status="success",
            summary=f"ok:{node_id}:{loop_value}",
            outputs={f"out_{node_id}": loop_value or "ok"},
        )


class _ParallelBarrierProbeNode(BaseNodePlugin):
    node_type = "parallel_barrier_probe"
    writer_ids: set[str] = set()
    started_writers: set[str] = set()
    completed_writers: set[str] = set()
    calls: dict[str, int] = defaultdict(int)
    all_started: asyncio.Event | None = None

    async def execute(self, ctx: NodeContext) -> NodeResult:
        node_id = ctx.node_def.id
        type(self).calls[node_id] += 1

        if node_id in type(self).writer_ids:
            type(self).started_writers.add(node_id)
            if type(self).started_writers == type(self).writer_ids:
                assert type(self).all_started is not None
                type(self).all_started.set()
            assert type(self).all_started is not None
            await asyncio.wait_for(type(self).all_started.wait(), timeout=1)
            type(self).completed_writers.add(node_id)

        if node_id == "integrator":
            assert type(self).completed_writers == type(self).writer_ids

        return NodeResult(
            status="success",
            summary=f"ok:{node_id}",
            outputs={f"out_{node_id}": "ok"},
        )


def _engine(monkeypatch) -> WorkflowEngine:
    monkeypatch.setitem(
        registry._plugins, _RetryProbeNode.node_type, _RetryProbeNode,
    )
    _RetryProbeNode.calls = defaultdict(int)
    _RetryProbeNode.inputs = defaultdict(list)
    _RetryProbeNode.fail_once = set()
    _RetryProbeNode.always_fail = set()
    engine = WorkflowEngine(SimpleNamespace(sessions={}, main_session_id=""))

    async def _no_save(*_args, **_kwargs):
        return None

    monkeypatch.setattr(engine, "_save_task_state", _no_save)
    monkeypatch.setattr(engine, "_push_wf_task_update", lambda *_args: None)
    return engine


def _task(definition: WorkflowDef, params: dict[str, str] | None = None) -> WorkflowTask:
    return WorkflowTask(
        workflow_id=definition.workflow_id,
        status="running",
        snapshot_definition=definition.to_dict(),
        parameter_values=params or {},
    )


def _run_sequence(
    engine: WorkflowEngine,
    definition: WorkflowDef,
    task: WorkflowTask,
    node_ids: list[str],
) -> str:
    return asyncio.run(engine._execute_node_sequence(
        definition=definition,
        task=task,
        node_ids=node_ids,
        disabled_ids=set(),
        shared_ws=None,
        parent_id="main",
        on_node_started=lambda _state: None,
        needs_approval=False,
        run_record=WorkflowRunRecord(workflow_id=definition.workflow_id),
    ))


def test_task_checkpoints_are_immutable_and_ordered(tmp_path, monkeypatch):
    monkeypatch.setattr(config_module, "WORKFLOWS_DIR", tmp_path)
    engine = WorkflowEngine(SimpleNamespace(sessions={}, main_session_id=""))
    original_save = engine._do_save_task_state
    first_started = threading.Event()
    release_first = threading.Event()
    payload_statuses: list[str] = []

    def delayed_save(workflow_id, task_id, task_data):
        payload_statuses.append(task_data["node_states"]["worker"]["status"])
        if len(payload_statuses) == 1:
            first_started.set()
            assert release_first.wait(timeout=2)
        original_save(workflow_id, task_id, task_data)

    monkeypatch.setattr(engine, "_do_save_task_state", delayed_save)
    task = WorkflowTask(
        task_id="task-checkpoint-order",
        workflow_id="wf-checkpoint-order",
        node_states={
            "worker": NodeExecutionState(node_id="worker", status="running"),
        },
    )

    async def scenario():
        first = asyncio.create_task(engine._save_task_state(task.workflow_id, task))
        started = await asyncio.to_thread(first_started.wait, 2)
        assert started
        task.node_states["worker"].status = "completed"
        second = asyncio.create_task(engine._save_task_state(task.workflow_id, task))
        await asyncio.sleep(0)
        release_first.set()
        await asyncio.gather(first, second)

    asyncio.run(scenario())

    task_file = (
        tmp_path / task.workflow_id / "tasks" / f"{task.task_id}.json"
    )
    persisted = json.loads(task_file.read_text(encoding="utf-8"))
    assert payload_statuses == ["running", "completed"]
    assert persisted["node_states"]["worker"]["status"] == "completed"


def test_first_running_checkpoint_freezes_attempt_and_input_before_exit(monkeypatch):
    engine = _engine(monkeypatch)
    definition = WorkflowDef(
        workflow_id="wf-first-running-checkpoint",
        nodes=[WorkflowNode(id="worker", node_type="retry_probe")],
    )
    task = _task(definition, {"topic": "must-survive"})
    persisted: list[dict] = []

    async def _crash_after_save(_workflow_id: str, current: WorkflowTask):
        persisted.append(current.to_dict())
        raise RuntimeError("simulated process exit")

    monkeypatch.setattr(engine, "_save_task_state", _crash_after_save)

    with pytest.raises(RuntimeError, match="simulated process exit"):
        _run_sequence(engine, definition, task, ["worker"])

    assert len(persisted) == 1
    running = persisted[0]["node_states"]["worker"]
    assert running["status"] == "running"
    assert running["attempt_count"] == 1
    assert running["input_snapshot"]["topic"] == "must-survive"
    assert _RetryProbeNode.calls == {}


def test_terminal_node_failure_is_persisted_before_sequence_returns(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(config_module, "WORKFLOWS_DIR", tmp_path)
    monkeypatch.setitem(
        registry._plugins, _RetryProbeNode.node_type, _RetryProbeNode,
    )
    _RetryProbeNode.calls = defaultdict(int)
    _RetryProbeNode.inputs = defaultdict(list)
    _RetryProbeNode.fail_once = set()
    _RetryProbeNode.always_fail = {"worker"}

    engine = WorkflowEngine(SimpleNamespace(sessions={}, main_session_id=""))
    monkeypatch.setattr(engine, "_push_wf_task_update", lambda *_args: None)
    definition = WorkflowDef(
        workflow_id="wf-terminal-failure-checkpoint",
        nodes=[WorkflowNode(id="worker", node_type="retry_probe")],
    )
    task = _task(definition, {"topic": "must-survive"})

    assert _run_sequence(engine, definition, task, ["worker"]) == "failed"

    task_file = (
        tmp_path / definition.workflow_id / "tasks" / f"{task.task_id}.json"
    )
    persisted = json.loads(task_file.read_text(encoding="utf-8"))
    failed = persisted["node_states"]["worker"]
    assert failed["status"] == "failed"
    assert failed["error"] == "failed:worker:"
    assert failed["attempt_count"] == 1
    assert failed["attempt_history"][-1]["status"] == "failed"


def test_cancelled_approval_removes_waiter_and_cannot_be_resolved(monkeypatch):
    engine = _engine(monkeypatch)
    node = WorkflowNode(id="approval", node_type="approval", label="审批")
    context = NodeContext(
        definition=WorkflowDef(workflow_id="wf-approval", nodes=[node]),
        node_def=node,
        node_state=NodeExecutionState(node_id=node.id),
        workflow_id="wf-approval",
        task_id="task-approval",
    )
    key = (context.workflow_id, context.task_id, node.id)

    async def scenario():
        running = asyncio.create_task(ApprovalNode().execute(context))
        for _ in range(100):
            if key in engine._pending_approvals:
                break
            await asyncio.sleep(0)
        assert key in engine._pending_approvals
        running.cancel()
        try:
            await running
        except asyncio.CancelledError:
            pass
        return engine.resolve_approval(
            context.workflow_id,
            context.task_id,
            node.id,
            approved=True,
        )

    result = asyncio.run(scenario())

    assert key not in engine._pending_approvals
    assert key not in engine._approval_results
    assert result["success"] is False


def test_approval_waiter_exists_before_required_event_is_emitted(monkeypatch):
    from src.web.event_bus import event_bus

    engine = _engine(monkeypatch)
    node = WorkflowNode(id="approval", node_type="approval", label="审批")
    context = NodeContext(
        definition=WorkflowDef(workflow_id="wf-approval-race", nodes=[node]),
        node_def=node,
        node_state=NodeExecutionState(node_id=node.id),
        workflow_id="wf-approval-race",
        task_id="task-approval-race",
    )
    resolutions: list[dict] = []

    async def emit_event(event: dict):
        if event.get("type") == "wf_approval_required":
            resolutions.append(engine.resolve_approval(
                context.workflow_id,
                context.task_id,
                node.id,
                approved=True,
            ))

    monkeypatch.setattr(event_bus, "emit_event", emit_event)

    async def scenario():
        return await asyncio.wait_for(
            ApprovalNode().execute(context),
            timeout=0.2,
        )

    result = asyncio.run(scenario())

    assert result.status == "success"
    assert len(resolutions) == 1
    assert resolutions[0]["success"] is True
    key = (context.workflow_id, context.task_id, node.id)
    assert key not in engine._pending_approvals
    assert key not in engine._approval_results


def test_auto_retry_reuses_frozen_input_and_skips_completed_upstream(monkeypatch):
    engine = _engine(monkeypatch)
    nodes = [
        WorkflowNode(id="a", node_type="retry_probe"),
        WorkflowNode(
            id="b", node_type="retry_probe",
            auto_retry_count=1, auto_retry_interval_seconds=0,
        ),
    ]
    definition = WorkflowDef(workflow_id="wf-retry", nodes=nodes)
    _RetryProbeNode.fail_once = {("b", "")}
    task = _task(definition, {"input": "frozen"})

    assert _run_sequence(engine, definition, task, ["a", "b"]) == "retry_waiting"
    assert _RetryProbeNode.calls == {"a": 1, "b": 1}
    task.parameter_values["input"] = "changed"
    task.node_states["b"] = activate_scheduled_retry(task.node_states["b"])

    assert _run_sequence(engine, definition, task, ["a", "b"]) == "completed"
    assert _RetryProbeNode.calls == {"a": 1, "b": 2}
    assert [item["input"] for item in _RetryProbeNode.inputs["b"]] == [
        "frozen", "frozen",
    ]
    assert [item["trigger"] for item in task.node_states["b"].attempt_history] == [
        "initial", "auto_retry",
    ]


def test_retry_exhaustion_auto_skips_without_partial_output(monkeypatch):
    engine = _engine(monkeypatch)
    nodes = [
        WorkflowNode(
            id="bad", node_type="retry_probe", auto_retry_count=1,
            fail_auto_skip=True,
        ),
        WorkflowNode(id="after", node_type="retry_probe"),
    ]
    definition = WorkflowDef(workflow_id="wf-skip", nodes=nodes)
    _RetryProbeNode.always_fail = {"bad"}
    task = _task(definition)

    assert _run_sequence(engine, definition, task, ["bad", "after"]) == "retry_waiting"
    task.node_states["bad"] = activate_scheduled_retry(task.node_states["bad"])
    assert _run_sequence(engine, definition, task, ["bad", "after"]) == "completed"

    skipped = task.node_states["bad"]
    assert skipped.status == "skipped"
    assert skipped.outputs == {}
    assert skipped.stdout == ""
    assert skipped.stderr == ""
    assert _RetryProbeNode.calls == {"bad": 2, "after": 1}


def test_parallel_resume_does_not_rerun_completed_branch(monkeypatch):
    engine = _engine(monkeypatch)
    nodes = [
        WorkflowNode(id="left", node_type="retry_probe"),
        WorkflowNode(
            id="right", node_type="retry_probe", auto_retry_count=1,
        ),
    ]
    definition = WorkflowDef(workflow_id="wf-parallel", nodes=nodes)
    _RetryProbeNode.fail_once = {("right", "")}
    task = _task(definition)
    branches = [{"nodes": ["left"]}, {"nodes": ["right"]}]

    async def _run() -> str:
        return await engine._execute_parallel_branches(
            definition, task, branches, {}, set(), None, "main",
            lambda _state: None, False,
            WorkflowRunRecord(workflow_id=definition.workflow_id),
        )

    assert asyncio.run(_run()) == "retry_waiting"
    task.node_states["right"] = activate_scheduled_retry(task.node_states["right"])
    assert asyncio.run(_run()) == "completed"
    assert _RetryProbeNode.calls == {"left": 1, "right": 2}


def test_parallel_retry_waiting_takes_priority_over_terminal_failure(monkeypatch):
    engine = _engine(monkeypatch)
    nodes = [
        WorkflowNode(
            id="retryable",
            node_type="retry_probe",
            auto_retry_count=1,
        ),
        WorkflowNode(id="terminal", node_type="retry_probe"),
    ]
    definition = WorkflowDef(workflow_id="wf-parallel-mixed", nodes=nodes)
    _RetryProbeNode.fail_once = {("retryable", "")}
    _RetryProbeNode.always_fail = {"terminal"}
    task = _task(definition)
    branches = [{"nodes": ["retryable"]}, {"nodes": ["terminal"]}]

    async def _run() -> str:
        return await engine._execute_parallel_branches(
            definition, task, branches, {}, set(), None, "main",
            lambda _state: None, False,
            WorkflowRunRecord(workflow_id=definition.workflow_id),
        )

    assert asyncio.run(_run()) == "retry_waiting"
    assert task.node_states["terminal"].status == "failed"
    task.node_states["retryable"] = activate_scheduled_retry(
        task.node_states["retryable"]
    )
    assert asyncio.run(_run()) == "failed"
    assert _RetryProbeNode.calls == {"retryable": 2, "terminal": 1}


def test_condition_choice_is_frozen_and_complete_branch_resumes(monkeypatch):
    engine = _engine(monkeypatch)
    nodes = [
        WorkflowNode(id="yes_1", node_type="retry_probe"),
        WorkflowNode(
            id="yes_2", node_type="retry_probe", auto_retry_count=1,
        ),
        WorkflowNode(id="no_1", node_type="retry_probe"),
        WorkflowNode(id="join", node_type="retry_probe"),
    ]
    definition = WorkflowDef(
        workflow_id="wf-condition",
        nodes=nodes,
        edges=[
            WorkflowEdge(source="yes_1", target="yes_2"),
            WorkflowEdge(source="yes_2", target="join"),
            WorkflowEdge(source="no_1", target="join"),
        ],
    )
    definition._rebuild_caches()
    _RetryProbeNode.fail_once = {("yes_2", "")}
    task = _task(definition, {"score": "1"})
    step = {
        "gateway_id": "choice",
        "convergence_node_id": "join",
        "branches": [
            {"target": "yes_1", "condition": {"expression": "{{score}} > 0"}},
            {"target": "no_1", "condition": {"is_default": True}},
        ],
    }

    async def _run() -> str:
        return await engine._evaluate_condition_gateway(
            definition, task, step, set(), None, "main",
            lambda _state: None, False,
            WorkflowRunRecord(workflow_id=definition.workflow_id),
        )

    assert asyncio.run(_run()) == "retry_waiting"
    task.parameter_values["score"] = "-1"
    task.node_states["yes_2"] = activate_scheduled_retry(
        task.node_states["yes_2"],
    )
    assert asyncio.run(_run()) == "completed"
    assert task.control_flow_state["conditions"]["choice"]["selected_target"] == "yes_1"
    assert task.node_states["no_1"].status == "skipped"
    assert _RetryProbeNode.calls == {"yes_1": 1, "yes_2": 2}


def test_plan_segment_runs_default_condition_branch_then_common_tail(monkeypatch):
    engine = _engine(monkeypatch)
    definition = WorkflowDef(
        workflow_id="wf-condition-default",
        nodes=[
            WorkflowNode(id="multi", node_type="retry_probe"),
            WorkflowNode(id="single", node_type="retry_probe"),
            WorkflowNode(id="join", node_type="retry_probe"),
        ],
        gateways=[WorkflowGateway(id="choice", gateway_type="condition")],
        edges=[
            WorkflowEdge(source="__start__", target="choice"),
            WorkflowEdge(
                source="choice",
                target="multi",
                condition={"expression": "{{writer_type}} == multi"},
            ),
            WorkflowEdge(
                source="choice",
                target="single",
                condition={"is_default": True},
            ),
            WorkflowEdge(source="multi", target="join"),
            WorkflowEdge(source="single", target="join"),
            WorkflowEdge(source="join", target="__end__"),
        ],
    )
    definition._rebuild_caches()
    task = _task(definition, {"writer_type": "single"})

    async def _run() -> str:
        return await engine._execute_plan_segment(
            definition=definition,
            task=task,
            execution_plan=definition.get_execution_plan(),
            disabled_ids=set(),
            shared_ws=None,
            parent_id="main",
            on_node_started=lambda _state: None,
            needs_approval=False,
            run_record=WorkflowRunRecord(workflow_id=definition.workflow_id),
        )

    assert asyncio.run(_run()) == "completed"
    assert _RetryProbeNode.calls == {"single": 1, "join": 1}
    assert task.node_states["multi"].status == "skipped"


def test_condition_branch_preserves_nested_parallel_gateway(monkeypatch):
    engine = _engine(monkeypatch)
    monkeypatch.setitem(
        registry._plugins,
        _ParallelBarrierProbeNode.node_type,
        _ParallelBarrierProbeNode,
    )
    writer_ids = {f"writer_{index}" for index in range(5)}
    _ParallelBarrierProbeNode.writer_ids = writer_ids
    _ParallelBarrierProbeNode.started_writers = set()
    _ParallelBarrierProbeNode.completed_writers = set()
    _ParallelBarrierProbeNode.calls = defaultdict(int)

    definition = WorkflowDef(
        workflow_id="wf-condition-nested-parallel",
        nodes=[
            WorkflowNode(id="skeleton", node_type="retry_probe"),
            *[
                WorkflowNode(
                    id=node_id,
                    node_type=_ParallelBarrierProbeNode.node_type,
                )
                for node_id in sorted(writer_ids)
            ],
            WorkflowNode(
                id="integrator",
                node_type=_ParallelBarrierProbeNode.node_type,
            ),
            WorkflowNode(id="single_writer", node_type="retry_probe"),
            WorkflowNode(id="common_tail", node_type="retry_probe"),
        ],
        gateways=[
            WorkflowGateway(id="writer_choice", gateway_type="condition"),
            WorkflowGateway(
                id="writer_fork",
                gateway_type="parallel",
                converge_gateway_id="writer_merge",
            ),
            WorkflowGateway(id="writer_merge", gateway_type="converge"),
        ],
        edges=[
            WorkflowEdge(source="__start__", target="writer_choice"),
            WorkflowEdge(
                source="writer_choice",
                target="skeleton",
                condition={"expression": "{{writer_type}} == multi"},
            ),
            WorkflowEdge(
                source="writer_choice",
                target="single_writer",
                condition={"is_default": True},
            ),
            WorkflowEdge(source="skeleton", target="writer_fork"),
            *[
                WorkflowEdge(source="writer_fork", target=node_id)
                for node_id in sorted(writer_ids)
            ],
            *[
                WorkflowEdge(source=node_id, target="writer_merge")
                for node_id in sorted(writer_ids)
            ],
            WorkflowEdge(source="writer_merge", target="integrator"),
            WorkflowEdge(source="integrator", target="common_tail"),
            WorkflowEdge(source="single_writer", target="common_tail"),
            WorkflowEdge(source="common_tail", target="__end__"),
        ],
    )
    definition._rebuild_caches()
    task = _task(definition, {"writer_type": "multi"})

    async def _run() -> str:
        _ParallelBarrierProbeNode.all_started = asyncio.Event()
        return await engine._execute_plan_segment(
            definition=definition,
            task=task,
            execution_plan=definition.get_execution_plan(),
            disabled_ids=set(),
            shared_ws=None,
            parent_id="main",
            on_node_started=lambda _state: None,
            needs_approval=False,
            run_record=WorkflowRunRecord(workflow_id=definition.workflow_id),
        )

    assert asyncio.run(_run()) == "completed"
    assert _ParallelBarrierProbeNode.started_writers == writer_ids
    assert _ParallelBarrierProbeNode.completed_writers == writer_ids
    assert _ParallelBarrierProbeNode.calls == {
        **{node_id: 1 for node_id in writer_ids},
        "integrator": 1,
    }
    assert _RetryProbeNode.calls == {"skeleton": 1, "common_tail": 1}
    assert task.node_states["single_writer"].status == "skipped"
    assert task.node_states["writer_merge"].status == "completed"
    merged_summary = task.node_states["writer_merge"].summary
    assert all(node_id in merged_summary for node_id in writer_ids)
    condition_state = task.control_flow_state["conditions"]["writer_choice"]
    assert condition_state["selected_target"] == "skeleton"
    assert "branch_nodes" not in condition_state


def test_condition_nested_parallel_resume_keeps_choice_and_completed_writers(
    monkeypatch,
):
    engine = _engine(monkeypatch)
    definition = WorkflowDef(
        workflow_id="wf-condition-nested-parallel-resume",
        nodes=[
            WorkflowNode(id="skeleton", node_type="retry_probe"),
            WorkflowNode(id="writer_a", node_type="retry_probe"),
            WorkflowNode(
                id="writer_b",
                node_type="retry_probe",
                auto_retry_count=1,
            ),
            WorkflowNode(id="integrator", node_type="retry_probe"),
            WorkflowNode(id="single_writer", node_type="retry_probe"),
            WorkflowNode(id="common_tail", node_type="retry_probe"),
        ],
        gateways=[
            WorkflowGateway(id="writer_choice", gateway_type="condition"),
            WorkflowGateway(
                id="writer_fork",
                gateway_type="parallel",
                converge_gateway_id="writer_merge",
            ),
            WorkflowGateway(id="writer_merge", gateway_type="converge"),
        ],
        edges=[
            WorkflowEdge(source="__start__", target="writer_choice"),
            WorkflowEdge(
                source="writer_choice",
                target="skeleton",
                condition={"expression": "{{writer_type}} == multi"},
            ),
            WorkflowEdge(
                source="writer_choice",
                target="single_writer",
                condition={"is_default": True},
            ),
            WorkflowEdge(source="skeleton", target="writer_fork"),
            WorkflowEdge(source="writer_fork", target="writer_a"),
            WorkflowEdge(source="writer_fork", target="writer_b"),
            WorkflowEdge(source="writer_a", target="writer_merge"),
            WorkflowEdge(source="writer_b", target="writer_merge"),
            WorkflowEdge(source="writer_merge", target="integrator"),
            WorkflowEdge(source="integrator", target="common_tail"),
            WorkflowEdge(source="single_writer", target="common_tail"),
            WorkflowEdge(source="common_tail", target="__end__"),
        ],
    )
    definition._rebuild_caches()
    task = _task(definition, {"writer_type": "multi"})
    _RetryProbeNode.fail_once = {("writer_b", "")}

    async def _run() -> str:
        return await engine._execute_plan_segment(
            definition=definition,
            task=task,
            execution_plan=definition.get_execution_plan(),
            disabled_ids=set(),
            shared_ws=None,
            parent_id="main",
            on_node_started=lambda _state: None,
            needs_approval=False,
            run_record=WorkflowRunRecord(workflow_id=definition.workflow_id),
        )

    assert asyncio.run(_run()) == "retry_waiting"
    assert _RetryProbeNode.calls == {
        "skeleton": 1,
        "writer_a": 1,
        "writer_b": 1,
    }
    task.parameter_values["writer_type"] = "single"
    task.node_states["writer_b"] = activate_scheduled_retry(
        task.node_states["writer_b"],
    )

    assert asyncio.run(_run()) == "completed"
    assert task.control_flow_state["conditions"]["writer_choice"][
        "selected_target"
    ] == "skeleton"
    assert _RetryProbeNode.calls == {
        "skeleton": 1,
        "writer_a": 1,
        "writer_b": 2,
        "integrator": 1,
        "common_tail": 1,
    }
    assert task.node_states["single_writer"].status == "skipped"


def test_loop_resume_continues_current_iteration_without_rerunning_prefix(monkeypatch):
    engine = _engine(monkeypatch)
    nodes = [
        WorkflowNode(id="first", node_type="retry_probe"),
        WorkflowNode(
            id="second", node_type="retry_probe", auto_retry_count=1,
        ),
        WorkflowNode(id="done", node_type="retry_probe"),
    ]
    definition = WorkflowDef(
        workflow_id="wf-loop",
        nodes=nodes,
        edges=[
            WorkflowEdge(
                source="loop", target="first",
                condition={"expression": "for item in items"},
            ),
            WorkflowEdge(source="first", target="second"),
            WorkflowEdge(source="second", target="loop"),
            WorkflowEdge(
                source="loop", target="done",
                condition={"is_default": True},
            ),
        ],
    )
    definition._rebuild_caches()
    task = _task(definition, {"items": json.dumps([0, 1, 2, 3])})

    # 该插件按节点全局次数判断 fail_once；second 的第 3 次对应 item=2。
    original_execute = _RetryProbeNode.execute

    async def _fail_item_two(self, ctx: NodeContext) -> NodeResult:
        if ctx.node_def.id == "second" and ctx.parameter_values.get("item") == "2":
            key = (ctx.node_def.id, "2")
            if key not in _RetryProbeNode.fail_once:
                _RetryProbeNode.fail_once.add(key)
                _RetryProbeNode.calls[ctx.node_def.id] += 1
                _RetryProbeNode.inputs[ctx.node_def.id].append(dict(ctx.parameter_values))
                return NodeResult(status="failed", error="item 2 failed")
        return await original_execute(self, ctx)

    monkeypatch.setattr(_RetryProbeNode, "execute", _fail_item_two)
    step = {
        "gateway_id": "loop",
        "loop_body_nodes": ["first", "second"],
        "continue_target": "first",
        "exit_target": "done",
    }

    async def _run() -> str:
        return await engine._execute_loop_gateway(
            definition, task, step, set(), None, "main",
            lambda _state: None, False,
            WorkflowRunRecord(workflow_id=definition.workflow_id),
        )

    first_outcome = asyncio.run(_run())
    assert first_outcome == "retry_waiting", {
        "calls": dict(_RetryProbeNode.calls),
        "inputs": dict(_RetryProbeNode.inputs),
        "states": {
            key: value.status for key, value in task.node_states.items()
        },
        "cursor": task.control_flow_state,
    }
    assert task.control_flow_state["loops"]["loop"]["active_index"] == 2
    assert _RetryProbeNode.calls["first"] == 3
    task.node_states["second"] = activate_scheduled_retry(
        task.node_states["second"],
    )
    assert asyncio.run(_run()) == "completed"
    assert _RetryProbeNode.calls["first"] == 4
    assert _RetryProbeNode.calls["second"] == 5
    assert [
        item["item"] for item in _RetryProbeNode.inputs["second"]
    ] == ["0", "1", "2", "2", "3"]
    assert _RetryProbeNode.calls["done"] == 1
