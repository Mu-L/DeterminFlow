"""与节点插件无关的失败处理策略纯函数。"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from typing import Any, Protocol

from .runtime_models import NodeExecutionState


MAX_AUTO_RETRY_COUNT = 20
MAX_AUTO_RETRY_INTERVAL_SECONDS = 86_400

INITIAL_ATTEMPT_TRIGGER = "initial"
AUTO_RETRY_TRIGGER = "auto_retry"
MANUAL_RETRY_TRIGGER = "manual_retry"
VALIDATOR_RETRY_TRIGGER = "validator_retry"
RECOVERY_REISSUE_TRIGGER = "recovery_reissue"
RETRY_TRIGGERS = {
    AUTO_RETRY_TRIGGER,
    MANUAL_RETRY_TRIGGER,
    VALIDATOR_RETRY_TRIGGER,
}


class FailurePolicyNode(Protocol):
    auto_retry_count: int
    auto_retry_interval_seconds: int
    fail_auto_skip: bool


def normalize_node_status(status: str) -> str:
    """把插件历史状态归一为 Workflow 的规范状态。"""
    return "completed" if status in {"success", "completed"} else status


def automatic_retries_remaining(
    node: FailurePolicyNode,
    state: NodeExecutionState,
) -> int:
    """返回当前失败链尚可调度的自动重试次数。"""
    configured = getattr(node, "auto_retry_count", 0)
    if isinstance(configured, bool) or not isinstance(configured, int):
        return 0
    consumed = max(0, state.automatic_retry_count)
    return max(0, min(configured, MAX_AUTO_RETRY_COUNT) - consumed)


def can_auto_retry(
    node: FailurePolicyNode,
    state: NodeExecutionState,
) -> bool:
    """只有失败节点且当前失败链仍有预算时才允许自动重试。"""
    return (
        normalize_node_status(state.status) == "failed"
        and automatic_retries_remaining(node, state) > 0
    )


def should_auto_skip(
    node: FailurePolicyNode,
    state: NodeExecutionState,
) -> bool:
    """自动重试耗尽后，按节点策略决定是否跳过。"""
    return (
        normalize_node_status(state.status) == "failed"
        and bool(getattr(node, "fail_auto_skip", False))
        and not can_auto_retry(node, state)
    )


def _copy_state(
    state: NodeExecutionState,
    **changes: Any,
) -> NodeExecutionState:
    """复制状态及其可变字段，避免策略函数修改调用方对象。"""
    copied_fields = {
        "outputs": deepcopy(state.outputs),
        "token_usage": deepcopy(state.token_usage),
        "token_usage_calls": deepcopy(state.token_usage_calls),
        "rejection_history": deepcopy(state.rejection_history),
        "iteration_history": deepcopy(state.iteration_history),
        "child_states": deepcopy(state.child_states),
        "attempt_history": deepcopy(state.attempt_history),
        "input_snapshot": deepcopy(state.input_snapshot),
    }
    copied_fields.update(changes)
    return replace(state, **copied_fields)


def begin_node_attempt(
    state: NodeExecutionState,
    *,
    started_at: str,
    input_snapshot: dict[str, Any] | None = None,
    upstream_summary_snapshot: str | None = None,
) -> NodeExecutionState:
    """开始一次尝试，并只在首次执行时冻结原始输入与上游摘要。"""
    frozen_input = state.input_snapshot
    if (
        (
            state.attempt_count == 0
            or state.next_attempt_trigger == INITIAL_ATTEMPT_TRIGGER
        )
        and input_snapshot is not None
    ):
        frozen_input = deepcopy(input_snapshot)
    frozen_upstream = state.upstream_summary_snapshot
    if (
        (
            state.attempt_count == 0
            or state.next_attempt_trigger == INITIAL_ATTEMPT_TRIGGER
        )
        and upstream_summary_snapshot is not None
    ):
        frozen_upstream = upstream_summary_snapshot
    reissuing_approval = (
        state.next_attempt_trigger == RECOVERY_REISSUE_TRIGGER
    )
    return _copy_state(
        state,
        status="running",
        session_id=state.session_id if reissuing_approval else "",
        started_at=started_at,
        completed_at=None,
        summary=state.summary if reissuing_approval else "",
        error="",
        outputs={},
        stdout="",
        stderr="",
        is_skipped=False,
        attempt_count=state.attempt_count + 1,
        next_retry_at=None,
        input_snapshot=deepcopy(frozen_input),
        upstream_summary_snapshot=frozen_upstream,
    )


def record_attempt(
    state: NodeExecutionState,
    *,
    status: str | None = None,
    completed_at: str | None = None,
) -> NodeExecutionState:
    """把当前尝试写入历史；同一 attempt_number 重放时原位覆盖。"""
    normalized = normalize_node_status(status or state.status)
    record = {
        "attempt_number": state.attempt_count,
        "trigger": state.next_attempt_trigger,
        "status": normalized,
        "automatic_retry_count": state.automatic_retry_count,
        "session_id": state.session_id,
        "started_at": state.started_at,
        "completed_at": completed_at or state.completed_at,
        "error": state.error,
    }
    history = deepcopy(state.attempt_history)
    if history and history[-1].get("attempt_number") == state.attempt_count:
        history[-1] = record
    else:
        history.append(record)
    return _copy_state(
        state,
        status=normalized,
        completed_at=completed_at or state.completed_at,
        attempt_history=history,
    )


def prepare_node_retry(
    state: NodeExecutionState,
    *,
    trigger: str,
    next_retry_at: str | None = None,
    preserve_child_runtime: bool = True,
) -> NodeExecutionState:
    """清理失败尝试的瞬态数据，保留累计费用、历史与冻结输入。

    ``auto_retry`` 会消耗当前失败链的一次自动预算；``manual_retry``
    与 ``validator_retry`` 开启新的失败链，因此自动重试计数归零。
    提供 ``next_retry_at`` 时状态进入 ``retry_waiting``，可在进程重启后
    继续调度。
    """
    if trigger not in RETRY_TRIGGERS:
        raise ValueError(f"不支持的节点重试触发类型: {trigger}")
    if (
        trigger == AUTO_RETRY_TRIGGER
        and state.status == "retry_waiting"
        and state.next_attempt_trigger == trigger
        and state.next_retry_at == next_retry_at
    ):
        return _copy_state(state)
    if normalize_node_status(state.status) != "failed":
        raise ValueError("只有 failed 节点可以准备重试")

    automatic_retry_count = (
        state.automatic_retry_count + 1
        if trigger == AUTO_RETRY_TRIGGER
        else 0
    )
    return _copy_state(
        state,
        status="retry_waiting" if next_retry_at is not None else "pending",
        session_id="",
        started_at=None,
        completed_at=None,
        summary="",
        error="",
        rejection_reason="",
        outputs={},
        stdout="",
        stderr="",
        child_states=(
            deepcopy(state.child_states) if preserve_child_runtime else {}
        ),
        is_skipped=False,
        automatic_retry_count=automatic_retry_count,
        next_retry_at=next_retry_at,
        next_attempt_trigger=trigger,
    )


def activate_scheduled_retry(state: NodeExecutionState) -> NodeExecutionState:
    """把已持久化且到期的自动重试转为可立即执行状态。"""
    if state.status != "retry_waiting":
        raise ValueError("只有 retry_waiting 节点可以激活定时重试")
    return _copy_state(state, status="pending", next_retry_at=None)


def apply_failure_skip(
    state: NodeExecutionState,
    *,
    completed_at: str | None = None,
) -> NodeExecutionState:
    """跳过失败节点并清除所有可能被下游误用的部分产出。"""
    if normalize_node_status(state.status) != "failed":
        raise ValueError("只有 failed 节点可以执行失败跳过")
    return _copy_state(
        state,
        status="skipped",
        session_id="",
        completed_at=completed_at or state.completed_at,
        summary="",
        outputs={},
        stdout="",
        stderr="",
        is_skipped=True,
        next_retry_at=None,
    )
