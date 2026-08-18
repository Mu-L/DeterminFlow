"""Safe runtime snapshots for the Workflow Executor pool."""

from __future__ import annotations

import asyncio
import inspect
import logging
import os
import time
from collections.abc import Mapping
from typing import Any

import src.config as _config

from .executor_protocol import STATUS_OPERATION


logger = logging.getLogger(__name__)

STATUS_RPC_TIMEOUT_SECONDS = 2.0
SAFE_REASONS = frozenset({
    "inline",
    "pool_unavailable",
    "identities_unavailable",
    "unreachable",
    "epoch_mismatch",
    "identity_mismatch",
    "invalid_status",
    "invalid_snapshot",
    "collection_failed",
    "not_ready",
})
MEMBER_STATUSES = frozenset({
    "starting",
    "ready",
    "restarting",
    "stopping",
    "stopped",
})
EVENT_BRIDGE_COUNTER_NAMES = (
    "forwarded",
    "reconnect",
    "failure",
    "oversized",
    "malformed_or_stale",
)


class EventBridgeCounters:
    """Process-local counts for one event-bridge endpoint."""

    def __init__(self) -> None:
        self._counts = {name: 0 for name in EVENT_BRIDGE_COUNTER_NAMES}

    def inc(self, name: str, n: int = 1) -> None:
        if name not in self._counts:
            return
        self._counts[name] += n

    def snapshot(self) -> dict[str, int]:
        return dict(self._counts)

    def reset(self) -> None:
        for name in self._counts:
            self._counts[name] = 0


class RpcCallCounters:
    """Cumulative allowlisted RPC counts for one Executor process."""

    def __init__(self) -> None:
        self.received = 0
        self.succeeded = 0
        self.failed = 0
        self.protocol_errors = 0
        self.by_operation: dict[str, int] = {}

    def record_received(self, operation: str) -> None:
        self.received += 1
        self.by_operation[operation] = self.by_operation.get(operation, 0) + 1

    def record_success(self) -> None:
        self.succeeded += 1

    def record_failure(self) -> None:
        self.failed += 1

    def record_protocol_error(self) -> None:
        self.protocol_errors += 1

    def snapshot(self) -> dict[str, Any]:
        return {
            "received": self.received,
            "succeeded": self.succeeded,
            "failed": self.failed,
            "protocol_errors": self.protocol_errors,
            "by_operation": dict(self.by_operation),
        }


controller_event_bridge_counters = EventBridgeCounters()


def reset_controller_event_bridge_counters() -> None:
    controller_event_bridge_counters.reset()


def count_active_tasks(workflow_manager: Any) -> int:
    counter = getattr(workflow_manager, "active_task_count", None)
    if callable(counter):
        try:
            return _nonneg_int(counter())
        except Exception:
            return 0
    if isinstance(counter, int) and not isinstance(counter, bool):
        return _nonneg_int(counter)
    running = getattr(workflow_manager, "_running_tasks", None)
    if not isinstance(running, Mapping):
        return 0
    active = 0
    for item in running.values():
        done = getattr(item, "done", None)
        if callable(done):
            try:
                if done():
                    continue
            except Exception:
                continue
        elif item is None:
            continue
        active += 1
    return active


def build_executor_runtime_status(
    *,
    identity: Any,
    started_monotonic: float,
    workflow_manager: Any,
    rpc_counts: Mapping[str, Any] | None = None,
    event_bridge: Mapping[str, Any] | None = None,
    now_monotonic: float | None = None,
    pid: int | None = None,
) -> dict[str, Any]:
    now = time.monotonic() if now_monotonic is None else now_monotonic
    return sanitize_member_status({
        "executor_id": getattr(identity, "executor_id", ""),
        "epoch": getattr(identity, "epoch", ""),
        "pid": os.getpid() if pid is None else pid,
        "uptime": max(0.0, now - started_monotonic),
        "active_task_count": count_active_tasks(workflow_manager),
        "reachable": True,
        "degraded": False,
        "rpc": rpc_counts or {},
        "event_bridge": event_bridge or {},
    })


def sanitize_rpc_counts(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raw = {}
    by_operation: dict[str, int] = {}
    source_ops = raw.get("by_operation")
    if isinstance(source_ops, Mapping):
        for key, value in source_ops.items():
            if isinstance(key, str) and key.isidentifier():
                count = _nonneg_int(value)
                if count:
                    by_operation[key] = count
    return {
        "received": _nonneg_int(raw.get("received")),
        "succeeded": _nonneg_int(raw.get("succeeded")),
        "failed": _nonneg_int(raw.get("failed")),
        "protocol_errors": _nonneg_int(raw.get("protocol_errors")),
        "by_operation": by_operation,
    }


def sanitize_event_bridge(raw: Any) -> dict[str, int]:
    if not isinstance(raw, Mapping):
        raw = {}
    return {
        name: _nonneg_int(raw.get(name))
        for name in EVENT_BRIDGE_COUNTER_NAMES
    }


def sanitize_member_status(raw: Any) -> dict[str, Any]:
    """Project a member snapshot onto the public allowlist."""
    if not isinstance(raw, Mapping):
        return _degraded_member(reason="invalid_status")
    executor_id = raw.get("executor_id")
    epoch = raw.get("epoch", raw.get("executor_epoch"))
    pid = raw.get("pid")
    member = {
        "executor_id": executor_id if isinstance(executor_id, str) else "",
        "epoch": epoch if isinstance(epoch, str) else "",
        "pid": pid if isinstance(pid, int) and not isinstance(pid, bool) else None,
        "uptime": _nonneg_float(raw.get("uptime", raw.get("uptime_seconds"))),
        "active_task_count": _nonneg_int(raw.get("active_task_count")),
        "reachable": bool(raw.get("reachable", True)),
        "degraded": bool(raw.get("degraded", False)),
        "rpc": sanitize_rpc_counts(raw.get("rpc")),
        "event_bridge": sanitize_event_bridge(raw.get("event_bridge")),
    }
    reason = _safe_reason(raw.get("reason"))
    if reason is not None:
        member["reason"] = reason
    status = raw.get("status")
    if status in MEMBER_STATUSES:
        member["status"] = status
        member["is_ready"] = bool(raw.get("is_ready", status == "ready"))
        member["restart_count"] = _nonneg_int(raw.get("restart_count"))
        exit_code = raw.get("last_exit_code")
        member["last_exit_code"] = (
            exit_code
            if isinstance(exit_code, int) and not isinstance(exit_code, bool)
            else None
        )
        ready_at = raw.get("last_ready_at")
        member["last_ready_at"] = (
            _nonneg_float(ready_at)
            if isinstance(ready_at, (int, float)) and not isinstance(ready_at, bool)
            else None
        )
    return member


def inline_executor_status(*, configured_count: int | None = None) -> dict[str, Any]:
    return {
        "mode": "inline",
        "degraded": False,
        "available": False,
        "reason": "inline",
        "configured_count": _configured_count(None, configured_count),
        "member_count": 0,
        "reachable_count": 0,
        "ready_count": 0,
        "members": [],
        "event_bridge": sanitize_event_bridge({}),
    }


def process_pool_unavailable_status(
    *,
    reason: str = "pool_unavailable",
    configured_count: int | None = None,
) -> dict[str, Any]:
    safe_reason = _safe_reason(reason) or "pool_unavailable"
    return {
        "mode": "process",
        "degraded": True,
        "available": False,
        "reason": safe_reason,
        "configured_count": _configured_count(None, configured_count),
        "member_count": 0,
        "reachable_count": 0,
        "ready_count": 0,
        "members": [],
        "event_bridge": sanitize_event_bridge(
            controller_event_bridge_counters.snapshot()
        ),
    }


async def collect_workflow_executor_status(
    pool: Any | None,
    *,
    mode: str | None = None,
    configured_count: int | None = None,
) -> dict[str, Any]:
    """Return a fail-closed Controller snapshot. Never raises to the HTTP layer."""
    try:
        return await _collect_workflow_executor_status(
            pool, mode=mode, configured_count=configured_count,
        )
    except Exception:
        logger.warning("Workflow Executor 状态采集失败", exc_info=True)
        resolved = _resolved_mode(mode)
        if resolved == "process":
            return process_pool_unavailable_status(
                reason="collection_failed",
                configured_count=configured_count,
            )
        return inline_executor_status(configured_count=configured_count)


async def _collect_workflow_executor_status(
    pool: Any | None,
    *,
    mode: str | None,
    configured_count: int | None,
) -> dict[str, Any]:
    resolved = _resolved_mode(mode)
    count = _configured_count(pool, configured_count)
    if resolved != "process":
        return inline_executor_status(configured_count=count)
    if pool is None:
        return process_pool_unavailable_status(
            reason="pool_unavailable", configured_count=count,
        )

    snapshot_fn = getattr(pool, "status_snapshot", None)
    if callable(snapshot_fn):
        try:
            raw = snapshot_fn()
            if inspect.isawaitable(raw):
                raw = await raw
            return _project_pool_snapshot(raw, configured_count=count)
        except Exception:
            logger.warning(
                "pool.status_snapshot 失败，回退到 identities/client_for",
                exc_info=True,
            )

    try:
        identities = tuple(pool.identities)
    except Exception:
        return process_pool_unavailable_status(
            reason="identities_unavailable", configured_count=count,
        )

    pids = _safe_member_pids(pool)
    gathered = await asyncio.gather(
        *(_collect_member(pool, identity, pids) for identity in identities),
        return_exceptions=True,
    )
    members: list[dict[str, Any]] = []
    for identity, result in zip(identities, gathered):
        if isinstance(result, Exception):
            members.append(_degraded_member(
                executor_id=getattr(identity, "executor_id", ""),
                epoch=getattr(identity, "epoch", ""),
                pid=pids.get(getattr(identity, "executor_id", "")),
                reason="unreachable",
            ))
        else:
            members.append(result)
    reachable = sum(1 for member in members if member.get("reachable"))
    ready = sum(1 for member in members if member.get("is_ready"))
    degraded = any(
        member.get("degraded") or not member.get("reachable") for member in members
    )
    snapshot = {
        "mode": "process",
        "degraded": degraded,
        "available": True,
        "configured_count": count,
        "member_count": len(members),
        "reachable_count": reachable,
        "ready_count": ready,
        "members": members,
        "event_bridge": sanitize_event_bridge(
            controller_event_bridge_counters.snapshot()
        ),
    }
    if degraded:
        snapshot["reason"] = _first_degraded_reason(members)
    return snapshot


async def _collect_member(
    pool: Any,
    identity: Any,
    pids: Mapping[str, int | None],
) -> dict[str, Any]:
    executor_id = getattr(identity, "executor_id", "")
    expected_epoch = getattr(identity, "epoch", "")
    supervisor = _lookup_supervisor(pool, executor_id)
    readiness = await _readiness_metadata(supervisor)
    snapshot_fn = (
        getattr(supervisor, "status_snapshot", None)
        if supervisor is not None
        else None
    )
    if callable(snapshot_fn):
        try:
            raw = snapshot_fn()
            if inspect.isawaitable(raw):
                raw = await raw
            member = _reconcile_member(sanitize_member_status(raw), identity, pids)
            return _merge_readiness(member, readiness)
        except Exception:
            logger.warning(
                "supervisor.status_snapshot 失败，回退到 status RPC: id=%s",
                executor_id,
                exc_info=True,
            )

    fallback = _degraded_member(
        executor_id=executor_id,
        epoch=expected_epoch,
        pid=pids.get(executor_id),
        reason="unreachable",
    )
    fallback = _merge_readiness(fallback, readiness)
    if readiness.get("is_ready") is False:
        fallback["reason"] = "not_ready"
        return fallback
    client_for = getattr(pool, "client_for", None)
    if not callable(client_for):
        return fallback
    try:
        client = client_for(executor_id)
        raw = await asyncio.wait_for(
            client.call(STATUS_OPERATION),
            timeout=STATUS_RPC_TIMEOUT_SECONDS,
        )
    except Exception:
        return fallback
    member = _reconcile_member(sanitize_member_status(raw), identity, pids)
    return _merge_readiness(member, readiness)


async def _readiness_metadata(supervisor: Any | None) -> dict[str, Any]:
    if supervisor is None:
        return {}
    snapshot_fn = getattr(supervisor, "snapshot", None)
    if not callable(snapshot_fn):
        return {}
    try:
        raw = snapshot_fn()
        if inspect.isawaitable(raw):
            raw = await raw
    except Exception:
        return {}
    status = getattr(raw, "status", None)
    if status not in MEMBER_STATUSES:
        return {}
    exit_code = getattr(raw, "last_exit_code", None)
    ready_at = getattr(raw, "last_ready_at", None)
    return {
        "status": status,
        "is_ready": bool(getattr(raw, "is_ready", False)),
        "restart_count": _nonneg_int(getattr(raw, "restart_count", 0)),
        "last_exit_code": (
            exit_code
            if isinstance(exit_code, int) and not isinstance(exit_code, bool)
            else None
        ),
        "last_ready_at": (
            _nonneg_float(ready_at)
            if isinstance(ready_at, (int, float)) and not isinstance(ready_at, bool)
            else None
        ),
    }


def _merge_readiness(
    member: dict[str, Any], readiness: Mapping[str, Any],
) -> dict[str, Any]:
    if readiness:
        member.update(readiness)
    return member


def _reconcile_member(
    member: dict[str, Any],
    identity: Any,
    pids: Mapping[str, int | None],
) -> dict[str, Any]:
    executor_id = getattr(identity, "executor_id", "")
    expected_epoch = getattr(identity, "epoch", "")
    returned_id = member.get("executor_id")
    returned_epoch = member.get("epoch")
    member["executor_id"] = executor_id
    pool_pid = pids.get(executor_id)
    if member.get("pid") is None and isinstance(pool_pid, int):
        member["pid"] = pool_pid
    if returned_id and returned_id != executor_id:
        member["reachable"] = False
        member["degraded"] = True
        member["reason"] = "identity_mismatch"
        member["epoch"] = expected_epoch
        return member
    if returned_epoch and returned_epoch != expected_epoch:
        member["reachable"] = False
        member["degraded"] = True
        member["reason"] = "epoch_mismatch"
        member["epoch"] = expected_epoch
        return member
    member["epoch"] = expected_epoch
    member["reachable"] = True
    member["degraded"] = False
    member.pop("reason", None)
    return member


def _project_pool_snapshot(
    raw: Any,
    *,
    configured_count: int,
) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        return process_pool_unavailable_status(
            reason="invalid_snapshot", configured_count=configured_count,
        )
    members_raw = raw.get("members")
    members = []
    if isinstance(members_raw, list):
        for item in members_raw:
            members.append(sanitize_member_status(item))
    event_bridge = raw.get("event_bridge")
    if not isinstance(event_bridge, Mapping):
        event_bridge = controller_event_bridge_counters.snapshot()
    reachable = sum(1 for member in members if member.get("reachable"))
    ready = sum(1 for member in members if member.get("is_ready"))
    degraded = bool(raw.get("degraded")) or any(
        member.get("degraded") or not member.get("reachable") for member in members
    )
    mode = raw.get("mode")
    if mode not in {"process", "inline"}:
        mode = "process"
    snapshot = {
        "mode": mode,
        "degraded": degraded,
        "available": bool(raw.get("available", mode == "process")),
        "configured_count": _nonneg_int(
            raw.get("configured_count"), default=configured_count,
        ) or configured_count,
        "member_count": len(members),
        "reachable_count": reachable,
        "ready_count": ready,
        "members": members,
        "event_bridge": sanitize_event_bridge(event_bridge),
    }
    reason = _safe_reason(raw.get("reason"))
    if reason is not None:
        snapshot["reason"] = reason
    elif degraded:
        snapshot["reason"] = _first_degraded_reason(members)
    return snapshot


def _first_degraded_reason(members: list[dict[str, Any]]) -> str:
    for member in members:
        if member.get("degraded") or not member.get("reachable"):
            reason = _safe_reason(member.get("reason"))
            if reason is not None:
                return reason
    return "unreachable" if members else "pool_unavailable"


def _lookup_supervisor(pool: Any, executor_id: str) -> Any | None:
    for name in ("supervisor_for", "get_supervisor"):
        lookup = getattr(pool, name, None)
        if callable(lookup):
            try:
                return lookup(executor_id)
            except Exception:
                return None
    supervisors = getattr(pool, "_supervisors", None)
    if isinstance(supervisors, Mapping):
        return supervisors.get(executor_id)
    return None


def _safe_member_pids(pool: Any) -> dict[str, int | None]:
    try:
        raw = getattr(pool, "member_pids", {}) or {}
    except Exception:
        return {}
    if not isinstance(raw, Mapping):
        return {}
    pids: dict[str, int | None] = {}
    for key, value in raw.items():
        if not isinstance(key, str):
            continue
        if value is None or (isinstance(value, int) and not isinstance(value, bool)):
            pids[key] = value
    return pids


def _degraded_member(
    *,
    executor_id: str = "",
    epoch: str = "",
    pid: int | None = None,
    reason: str = "unreachable",
) -> dict[str, Any]:
    member = {
        "executor_id": executor_id,
        "epoch": epoch,
        "pid": pid if isinstance(pid, int) and not isinstance(pid, bool) else None,
        "uptime": 0.0,
        "active_task_count": 0,
        "reachable": False,
        "degraded": True,
        "rpc": sanitize_rpc_counts({}),
        "event_bridge": sanitize_event_bridge({}),
    }
    safe_reason = _safe_reason(reason)
    if safe_reason is not None:
        member["reason"] = safe_reason
    return member


def _safe_reason(reason: Any) -> str | None:
    if isinstance(reason, str) and reason in SAFE_REASONS:
        return reason
    return None


def _configured_count(pool: Any | None, fallback: int | None) -> int:
    if isinstance(fallback, int) and not isinstance(fallback, bool) and fallback > 0:
        return fallback
    if pool is not None:
        ids = getattr(pool, "executor_ids", None)
        if ids is not None:
            try:
                return max(0, len(ids))
            except Exception:
                pass
    try:
        return int(getattr(_config, "WORKFLOW_EXECUTOR_COUNT", 1))
    except (TypeError, ValueError):
        return 1


def _resolved_mode(mode: str | None) -> str:
    value = mode
    if value is None:
        value = getattr(_config, "WORKFLOW_EXECUTOR_MODE", "inline")
    text = str(value).strip().lower()
    if text in {"inline", "process"}:
        return text
    return "inline"


def _nonneg_int(value: Any, default: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return default
    return max(0, value)


def _nonneg_float(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    return round(max(0.0, float(value)), 3)
