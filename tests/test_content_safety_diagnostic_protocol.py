import asyncio
import json
from types import SimpleNamespace

from fastapi import WebSocketDisconnect

from src.web import ws_handlers


class _Broadcaster:
    def subscribe(self):
        return asyncio.Queue()

    def unsubscribe(self, _queue):
        return None


class _DiagnosticBus:
    def __init__(self):
        self.direct_events: list[dict] = []
        self.broadcast_events: list[dict] = []
        self.result_sent = asyncio.Event()

    async def subscribe_session(self, _session_id, _ws):
        return None

    async def subscribe(self, _channel, _ws):
        return None

    async def unsubscribe(self, _channel, _ws):
        return None

    def enqueue_to_ws(self, _ws, event):
        self.direct_events.append(event)
        if event.get("type") == "content_safety_diagnostic_result":
            self.result_sent.set()
        return True

    async def emit_chat(self, event):
        self.broadcast_events.append(event)

    def get_session_revision(self, _session_id):
        return 0

    def get_active_stream(self, _session_id):
        return None


def _run_diagnostic_ws(monkeypatch, session, command: dict):
    fake_bus = _DiagnosticBus()
    monkeypatch.setattr(ws_handlers, "event_bus", fake_bus)

    class _FakeWebSocket:
        query_params = {"session_id": "session-1"}

        def __init__(self):
            self.receive_count = 0

        async def accept(self):
            return None

        async def receive_text(self):
            self.receive_count += 1
            if self.receive_count == 1:
                return json.dumps(command)
            await asyncio.wait_for(fake_bus.result_sent.wait(), timeout=1)
            raise WebSocketDisconnect()

    session_manager = SimpleNamespace(
        get_session=lambda session_id: session if session_id == "session-1" else None,
        get_main_session=lambda: session,
        notification_broadcaster=_Broadcaster(),
    )
    asyncio.run(ws_handlers.handle_chat_ws(
        _FakeWebSocket(),
        SimpleNamespace(session_manager=session_manager),
    ))
    return fake_bus


def test_content_safety_diagnostic_acknowledges_and_correlates_result(monkeypatch):
    async def run_diagnostic():
        return {"success": True, "message": "诊断完成"}

    session = SimpleNamespace(
        session_id="session-1",
        record=[],
        status="running",
        invocation_active=False,
        token_usage={},
        run_content_safety_diagnostic=run_diagnostic,
    )
    fake_bus = _run_diagnostic_ws(monkeypatch, session, {
        "type": "diagnose_content_safety",
        "session_id": "session-1",
        "request_id": "diagnostic-1",
    })

    protocol_events = [
        event for event in fake_bus.direct_events
        if event.get("type", "").startswith("content_safety_diagnostic_")
    ]
    assert protocol_events == [
        {
            "type": "content_safety_diagnostic_accepted",
            "session_id": "session-1",
            "request_id": "diagnostic-1",
        },
        {
            "type": "content_safety_diagnostic_result",
            "session_id": "session-1",
            "request_id": "diagnostic-1",
            "success": True,
            "message": "诊断完成",
        },
    ]
    assert fake_bus.broadcast_events[0]["type"] == "snapshot"


def test_content_safety_diagnostic_busy_result_can_be_retried(monkeypatch):
    session = SimpleNamespace(
        session_id="session-1",
        record=[],
        status="streaming",
        invocation_active=True,
        token_usage={},
    )
    fake_bus = _run_diagnostic_ws(monkeypatch, session, {
        "type": "diagnose_content_safety",
        "session_id": "session-1",
        "request_id": "diagnostic-busy",
    })

    result = next(
        event for event in fake_bus.direct_events
        if event.get("type") == "content_safety_diagnostic_result"
    )
    assert result["request_id"] == "diagnostic-busy"
    assert result["success"] is False
    assert "正在生成" in result["message"]
