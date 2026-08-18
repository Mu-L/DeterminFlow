"""Executor-side allowlisted RPC server."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Awaitable, Callable

from .executor_observability import (
    RpcCallCounters,
    build_executor_runtime_status,
)
from .executor_protocol import (
    ASYNC_OPERATIONS,
    MAX_FRAME_BYTES,
    RPC_TIMEOUT_SECONDS,
    STATUS_OPERATION,
    ExecutorIdentity,
    ExecutorProtocolError,
    response_frame,
    validate_request,
)


logger = logging.getLogger(__name__)


class WorkflowExecutorServer:
    def __init__(
        self,
        socket_path: Path,
        identity: ExecutorIdentity,
        workflow_manager: Any,
        *,
        shutdown_callback: Callable[[], None] | None = None,
        event_forwarder: Any | None = None,
    ):
        self.socket_path = Path(socket_path)
        self.identity = identity
        self.workflow_manager = workflow_manager
        self.shutdown_callback = shutdown_callback
        self.event_forwarder = event_forwarder
        self.rpc_counters = RpcCallCounters()
        self._started_monotonic = time.monotonic()
        self._server: asyncio.AbstractServer | None = None

    async def start(self) -> None:
        self.socket_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.socket_path.exists():
            if not self.socket_path.is_socket():
                raise RuntimeError("Executor socket path is not a socket")
            self.socket_path.unlink()
        self._server = await asyncio.start_unix_server(
            self._handle_connection,
            self.socket_path,
            limit=MAX_FRAME_BYTES + 1,
        )
        os.chmod(self.socket_path, 0o600)

    async def close(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        self.socket_path.unlink(missing_ok=True)

    def runtime_status(self) -> dict[str, Any]:
        event_bridge = {}
        stats = getattr(self.event_forwarder, "stats", None)
        if callable(stats):
            try:
                event_bridge = stats()
            except Exception:
                event_bridge = {}
        elif self.event_forwarder is not None:
            counters = getattr(self.event_forwarder, "counters", None)
            snapshot = getattr(counters, "snapshot", None)
            if callable(snapshot):
                event_bridge = snapshot()
        return build_executor_runtime_status(
            identity=self.identity,
            started_monotonic=self._started_monotonic,
            workflow_manager=self.workflow_manager,
            rpc_counts=self.rpc_counters.snapshot(),
            event_bridge=event_bridge,
        )

    async def _dispatch(self, operation: str, arguments: dict[str, Any]) -> Any:
        if operation == "ping":
            return {
                "executor_id": self.identity.executor_id,
                "executor_epoch": self.identity.epoch,
                "pid": os.getpid(),
            }
        if operation == STATUS_OPERATION:
            return self.runtime_status()
        if operation == "shutdown":
            if self.shutdown_callback is not None:
                self.shutdown_callback()
            return {"accepted": True}
        if operation == "recover_owned_tasks":
            return await self.workflow_manager.recover_workflow_tasks(
                executor_identity=self.identity,
            )
        method = getattr(self.workflow_manager, operation)
        result = method(**arguments)
        if operation in ASYNC_OPERATIONS:
            if not isinstance(result, Awaitable):
                raise TypeError(f"{operation} must return an awaitable")
            return await result
        return result

    async def _handle_connection(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        request_id = "unknown"
        try:
            raw = await asyncio.wait_for(
                reader.readline(), timeout=RPC_TIMEOUT_SECONDS,
            )
            if not raw or len(raw) > MAX_FRAME_BYTES:
                raise ExecutorProtocolError("request frame is empty or oversized")
            request = json.loads(raw)
            if isinstance(request, dict) and isinstance(request.get("request_id"), str):
                request_id = request["request_id"]
            validated = validate_request(request, expected_identity=self.identity)
            request_id = validated["request_id"]
            operation = validated["operation"]
            self.rpc_counters.record_received(operation)
            if operation == STATUS_OPERATION:
                self.rpc_counters.record_success()
                result = self.runtime_status()
            else:
                result = await self._dispatch(operation, validated["arguments"])
                self.rpc_counters.record_success()
            response = response_frame(request_id, result=result)
        except (ExecutorProtocolError, json.JSONDecodeError) as exc:
            self.rpc_counters.record_protocol_error()
            response = response_frame(request_id, error=str(exc))
        except Exception as exc:
            self.rpc_counters.record_failure()
            logger.exception("Workflow Executor RPC failed: request_id=%s", request_id)
            response = response_frame(
                request_id,
                error=f"{type(exc).__name__}: {exc}",
            )
        writer.write(json.dumps(response, ensure_ascii=False).encode("utf-8") + b"\n")
        try:
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()
