from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

import src.agent.session as session_module
import src.agent.session_lifecycle as lifecycle_module
from src.agent.extension_sessions import ExtensionSessionRuntime
from src.agent.session import AgentSession
from src.agent.session_manager import SessionManager


@pytest.fixture
def detached_runtime(tmp_path, monkeypatch):
    monkeypatch.setattr(session_module, "SESSIONS_DIR", tmp_path)
    monkeypatch.setattr(lifecycle_module, "SESSIONS_DIR", tmp_path)
    agent_def = SimpleNamespace(
        model=None,
        model_params={},
        system_prompt_template="",
    )
    monkeypatch.setattr(
        "src.agent.definition.get_agent_definition",
        lambda agent_type: agent_def if agent_type == "plugin-page-assistant" else None,
    )
    manager = SessionManager()
    manager._prompt_builder = SimpleNamespace(
        build=lambda agent_type, **kwargs: f"prompt:{agent_type}"
    )

    def configure(self, session):
        session.compiled_graph = object()
        self._manager.register_runtime_session(session)

    monkeypatch.setattr(ExtensionSessionRuntime, "_configure_graph", configure)
    return manager, ExtensionSessionRuntime(manager).for_owner("example-plugin")


def test_detached_session_is_parentless_workspace_free_and_cold_after_turn(
    detached_runtime,
    monkeypatch,
):
    asyncio.run(_test_detached_session_is_parentless(detached_runtime, monkeypatch))


async def _test_detached_session_is_parentless(detached_runtime, monkeypatch):
    manager, runtime = detached_runtime
    manager.main_session_id = "existing-main"
    ref = await runtime.ensure_detached(
        external_ref="portal-session-1",
        agent_type="plugin-page-assistant",
        scope_hash="scope-1",
    )

    session = manager.sessions[ref.session_id]
    assert ref.created is True
    assert len(ref.session_id) == 32
    assert session.session_type == "sub"
    assert session.parent_id is None
    assert session.workspace_path is None
    assert session.lifecycle_profile == "detached_conversation"
    assert manager.main_session_id == "existing-main"

    async def send_message(self, content, **kwargs):
        self.record.extend(
            [
                {"id": "msg_00001", "type": "user", "content": content},
                {"id": "msg_00002", "type": "assistant", "content": "reply"},
            ]
        )
        return "reply"

    monkeypatch.setattr(AgentSession, "send_message", send_message)
    assert await runtime.invoke(ref.session_id, "hello") == "reply"
    assert ref.session_id not in manager.sessions
    persisted = AgentSession.load(ref.session_id)
    assert persisted is not None
    assert persisted.record[-1]["content"] == "reply"


def test_detached_session_reuses_persisted_conversation_after_restart(
    detached_runtime,
    monkeypatch,
):
    asyncio.run(_test_detached_session_reuses(detached_runtime, monkeypatch))


async def _test_detached_session_reuses(detached_runtime, monkeypatch):
    manager, runtime = detached_runtime
    ref = await runtime.ensure_detached(
        external_ref="portal-session-2",
        agent_type="plugin-page-assistant",
        scope_hash="scope-2",
    )

    async def first_turn(self, content, **kwargs):
        self.record.append({"id": "msg_00001", "type": "user", "content": content})
        return "first"

    monkeypatch.setattr(AgentSession, "send_message", first_turn)
    await runtime.invoke(ref.session_id, "first")

    restarted = SessionManager()
    restarted._prompt_builder = manager._prompt_builder
    restarted.load_sessions()
    restarted_runtime = ExtensionSessionRuntime(restarted).for_owner("example-plugin")
    reused = await restarted_runtime.ensure_detached(
        external_ref="portal-session-2",
        agent_type="plugin-page-assistant",
        scope_hash="scope-2",
    )
    assert reused.created is False
    assert reused.session_id == ref.session_id

    async def second_turn(self, content, **kwargs):
        assert self.record[-1]["content"] == "first"
        return "second"

    monkeypatch.setattr(AgentSession, "send_message", second_turn)
    assert await restarted_runtime.invoke(ref.session_id, "second") == "second"


def test_detached_session_fails_closed_across_owner_and_scope(detached_runtime):
    asyncio.run(_test_detached_session_fails_closed(detached_runtime))


async def _test_detached_session_fails_closed(detached_runtime):
    manager, runtime = detached_runtime
    ref = await runtime.ensure_detached(
        external_ref="portal-session-3",
        agent_type="plugin-page-assistant",
        scope_hash="scope-3",
    )

    with pytest.raises(PermissionError, match="scope"):
        await runtime.ensure_detached(
            external_ref="portal-session-3",
            agent_type="plugin-page-assistant",
            scope_hash="different-scope",
        )

    other_owner = ExtensionSessionRuntime(manager).for_owner("other-plugin")
    with pytest.raises(PermissionError, match="owner"):
        other_owner._load_owned(ref.session_id)


def test_detached_session_creation_is_idempotent_under_concurrency(detached_runtime):
    asyncio.run(_test_detached_creation_is_idempotent(detached_runtime))


async def _test_detached_creation_is_idempotent(detached_runtime):
    manager, runtime = detached_runtime
    first, second = await asyncio.gather(
        runtime.ensure_detached(
            external_ref="portal-session-concurrent",
            agent_type="plugin-page-assistant",
            scope_hash="scope-concurrent",
        ),
        runtime.ensure_detached(
            external_ref="portal-session-concurrent",
            agent_type="plugin-page-assistant",
            scope_hash="scope-concurrent",
        ),
    )

    assert first.session_id == second.session_id
    assert sorted([first.created, second.created]) == [False, True]
    matching = [
        item
        for item in manager._session_catalog.values()
        if item.external_ref == "portal-session-concurrent"
    ]
    assert len(matching) == 1


def test_detached_invocation_lock_is_released_after_waiters_finish(
    detached_runtime,
    monkeypatch,
):
    asyncio.run(_test_detached_invocation_lock_is_released(detached_runtime, monkeypatch))


async def _test_detached_invocation_lock_is_released(detached_runtime, monkeypatch):
    _, runtime = detached_runtime
    ref = await runtime.ensure_detached(
        external_ref="portal-session-lock",
        agent_type="plugin-page-assistant",
        scope_hash="scope-lock",
    )
    first_started = asyncio.Event()
    release_first = asyncio.Event()
    active = 0
    peak = 0

    async def send_message(self, content, **kwargs):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        if content == "first":
            first_started.set()
            await release_first.wait()
        active -= 1
        return content

    monkeypatch.setattr(AgentSession, "send_message", send_message)
    first = asyncio.create_task(runtime.invoke(ref.session_id, "first"))
    await first_started.wait()
    second = asyncio.create_task(runtime.invoke(ref.session_id, "second"))
    await asyncio.sleep(0)
    release_first.set()

    assert await asyncio.gather(first, second) == ["first", "second"]
    assert peak == 1
    assert ref.session_id not in runtime._invocation_locks
    assert ref.session_id not in runtime._invocation_users


def test_detached_session_can_be_deleted_by_owner_reference(detached_runtime):
    asyncio.run(_test_detached_session_delete_by_reference(detached_runtime))


async def _test_detached_session_delete_by_reference(detached_runtime):
    manager, runtime = detached_runtime
    ref = await runtime.ensure_detached(
        external_ref="portal-session-delete",
        agent_type="plugin-page-assistant",
        scope_hash="scope-delete",
    )

    assert (
        await runtime.delete_detached(
            external_ref="portal-session-delete",
            scope_hash="scope-delete",
        )
        is True
    )
    assert manager.get_session(ref.session_id) is None
    assert (
        await runtime.delete_detached(
            external_ref="portal-session-delete",
            scope_hash="scope-delete",
        )
        is False
    )
