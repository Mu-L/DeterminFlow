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


def test_interactive_main_model_switch_is_persisted(monkeypatch):
    async def scenario():
        session = AgentSession(
            session_id="main-model-switch",
            session_type="main",
            agent_type="reviewer",
            model_params={"reasoning_effort": "high"},
        )
        session.model_id = "first:model-a"
        session.tools = ["tool-a"]
        saved = False
        compiled_with = None

        async def fake_save():
            nonlocal saved
            saved = True

        def fake_setup_graph(*, llm, tools):
            nonlocal compiled_with
            compiled_with = (llm, tools)

        class FakeModelManager:
            @staticmethod
            def get_all_models():
                return ["first:model-a", "second:model-b"]

            @staticmethod
            def get_provider_capabilities(provider_id):
                assert provider_id == "second"
                return {"reasoning_efforts": ["low", "high"]}

        class FakeAgentConfigManager:
            def __init__(self):
                self.updates = []

            @staticmethod
            def get_agent_config(agent_type):
                assert agent_type == "main"
                return {
                    "model": "first:model-a",
                    "model_params": {"temperature": 0.2, "reasoning_effort": "high"},
                }

            def update_agent(self, agent_type, updates):
                self.updates.append((agent_type, updates))
                return True

        session.async_save = fake_save
        session.setup_graph = fake_setup_graph
        manager = SessionManager()
        manager.sessions[session.session_id] = session
        agent_config_manager = FakeAgentConfigManager()
        manager.inject_dependencies(agent_config_manager=agent_config_manager)
        fake_llm = object()
        monkeypatch.setattr(
            "src.core.model_manager.get_model_manager",
            lambda: FakeModelManager(),
        )
        monkeypatch.setattr(
            "src.core.llm_client.create_startup_llm",
            lambda **_kwargs: fake_llm,
        )

        result = await manager.update_session_model(
            session.session_id,
            model_id="second:model-b",
            reasoning_effort="low",
        )
        return session, result, saved, compiled_with, fake_llm, agent_config_manager

    session, result, saved, compiled_with, fake_llm, agent_config_manager = asyncio.run(scenario())

    assert result["success"] is True
    assert session.model_id == "second:model-b"
    assert session.model_params["reasoning_effort"] == "low"
    assert session.model_params["thinking_enabled"] is True
    assert saved is True
    assert compiled_with == (fake_llm, ["tool-a"])
    assert agent_config_manager.updates == [(
        "main",
        {
            "model": "second:model-b",
            "model_params": {
                "temperature": 0.2,
                "reasoning_effort": "low",
                "thinking_enabled": True,
            },
        },
    )]


def test_session_round_trip_preserves_model_selection():
    session = AgentSession(
        session_id="model-round-trip",
        session_type="main",
        model_params={"reasoning_effort": "max", "thinking_enabled": True},
    )
    session.model_id = "openai:model-a"

    restored = AgentSession.from_dict(session.to_dict())

    assert restored.model_id == "openai:model-a"
    assert restored.model_params == {
        "reasoning_effort": "max",
        "thinking_enabled": True,
    }
