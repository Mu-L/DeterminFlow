"""Versioned local RPC contract for the Workflow Executor process."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


PROTOCOL_VERSION = 1
MAX_FRAME_BYTES = 1024 * 1024
RPC_TIMEOUT_SECONDS = 30.0
STATUS_OPERATION = "status"

ASYNC_OPERATIONS = frozenset({
    "run_task",
    "stop_task",
    "retry_node",
    "skip_node",
    "recover_owned_tasks",
})
READONLY_OPERATIONS = frozenset({"ping", STATUS_OPERATION})
SYNC_OPERATIONS = frozenset({"approve_node", "ping", "shutdown", STATUS_OPERATION})
OPERATIONS = ASYNC_OPERATIONS | SYNC_OPERATIONS


class ExecutorProtocolError(ValueError):
    """A malformed, unsupported, or stale Executor frame."""


@dataclass(frozen=True)
class ExecutorIdentity:
    executor_id: str
    epoch: str

    def __post_init__(self) -> None:
        if not self.executor_id or not self.epoch:
            raise ExecutorProtocolError("executor identity must be non-empty")


def validate_executor_generation(
    raw: Any,
    *,
    expected_identity: ExecutorIdentity,
) -> None:
    """Reject frames that do not target the live Executor generation."""
    if not isinstance(raw, dict):
        raise ExecutorProtocolError("request must be an object")
    if raw.get("executor_id") != expected_identity.executor_id:
        raise ExecutorProtocolError("executor_id mismatch")
    if raw.get("executor_epoch") != expected_identity.epoch:
        raise ExecutorProtocolError("executor_epoch stale")


def validate_request(
    raw: Any,
    *,
    expected_identity: ExecutorIdentity,
) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ExecutorProtocolError("request must be an object")
    if raw.get("protocol_version") != PROTOCOL_VERSION:
        raise ExecutorProtocolError("unsupported protocol_version")
    validate_executor_generation(raw, expected_identity=expected_identity)
    request_id = raw.get("request_id")
    if not isinstance(request_id, str) or not request_id:
        raise ExecutorProtocolError("request_id must be a non-empty string")
    operation = raw.get("operation")
    if operation not in OPERATIONS:
        raise ExecutorProtocolError("unsupported operation")
    arguments = raw.get("arguments", {})
    if not isinstance(arguments, dict):
        raise ExecutorProtocolError("arguments must be an object")
    return {
        "request_id": request_id,
        "operation": operation,
        "arguments": arguments,
    }


def response_frame(
    request_id: str,
    *,
    result: Any = None,
    error: str | None = None,
) -> dict[str, Any]:
    frame = {
        "protocol_version": PROTOCOL_VERSION,
        "request_id": request_id,
        "success": error is None,
    }
    if error is None:
        frame["result"] = result
    else:
        frame["error"] = error
    return frame
