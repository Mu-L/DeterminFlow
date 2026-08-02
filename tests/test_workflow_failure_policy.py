from __future__ import annotations

from copy import deepcopy
from dataclasses import replace

import pytest

from src.workflow.definition import WorkflowDef, WorkflowNode
from src.workflow.failure_policy import (
    AUTO_RETRY_TRIGGER,
    MANUAL_RETRY_TRIGGER,
    activate_scheduled_retry,
    apply_failure_skip,
    automatic_retries_remaining,
    begin_node_attempt,
    can_auto_retry,
    normalize_node_status,
    prepare_node_retry,
    record_attempt,
    should_auto_skip,
)
from src.workflow.runtime_models import (
    NodeExecutionState,
    WorkflowTask,
    _node_state_from_dict,
    _node_state_to_dict,
)


def test_workflow_node_failure_policy_defaults_and_round_trip() -> None:
    legacy = WorkflowNode.from_dict({"id": "writer", "label": "写作"})

    assert legacy.auto_retry_count == 0
    assert legacy.auto_retry_interval_seconds == 0
    assert legacy.fail_auto_skip is False

    configured = WorkflowNode.from_dict({
        "id": "writer",
        "auto_retry_count": "3",
        "auto_retry_interval_seconds": "15",
        "fail_auto_skip": True,
    })
    serialized = configured.to_dict()

    assert serialized["auto_retry_count"] == 3
    assert serialized["auto_retry_interval_seconds"] == 15
    assert serialized["fail_auto_skip"] is True


@pytest.mark.parametrize(
    ("field", "value", "expected_fragment"),
    [
        ("auto_retry_count", -1, "自动重试次数"),
        ("auto_retry_count", 21, "自动重试次数"),
        ("auto_retry_count", True, "自动重试次数"),
        ("auto_retry_count", "three", "自动重试次数"),
        ("auto_retry_interval_seconds", -1, "自动重试间隔"),
        ("auto_retry_interval_seconds", 86_401, "自动重试间隔"),
        ("auto_retry_interval_seconds", False, "自动重试间隔"),
        ("auto_retry_interval_seconds", "soon", "自动重试间隔"),
    ],
)
def test_workflow_node_failure_policy_validation_rejects_invalid_values(
    field: str,
    value: object,
    expected_fragment: str,
) -> None:
    node = WorkflowNode(id="writer", label="写作")
    setattr(node, field, value)

    errors = WorkflowDef(nodes=[node]).validate()

    assert any(expected_fragment in error for error in errors)


@pytest.mark.parametrize(
    ("count", "interval"),
    [(0, 0), (20, 86_400)],
)
def test_workflow_node_failure_policy_validation_accepts_boundaries(
    count: int,
    interval: int,
) -> None:
    node = WorkflowNode(
        id="writer",
        auto_retry_count=count,
        auto_retry_interval_seconds=interval,
    )

    errors = WorkflowDef(nodes=[node]).validate()

    assert not any("自动重试" in error for error in errors)


def test_legacy_node_execution_state_loads_retry_defaults() -> None:
    state = _node_state_from_dict({
        "node_id": "writer",
        "status": "failed",
        "outputs": {"draft": "旧内容"},
    })

    assert state.attempt_count == 0
    assert state.automatic_retry_count == 0
    assert state.next_retry_at is None
    assert state.attempt_history == []
    assert state.input_snapshot == {}
    assert state.upstream_summary_snapshot == ""
    assert state.next_attempt_trigger == "initial"


def test_node_execution_state_retry_fields_round_trip_without_aliasing() -> None:
    state = NodeExecutionState(
        node_id="subflow",
        attempt_count=2,
        automatic_retry_count=1,
        next_retry_at="2026-07-22T10:00:00+08:00",
        attempt_history=[{"attempt_number": 1, "outputs": {"draft": "v1"}}],
        input_snapshot={"topic": {"name": "测试"}},
        upstream_summary_snapshot="上游快照",
        next_attempt_trigger=AUTO_RETRY_TRIGGER,
        child_states={
            "child": NodeExecutionState(node_id="child", attempt_count=1),
        },
    )

    serialized = _node_state_to_dict(state)
    restored = _node_state_from_dict(serialized)
    serialized["attempt_history"][0]["outputs"]["draft"] = "mutated"
    serialized["input_snapshot"]["topic"]["name"] = "mutated"

    assert restored.attempt_count == 2
    assert restored.automatic_retry_count == 1
    assert restored.next_retry_at == "2026-07-22T10:00:00+08:00"
    assert restored.attempt_history[0]["outputs"]["draft"] == "v1"
    assert restored.input_snapshot == {"topic": {"name": "测试"}}
    assert restored.upstream_summary_snapshot == "上游快照"
    assert restored.next_attempt_trigger == AUTO_RETRY_TRIGGER
    assert restored.child_states["child"].attempt_count == 1


def test_workflow_task_control_flow_state_is_backward_compatible() -> None:
    legacy = WorkflowTask.from_dict({"task_id": "task-legacy"})
    task = WorkflowTask(
        task_id="task-current",
        control_flow_state={"resume": {"node_id": "writer"}},
    )
    serialized = task.to_dict()
    restored = WorkflowTask.from_dict(serialized)
    serialized["control_flow_state"]["resume"]["node_id"] = "mutated"

    assert legacy.control_flow_state == {}
    assert restored.control_flow_state == {"resume": {"node_id": "writer"}}


def test_terminal_task_does_not_advertise_failed_node_actions() -> None:
    failed_state = NodeExecutionState(
        node_id="writer",
        status="failed",
        attempt_count=1,
    )

    stopped = WorkflowTask(
        status="stopped",
        node_states={"writer": failed_state},
    ).to_dict()
    failed = WorkflowTask(
        status="failed",
        node_states={"writer": failed_state},
    ).to_dict()

    assert stopped["node_states"]["writer"]["available_actions"] == []
    assert failed["node_states"]["writer"]["available_actions"] == [
        "retry", "skip",
    ]


@pytest.mark.parametrize(
    ("raw", "normalized"),
    [
        ("success", "completed"),
        ("completed", "completed"),
        ("failed", "failed"),
        ("skipped", "skipped"),
    ],
)
def test_normalize_node_status(raw: str, normalized: str) -> None:
    assert normalize_node_status(raw) == normalized


def test_retry_budget_precedes_auto_skip() -> None:
    node = WorkflowNode(auto_retry_count=2, fail_auto_skip=True)
    retryable = NodeExecutionState(status="failed", automatic_retry_count=1)
    exhausted = NodeExecutionState(status="failed", automatic_retry_count=2)

    assert automatic_retries_remaining(node, retryable) == 1
    assert can_auto_retry(node, retryable) is True
    assert should_auto_skip(node, retryable) is False
    assert automatic_retries_remaining(node, exhausted) == 0
    assert can_auto_retry(node, exhausted) is False
    assert should_auto_skip(node, exhausted) is True


def test_begin_attempt_freezes_original_inputs_and_increments_total_attempts() -> None:
    initial = NodeExecutionState(
        node_id="writer",
        rejection_reason="保留既有审批反馈",
    )
    first = begin_node_attempt(
        initial,
        started_at="2026-07-22T09:00:00+08:00",
        input_snapshot={},
        upstream_summary_snapshot="",
    )
    retried = begin_node_attempt(
        prepare_node_retry(
            record_attempt(replace(first, status="failed", error="timeout")),
            trigger=MANUAL_RETRY_TRIGGER,
        ),
        started_at="2026-07-22T09:05:00+08:00",
        input_snapshot={"changed": True},
        upstream_summary_snapshot="已变化",
    )

    assert initial.attempt_count == 0
    assert first.status == "running"
    assert first.attempt_count == 1
    assert first.rejection_reason == "保留既有审批反馈"
    assert retried.attempt_count == 2
    assert retried.input_snapshot == {}
    assert retried.upstream_summary_snapshot == ""


def test_record_attempt_normalizes_success_and_is_idempotent() -> None:
    running = NodeExecutionState(
        node_id="writer",
        status="success",
        attempt_count=1,
        next_attempt_trigger="initial",
        started_at="2026-07-22T09:00:00+08:00",
        outputs={"draft": "v1"},
    )

    recorded = record_attempt(
        running,
        completed_at="2026-07-22T09:01:00+08:00",
    )
    replayed = record_attempt(recorded)

    assert running.status == "success"
    assert running.attempt_history == []
    assert replayed.status == "completed"
    assert len(replayed.attempt_history) == 1
    assert replayed.attempt_history[0]["attempt_number"] == 1
    assert replayed.attempt_history[0]["status"] == "completed"
    assert "outputs" not in replayed.attempt_history[0]


def test_auto_retry_reset_preserves_audit_data_and_frozen_inputs() -> None:
    failed = NodeExecutionState(
        node_id="subflow",
        status="failed",
        session_id="session-old",
        started_at="2026-07-22T09:00:00+08:00",
        completed_at="2026-07-22T09:01:00+08:00",
        summary="partial",
        error="upstream unavailable",
        outputs={"draft": "partial"},
        stdout="partial stdout",
        stderr="partial stderr",
        token_usage={"total_tokens": 120},
        token_usage_calls=[{"total_tokens": 120}],
        attempt_count=1,
        attempt_history=[{"attempt_number": 1, "status": "failed"}],
        input_snapshot={"topic": "测试"},
        upstream_summary_snapshot="上游原文",
        child_states={
            "child": NodeExecutionState(node_id="child", status="completed"),
        },
    )
    original = deepcopy(failed)

    waiting = prepare_node_retry(
        failed,
        trigger=AUTO_RETRY_TRIGGER,
        next_retry_at="2026-07-22T09:01:30+08:00",
    )
    pending = activate_scheduled_retry(waiting)

    assert failed == original
    assert waiting.status == "retry_waiting"
    assert waiting.automatic_retry_count == 1
    assert waiting.next_attempt_trigger == AUTO_RETRY_TRIGGER
    assert waiting.next_retry_at == "2026-07-22T09:01:30+08:00"
    assert waiting.session_id == ""
    assert waiting.started_at is None
    assert waiting.completed_at is None
    assert waiting.summary == ""
    assert waiting.error == ""
    assert waiting.outputs == {}
    assert waiting.stdout == ""
    assert waiting.stderr == ""
    assert waiting.token_usage == {"total_tokens": 120}
    assert waiting.token_usage_calls == [{"total_tokens": 120}]
    assert waiting.attempt_history == [{"attempt_number": 1, "status": "failed"}]
    assert waiting.input_snapshot == {"topic": "测试"}
    assert waiting.upstream_summary_snapshot == "上游原文"
    assert waiting.child_states["child"].status == "completed"
    assert pending.status == "pending"
    assert pending.next_retry_at is None


def test_manual_retry_starts_a_new_automatic_retry_budget() -> None:
    failed = NodeExecutionState(
        status="failed",
        automatic_retry_count=3,
        child_states={"child": NodeExecutionState(node_id="child")},
    )

    reset = prepare_node_retry(
        failed,
        trigger=MANUAL_RETRY_TRIGGER,
        preserve_child_runtime=False,
    )

    assert reset.status == "pending"
    assert reset.automatic_retry_count == 0
    assert reset.next_attempt_trigger == MANUAL_RETRY_TRIGGER
    assert reset.child_states == {}


def test_retry_preparation_is_restricted_to_failed_nodes() -> None:
    with pytest.raises(ValueError, match="failed"):
        prepare_node_retry(
            NodeExecutionState(status="completed"),
            trigger=MANUAL_RETRY_TRIGGER,
        )
    with pytest.raises(ValueError, match="触发类型"):
        prepare_node_retry(
            NodeExecutionState(status="failed"),
            trigger="unknown",
        )


def test_failure_skip_clears_partial_outputs_but_preserves_failure_audit() -> None:
    failed = NodeExecutionState(
        node_id="subflow",
        status="failed",
        error="provider unavailable",
        summary="partial",
        outputs={"draft": "partial"},
        stdout="partial stdout",
        stderr="partial stderr",
        token_usage={"total_tokens": 40},
        attempt_history=[{"attempt_number": 1, "status": "failed"}],
        child_states={
            "child": NodeExecutionState(node_id="child", status="failed"),
        },
    )

    skipped = apply_failure_skip(
        failed,
        completed_at="2026-07-22T09:01:00+08:00",
    )

    assert skipped.status == "skipped"
    assert skipped.is_skipped is True
    assert skipped.error == "provider unavailable"
    assert skipped.summary == ""
    assert skipped.outputs == {}
    assert skipped.stdout == ""
    assert skipped.stderr == ""
    assert skipped.token_usage == {"total_tokens": 40}
    assert skipped.attempt_history == [
        {"attempt_number": 1, "status": "failed"},
    ]
    assert skipped.child_states["child"].status == "failed"


def test_only_failed_node_can_be_auto_skipped() -> None:
    with pytest.raises(ValueError, match="failed"):
        apply_failure_skip(NodeExecutionState(status="completed"))
