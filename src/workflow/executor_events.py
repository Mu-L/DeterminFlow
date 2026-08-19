"""One-way Executor event bridge into the Controller EventBus."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Awaitable, Callable

from .executor_observability import (
    EventBridgeCounters,
    controller_event_bridge_counters,
)
from .executor_protocol import (
    AUTH_TOKEN_FIELD,
    MAX_FRAME_BYTES,
    PROTOCOL_VERSION,
    ExecutorIdentity,
    auth_token_matches,
)
from .executor_transport import (
    LoopbackEndpoint,
    bound_endpoint,
    open_loopback_connection,
    require_auth_token,
    start_loopback_server,
)


logger = logging.getLogger(__name__)
EventHandler = Callable[[str, dict[str, Any]], Awaitable[None]]


class ControllerEventReceiver:
    def __init__(
        self,
        identity: ExecutorIdentity,
        handler: EventHandler,
        *,
        auth_token: str,
    ):
        self.identity = identity
        self.handler = handler
        self.auth_token = require_auth_token(auth_token)
        self.counters = EventBridgeCounters()
        self._server: asyncio.AbstractServer | None = None
        self._endpoint: LoopbackEndpoint | None = None

    @property
    def endpoint(self) -> LoopbackEndpoint:
        if self._endpoint is None:
            raise RuntimeError("event receiver has not started")
        return self._endpoint

    def stats(self) -> dict[str, int]:
        return self.counters.snapshot()

    def _note(self, name: str) -> None:
        self.counters.inc(name)
        controller_event_bridge_counters.inc(name)

    def update_identity(self, identity: ExecutorIdentity) -> None:
        if identity.executor_id != self.identity.executor_id:
            raise ValueError("cannot change event bridge executor_id")
        self.identity = identity

    def update_auth_token(self, auth_token: str) -> None:
        self.auth_token = require_auth_token(auth_token)

    async def start(self) -> LoopbackEndpoint:
        self._server = await start_loopback_server(
            self._handle_connection,
            limit=MAX_FRAME_BYTES + 1,
        )
        self._endpoint = bound_endpoint(self._server)
        return self._endpoint

    async def close(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        self._endpoint = None

    async def _handle_connection(self, reader, writer) -> None:
        try:
            while True:
                raw = await reader.readline()
                if not raw:
                    return
                if len(raw) > MAX_FRAME_BYTES:
                    self._note("oversized")
                    logger.warning("丢弃过大的 Workflow Executor 事件")
                    return
                try:
                    envelope = json.loads(raw)
                except json.JSONDecodeError:
                    self._note("malformed_or_stale")
                    logger.warning("丢弃格式错误的 Workflow Executor 事件")
                    continue
                if not self._valid(envelope):
                    self._note("malformed_or_stale")
                    logger.warning("丢弃旧世代或非法 Workflow Executor 事件")
                    continue
                await self.handler(envelope["channel"], envelope["event"])
                self._note("forwarded")
        except asyncio.LimitOverrunError:
            self._note("oversized")
            logger.warning("Workflow Executor 事件超过 transport 限制")
        finally:
            writer.close()
            await writer.wait_closed()

    def _valid(self, envelope: Any) -> bool:
        return (
            isinstance(envelope, dict)
            and envelope.get("protocol_version") == PROTOCOL_VERSION
            and auth_token_matches(envelope, self.auth_token)
            and envelope.get("executor_id") == self.identity.executor_id
            and envelope.get("executor_epoch") == self.identity.epoch
            and envelope.get("channel") in {"chat", "events"}
            and isinstance(envelope.get("event"), dict)
        )


class ExecutorEventForwarder:
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
        self.counters = EventBridgeCounters()
        self._writer: asyncio.StreamWriter | None = None
        self._lock = asyncio.Lock()
        self._connected_once = False

    def stats(self) -> dict[str, int]:
        return self.counters.snapshot()

    async def emit(self, channel: str, event: dict[str, Any]) -> None:
        envelope = {
            "protocol_version": PROTOCOL_VERSION,
            "executor_id": self.identity.executor_id,
            "executor_epoch": self.identity.epoch,
            AUTH_TOKEN_FIELD: self.auth_token,
            "channel": channel,
            "event": event,
        }
        try:
            payload = json.dumps(envelope, ensure_ascii=False).encode("utf-8") + b"\n"
        except (TypeError, ValueError):
            self.counters.inc("malformed_or_stale")
            logger.warning(
                "Workflow Executor 事件不可序列化: type=%s",
                event.get("type"),
                exc_info=True,
            )
            return
        if len(payload) > MAX_FRAME_BYTES:
            self.counters.inc("oversized")
            logger.warning("Workflow Executor 事件超过大小限制: type=%s", event.get("type"))
            return
        async with self._lock:
            for attempt in range(2):
                try:
                    if self._writer is None or self._writer.is_closing():
                        _, self._writer = await open_loopback_connection(
                            self.endpoint,
                            limit=MAX_FRAME_BYTES + 1,
                        )
                        if self._connected_once:
                            self.counters.inc("reconnect")
                        self._connected_once = True
                    self._writer.write(payload)
                    await self._writer.drain()
                    self.counters.inc("forwarded")
                    return
                except OSError:
                    await self._close_writer()
                    if attempt == 0:
                        await asyncio.sleep(0.05)
            self.counters.inc("failure")
            logger.warning("Workflow Executor 事件转发失败: type=%s", event.get("type"))

    async def close(self) -> None:
        async with self._lock:
            await self._close_writer()

    async def _close_writer(self) -> None:
        writer = self._writer
        self._writer = None
        if writer is None:
            return
        writer.close()
        try:
            await writer.wait_closed()
        except OSError:
            pass
