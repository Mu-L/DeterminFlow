import asyncio
import json
from types import SimpleNamespace

from fastapi import WebSocketDisconnect

from src.agent.session import AgentSession
from src.agent.session_manager import SessionManager
from src.web import ws_handlers


def test_delete_refuses_pre_stream_invocation():
    async def scenario():
        session = AgentSession(
            session_id="pre-stream-delete",
            session_type="sub",
            agent_type="test",
        )
        session.compiled_graph = object()
        entered_compression = asyncio.Event()
        never_finish = asyncio.Event()

        async def no_save():
            return None

        async def block_compression(*_args, **_kwargs):
            entered_compression.set()
            await never_finish.wait()

        session.async_save = no_save
        session._check_and_compress_messages = block_compression
        task = asyncio.create_task(session.send_message("still active"))
        await entered_compression.wait()
        manager = SessionManager()
        manager.sessions[session.session_id] = session
        result = await manager.delete_session(session.session_id)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        return manager, session, result

    manager, session, result = asyncio.run(scenario())
    assert result["success"] is False
    assert session.session_id in manager.sessions
    assert "生成中" in result["message"]


def test_chat_ws_resync_returns_a_fresh_authoritative_snapshot(monkeypatch):
    class _FakeBus:
        def __init__(self):
            self.snapshots: list[dict] = []

        async def subscribe(self, _channel, _ws):
            return None

        async def subscribe_session(self, _session_id, _ws):
            return None

        async def unsubscribe(self, _channel, _ws):
            return None

        def enqueue_to_ws(self, _ws, event):
            self.snapshots.append(event)
            return True

        def get_session_revision(self, _session_id):
            return 7

        def get_active_stream(self, _session_id):
            return None

    class _FakeWebSocket:
        query_params = {"session_id": "session-1"}

        def __init__(self):
            self.receive_count = 0

        async def accept(self):
            return None

        async def receive_text(self):
            self.receive_count += 1
            if self.receive_count == 1:
                return json.dumps({"type": "resync", "session_id": "session-1"})
            raise WebSocketDisconnect()

        async def send_text(self, _message):
            return None

    class _Broadcaster:
        def subscribe(self):
            return asyncio.Queue()

        def unsubscribe(self, _queue):
            return None

    session = SimpleNamespace(
        session_id="session-1",
        record=[],
        status="running",
        token_usage={},
    )
    session_manager = SimpleNamespace(
        get_session=lambda session_id: session if session_id == "session-1" else None,
        get_main_session=lambda: session,
        notification_broadcaster=_Broadcaster(),
    )
    fake_bus = _FakeBus()
    monkeypatch.setattr(ws_handlers, "event_bus", fake_bus)

    asyncio.run(ws_handlers.handle_chat_ws(
        _FakeWebSocket(),
        SimpleNamespace(session_manager=session_manager),
    ))

    assert len(fake_bus.snapshots) == 2
    assert all(snapshot["type"] == "snapshot" for snapshot in fake_bus.snapshots)
    assert all(snapshot["messages"] == [] for snapshot in fake_bus.snapshots)
    assert fake_bus.snapshots[-1]["revision"] == 7
