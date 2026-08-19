"""Controller-side client for one sticky Workflow Executor."""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

from .executor_protocol import (
    AUTH_TOKEN_FIELD,
    MAX_FRAME_BYTES,
    PROTOCOL_VERSION,
    RPC_TIMEOUT_SECONDS,
    ExecutorIdentity,
)
from .executor_transport import (
    LoopbackEndpoint,
    open_loopback_connection,
    open_loopback_socket_sync,
    require_auth_token,
)


class ExecutorUnavailable(RuntimeError):
    """The assigned Executor cannot currently accept a command."""


class WorkflowExecutorClient:
    def __init__(
        self,
        endpoint: LoopbackEndpoint,
        identity: ExecutorIdentity,
        *,
        auth_token: str,
    ):
        self.endpoint = endpoint
        self.identity = identity
        self.auth_token = require_auth_token(auth_token)

    def update_identity(self, identity: ExecutorIdentity) -> None:
        if identity.executor_id != self.identity.executor_id:
            raise ValueError("cannot change executor_id on a live client")
        self.identity = identity

    def update_transport(
        self,
        endpoint: LoopbackEndpoint,
        auth_token: str,
    ) -> None:
        self.endpoint = endpoint
        self.auth_token = require_auth_token(auth_token)

    def _request(self, operation: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return {
            "protocol_version": PROTOCOL_VERSION,
            "request_id": uuid.uuid4().hex,
            "executor_id": self.identity.executor_id,
            "executor_epoch": self.identity.epoch,
            AUTH_TOKEN_FIELD: self.auth_token,
            "operation": operation,
            "arguments": arguments,
        }

    @staticmethod
    def _unwrap(frame: Any, request_id: str) -> Any:
        if not isinstance(frame, dict) or frame.get("request_id") != request_id:
            raise ExecutorUnavailable("Executor returned an invalid response")
        if frame.get("protocol_version") != PROTOCOL_VERSION:
            raise ExecutorUnavailable("Executor protocol version mismatch")
        if frame.get("success") is not True:
            raise ExecutorUnavailable(str(frame.get("error") or "Executor request failed"))
        return frame.get("result")

    async def call(self, operation: str, **arguments: Any) -> Any:
        try:
            return await asyncio.wait_for(
                self._call(operation, arguments),
                timeout=RPC_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError as exc:
            raise ExecutorUnavailable(
                f"Workflow Executor request timed out: {operation}"
            ) from exc

    async def _call(self, operation: str, arguments: dict[str, Any]) -> Any:
        request = self._request(operation, arguments)
        try:
            reader, writer = await open_loopback_connection(
                self.endpoint,
                limit=MAX_FRAME_BYTES + 1,
            )
            writer.write(json.dumps(request, ensure_ascii=False).encode("utf-8") + b"\n")
            await writer.drain()
            raw = await reader.readline()
            writer.close()
            await writer.wait_closed()
        except (OSError, asyncio.TimeoutError) as exc:
            raise ExecutorUnavailable(f"Workflow Executor unavailable: {exc}") from exc
        if not raw or len(raw) > MAX_FRAME_BYTES:
            raise ExecutorUnavailable("Executor returned an empty or oversized response")
        try:
            frame = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ExecutorUnavailable("Executor returned malformed JSON") from exc
        return self._unwrap(frame, request["request_id"])

    def call_sync(self, operation: str, **arguments: Any) -> Any:
        request = self._request(operation, arguments)
        try:
            with open_loopback_socket_sync(self.endpoint, timeout=10.0) as client:
                client.sendall(
                    json.dumps(request, ensure_ascii=False).encode("utf-8") + b"\n"
                )
                chunks = bytearray()
                while not chunks.endswith(b"\n"):
                    chunk = client.recv(min(65536, MAX_FRAME_BYTES + 1 - len(chunks)))
                    if not chunk:
                        break
                    chunks.extend(chunk)
                    if len(chunks) > MAX_FRAME_BYTES:
                        raise ExecutorUnavailable("Executor response exceeded the frame limit")
        except OSError as exc:
            raise ExecutorUnavailable(f"Workflow Executor unavailable: {exc}") from exc
        try:
            frame = json.loads(chunks)
        except json.JSONDecodeError as exc:
            raise ExecutorUnavailable("Executor returned malformed JSON") from exc
        return self._unwrap(frame, request["request_id"])
