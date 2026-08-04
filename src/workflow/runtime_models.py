from __future__ import annotations

from dataclasses import dataclass, field
from copy import deepcopy
from typing import Any, TYPE_CHECKING

from .model_utils import _generate_id, _now_iso

if TYPE_CHECKING:
    from .definition import WorkflowDef


def _get_serial_execution_order(definition: WorkflowDef) -> list[str]:
    """根据工作流定义返回串行执行顺序。"""
    if not definition.edges:
        return [node.id for node in definition.nodes]
    if definition.gateways:
        plan = definition.get_execution_plan()
        node_ids: list[str] = []
        for step in plan:
            if step["type"] == "node":
                node_ids.append(step["node_id"])
            elif step["type"] == "branch":
                node_ids.extend(step["nodes"])
            elif step["type"] in {"condition_gateway", "loop_gateway"}:
                node_ids.extend(step.get("loop_body_nodes", []))
        return node_ids
    all_sources = {edge.source for edge in definition.edges}
    all_targets = {edge.target for edge in definition.edges}
    start_nodes = all_sources - all_targets
    if not start_nodes:
        return [node.id for node in definition.nodes]
    order = []
    current = next(iter(start_nodes))
    visited = set()
    while current and current not in visited:
        visited.add(current)
        order.append(current)
        current = definition.get_next_node_id(current)
    return order


@dataclass
class NodeExecutionState:
    """单个节点在运行时的状态。"""

    node_id: str = ""
    status: str = "pending"
    session_id: str = ""
    started_at: str | None = None
    completed_at: str | None = None
    summary: str = ""
    error: str = ""
    rejection_count: int = 0
    rejection_reason: str = ""
    reject_upstream_count: int = 0
    outputs: dict[str, str] = field(default_factory=dict)
    stdout: str = ""
    stderr: str = ""
    token_usage: dict | None = None
    token_usage_calls: list[dict] = field(default_factory=list)
    rejection_history: list[dict] = field(default_factory=list)
    iteration_history: list[dict] = field(default_factory=list)
    child_states: dict[str, NodeExecutionState] = field(default_factory=dict)
    is_skipped: bool = False
    attempt_count: int = 0
    automatic_retry_count: int = 0
    next_retry_at: str | None = None
    attempt_history: list[dict] = field(default_factory=list)
    input_snapshot: dict[str, Any] = field(default_factory=dict)
    upstream_summary_snapshot: str = ""
    next_attempt_trigger: str = "initial"


def _non_negative_int(value: Any, default: int = 0) -> int:
    if isinstance(value, bool):
        return default
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return default


def _node_state_from_dict(data: dict, node_id: str = "") -> NodeExecutionState:
    child_states = {
        child_id: _node_state_from_dict(child_data, child_id)
        for child_id, child_data in data.get("child_states", {}).items()
    }
    return NodeExecutionState(
        node_id=data.get("node_id", node_id),
        status=data.get("status", "pending"),
        session_id=data.get("session_id", ""),
        started_at=data.get("started_at"),
        completed_at=data.get("completed_at"),
        summary=data.get("summary", ""),
        error=data.get("error", ""),
        rejection_count=data.get("rejection_count", 0),
        rejection_reason=data.get("rejection_reason", ""),
        reject_upstream_count=data.get("reject_upstream_count", 0),
        outputs=data.get("outputs", {}),
        stdout=data.get("stdout", ""),
        stderr=data.get("stderr", ""),
        token_usage=data.get("token_usage"),
        token_usage_calls=[
            dict(item) for item in data.get("token_usage_calls", [])
            if isinstance(item, dict)
        ],
        rejection_history=[
            dict(item) for item in data.get("rejection_history", [])
            if isinstance(item, dict)
        ],
        iteration_history=data.get("iteration_history", []),
        child_states=child_states,
        is_skipped=data.get("is_skipped", False),
        attempt_count=_non_negative_int(data.get("attempt_count", 0)),
        automatic_retry_count=_non_negative_int(
            data.get("automatic_retry_count", 0)
        ),
        next_retry_at=(
            data.get("next_retry_at")
            if isinstance(data.get("next_retry_at"), str)
            else None
        ),
        attempt_history=[
            deepcopy(item) for item in data.get("attempt_history", [])
            if isinstance(item, dict)
        ],
        input_snapshot=(
            deepcopy(data.get("input_snapshot"))
            if isinstance(data.get("input_snapshot"), dict)
            else {}
        ),
        upstream_summary_snapshot=(
            data.get("upstream_summary_snapshot", "")
            if isinstance(data.get("upstream_summary_snapshot", ""), str)
            else ""
        ),
        next_attempt_trigger=(
            data.get("next_attempt_trigger", "initial")
            if isinstance(data.get("next_attempt_trigger", "initial"), str)
            else "initial"
        ),
    )


def _node_state_to_dict(
    state: NodeExecutionState,
    *,
    actions_enabled: bool = True,
) -> dict:
    data = {
        "node_id": state.node_id,
        "status": state.status,
        "session_id": state.session_id,
        "started_at": state.started_at,
        "completed_at": state.completed_at,
        "summary": state.summary,
        "error": state.error,
        "rejection_count": state.rejection_count,
        "rejection_reason": state.rejection_reason,
        "reject_upstream_count": state.reject_upstream_count,
        "outputs": state.outputs,
        "stdout": state.stdout,
        "stderr": state.stderr,
        "attempt_count": state.attempt_count,
        "automatic_retry_count": state.automatic_retry_count,
        "next_retry_at": state.next_retry_at,
        "attempt_history": deepcopy(state.attempt_history),
        "input_snapshot": deepcopy(state.input_snapshot),
        "upstream_summary_snapshot": state.upstream_summary_snapshot,
        "next_attempt_trigger": state.next_attempt_trigger,
    }
    if state.token_usage:
        data["token_usage"] = state.token_usage
    if state.token_usage_calls:
        data["token_usage_calls"] = state.token_usage_calls
    if state.rejection_history:
        data["rejection_history"] = state.rejection_history
    if state.iteration_history:
        data["iteration_history"] = state.iteration_history
    if state.child_states:
        data["child_states"] = {
            child_id: _node_state_to_dict(
                child_state,
                actions_enabled=actions_enabled,
            )
            for child_id, child_state in state.child_states.items()
        }
    if state.is_skipped:
        data["is_skipped"] = True
    if state.status in {"failed", "retry_waiting"}:
        data["available_actions"] = (
            ["retry", "skip"] if actions_enabled else []
        )
    return data


@dataclass
class WorkflowState:
    """工作流运行时的整体状态。"""

    workflow_id: str = ""
    status: str = "idle"
    current_node_id: str | None = None
    current_run_id: str | None = None
    started_at: str | None = None
    completed_at: str | None = None
    node_states: dict[str, NodeExecutionState] = field(default_factory=dict)

    def to_dict(self) -> dict:
        actions_enabled = self.status in {"failed", "retry_waiting"}
        return {
            "workflow_id": self.workflow_id,
            "status": self.status,
            "current_node_id": self.current_node_id,
            "current_run_id": self.current_run_id,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "node_states": {
                node_id: _node_state_to_dict(
                    state,
                    actions_enabled=actions_enabled,
                )
                for node_id, state in self.node_states.items()
            },
        }

    @classmethod
    def from_dict(cls, data: dict) -> WorkflowState:
        return cls(
            workflow_id=data.get("workflow_id", ""),
            status=data.get("status", "idle"),
            current_node_id=data.get("current_node_id"),
            current_run_id=data.get("current_run_id"),
            started_at=data.get("started_at"),
            completed_at=data.get("completed_at"),
            node_states={
                node_id: _node_state_from_dict(node_data, node_id)
                for node_id, node_data in data.get("node_states", {}).items()
            },
        )

    def get_execution_order(self, definition: WorkflowDef) -> list[str]:
        return _get_serial_execution_order(definition)


@dataclass
class WorkflowRunRecord:
    """单次工作流运行记录。"""

    run_id: str = field(default_factory=lambda: _generate_id("run"))
    workflow_id: str = ""
    status: str = "running"
    started_at: str = field(default_factory=_now_iso)
    completed_at: str | None = None
    node_executions: list[NodeExecutionState] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "workflow_id": self.workflow_id,
            "status": self.status,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "node_executions": [
                _node_state_to_dict(state) for state in self.node_executions
            ],
        }

    @classmethod
    def from_dict(cls, data: dict) -> WorkflowRunRecord:
        return cls(
            run_id=data.get("run_id", _generate_id("run")),
            workflow_id=data.get("workflow_id", ""),
            status=data.get("status", "running"),
            started_at=data.get("started_at", _now_iso()),
            completed_at=data.get("completed_at"),
            node_executions=[
                _node_state_from_dict(item)
                for item in data.get("node_executions", [])
            ],
        )


@dataclass
class WorkflowTask:
    """带定义快照和独立状态生命周期的工作流任务。"""

    task_id: str = field(default_factory=lambda: _generate_id("task"))
    workflow_id: str = ""
    name: str = ""
    status: str = "running"
    main_session_id: str | None = None
    main_takeover: bool = False
    current_node_id: str | None = None
    run_id: str | None = None
    created_at: str = field(default_factory=_now_iso)
    updated_at: str = field(default_factory=_now_iso)
    started_at: str | None = None
    completed_at: str | None = None
    node_states: dict[str, NodeExecutionState] = field(default_factory=dict)
    snapshot_definition: dict | None = None
    parameter_values: dict[str, str] = field(default_factory=dict)
    snapshot_variables: list[dict] | None = None
    workspace_override: str | None = None
    workspace_mode: str = "legacy_shared"
    workspace_ref: str | None = None
    disabled_node_ids: list[str] = field(default_factory=list)
    scheme_id: str | None = None
    control_flow_state: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        actions_enabled = self.status in {"failed", "retry_waiting"}
        return {
            "task_id": self.task_id,
            "workflow_id": self.workflow_id,
            "name": self.name,
            "status": self.status,
            "current_node_id": self.current_node_id,
            "run_id": self.run_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "node_states": {
                node_id: _node_state_to_dict(
                    state,
                    actions_enabled=actions_enabled,
                )
                for node_id, state in self.node_states.items()
            },
            "snapshot_definition": self.snapshot_definition,
            "parameter_values": self.parameter_values,
            "snapshot_variables": self.snapshot_variables,
            "main_session_id": self.main_session_id,
            "main_takeover": self.main_takeover,
            "disabled_node_ids": self.disabled_node_ids,
            "scheme_id": self.scheme_id,
            "workspace_override": self.workspace_override,
            "workspace_mode": self.workspace_mode,
            "workspace_ref": self.workspace_ref,
            "control_flow_state": deepcopy(self.control_flow_state),
        }

    @classmethod
    def from_dict(cls, data: dict) -> WorkflowTask:
        main_takeover = data.get("main_takeover") is True
        if "main_takeover" not in data and data.get("main_session_id"):
            node_types = {
                node.get("id"): node.get("node_type", "agent")
                for node in (data.get("snapshot_definition") or {}).get("nodes", [])
                if isinstance(node, dict) and node.get("id")
            }
            for node_id, node_data in data.get("node_states", {}).items():
                if not isinstance(node_data, dict):
                    continue
                if node_types.get(node_id, "agent") != "agent":
                    continue
                if (
                    node_data.get("status") == "waiting_approval"
                    or node_data.get("next_attempt_trigger") == "recovery_reissue"
                ):
                    main_takeover = True
                    break
        return cls(
            task_id=data.get("task_id", _generate_id("task")),
            workflow_id=data.get("workflow_id", ""),
            name=data.get("name", ""),
            status=data.get("status", "running"),
            current_node_id=data.get("current_node_id"),
            run_id=data.get("run_id"),
            created_at=data.get("created_at", _now_iso()),
            updated_at=data.get("updated_at", data.get("created_at", _now_iso())),
            started_at=data.get("started_at"),
            completed_at=data.get("completed_at"),
            node_states={
                node_id: _node_state_from_dict(node_data, node_id)
                for node_id, node_data in data.get("node_states", {}).items()
            },
            snapshot_definition=data.get("snapshot_definition"),
            parameter_values=data.get("parameter_values", {}),
            snapshot_variables=data.get("snapshot_variables"),
            main_session_id=data.get("main_session_id"),
            main_takeover=main_takeover,
            disabled_node_ids=data.get("disabled_node_ids", []),
            scheme_id=data.get("scheme_id"),
            workspace_override=data.get("workspace_override"),
            workspace_mode=data.get("workspace_mode", "legacy_shared"),
            workspace_ref=data.get("workspace_ref"),
            control_flow_state=(
                deepcopy(data.get("control_flow_state"))
                if isinstance(data.get("control_flow_state"), dict)
                else {}
            ),
        )

    def get_execution_order(self, definition: WorkflowDef) -> list[str]:
        return _get_serial_execution_order(definition)

    def get_execution_plan(self, definition: WorkflowDef) -> list[dict]:
        return definition.get_execution_plan()
