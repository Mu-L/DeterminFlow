import asyncio
import json
from types import SimpleNamespace

import pytest

import src.agent.session as session_module
from src.agent.session import AgentSession, _persistence_manager
from src.workflow.execution_loop import WorkflowLoopMixin
from src.workflow.runtime_models import NodeExecutionState


def test_loop_iteration_snapshots_and_resets_rejection_budget():
    state = NodeExecutionState(
        node_id="agent",
        status="completed",
        rejection_count=1,
        rejection_reason="schema mismatch",
        reject_upstream_count=2,
        rejection_history=[{"retry_index": 1, "reason": "schema mismatch"}],
    )
    mixin = WorkflowLoopMixin()

    mixin._snapshot_node_for_iteration(state, 1, {})
    mixin._reset_rejection_state_for_iteration(state)

    assert state.iteration_history == [
        {
            "iteration": 1,
            "status": "completed",
            "summary": "",
            "outputs": {},
            "started_at": None,
            "completed_at": None,
            "error": "",
            "rejection_count": 1,
            "rejection_reason": "schema mismatch",
            "reject_upstream_count": 2,
            "rejection_history": [
                {"retry_index": 1, "reason": "schema mismatch"}
            ],
        }
    ]
    assert state.rejection_count == 0
    assert state.rejection_reason == ""
    assert state.reject_upstream_count == 0
    assert state.rejection_history == []


def test_loop_session_cleanup_waits_for_save_and_detaches_both_registries(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(session_module, "SESSIONS_DIR", tmp_path)

    async def exercise():
        mixin = WorkflowLoopMixin()
        previous_session = AgentSession(
            session_type="sub",
            session_id="previous",
            task_description="prior loop item",
        )
        previous_session.record.append(
            {"id": "msg-1", "type": "assistant", "content": "history kept"}
        )
        previous_session.start_consumer()
        consumer_task = previous_session._consumer_task
        assert consumer_task is not None
        main_session = AgentSession(session_type="main", session_id="main")
        sub_task_ready = asyncio.Event()

        async def finish_sub_session():
            await sub_task_ready.wait()
            previous_session.record.append(
                {"id": "msg-2", "type": "assistant", "content": "final history"}
            )

        sub_task = asyncio.create_task(finish_sub_session())
        mixin._session_manager = SimpleNamespace(
            main_session_id="main",
            sessions={"main": main_session, "previous": previous_session},
            _sub_tasks={"previous": sub_task},
        )
        _persistence_manager._sessions["previous"] = previous_session
        _persistence_manager._sessions["main"] = main_session
        try:
            cleanup = asyncio.create_task(
                mixin._cleanup_loop_session(
                    NodeExecutionState(node_id="agent", session_id="previous"),
                    2,
                )
            )
            await asyncio.sleep(0)
            assert not cleanup.done()
            assert "previous" in mixin._session_manager.sessions
            assert "previous" in _persistence_manager._sessions

            sub_task_ready.set()
            await cleanup
            await mixin._cleanup_loop_session(
                NodeExecutionState(node_id="main-agent", session_id="main"),
                2,
            )

            assert mixin._session_manager.sessions == {"main": main_session}
            assert mixin._session_manager._sub_tasks == {}
            assert "previous" not in _persistence_manager._sessions
            assert _persistence_manager._sessions["main"] is main_session
            assert consumer_task.done()
            assert previous_session._consumer_task is None
            history = json.loads(
                (tmp_path / "previous.json").read_text(encoding="utf-8")
            )
            assert [item["content"] for item in history["record"]] == [
                "history kept",
                "final history",
            ]
        finally:
            _persistence_manager.unregister("previous")
            _persistence_manager.unregister("main")
            await previous_session.stop_consumer()
            if not sub_task.done():
                sub_task.cancel()
                await sub_task

    asyncio.run(exercise())


def test_loop_session_cleanup_save_failure_preserves_dirty_and_registries():
    async def exercise():
        mixin = WorkflowLoopMixin()
        previous_session = AgentSession(
            session_type="sub", session_id="previous-failed"
        )
        previous_session.start_consumer()
        consumer_task = previous_session._consumer_task
        assert consumer_task is not None

        async def completed_sub_task():
            await asyncio.sleep(0)

        sub_task = asyncio.create_task(completed_sub_task())
        mixin._session_manager = SimpleNamespace(
            main_session_id="main",
            sessions={"previous-failed": previous_session},
            _sub_tasks={"previous-failed": sub_task},
        )
        _persistence_manager._sessions["previous-failed"] = previous_session

        def fail_save():
            raise OSError("disk unavailable")

        previous_session.save = fail_save
        try:
            with pytest.raises(OSError, match="disk unavailable"):
                await mixin._cleanup_loop_session(
                    NodeExecutionState(
                        node_id="agent", session_id="previous-failed"
                    ),
                    3,
                )

            assert previous_session._save_dirty is True
            assert (
                mixin._session_manager.sessions["previous-failed"]
                is previous_session
            )
            assert (
                _persistence_manager._sessions["previous-failed"]
                is previous_session
            )
            assert mixin._session_manager._sub_tasks["previous-failed"] is sub_task
            assert consumer_task.done()
            assert previous_session._consumer_task is None
        finally:
            _persistence_manager.unregister("previous-failed")
            mixin._session_manager.sessions.pop("previous-failed", None)
            mixin._session_manager._sub_tasks.pop("previous-failed", None)
            await previous_session.stop_consumer()
            await sub_task

    asyncio.run(exercise())


def test_force_save_remains_tolerant_but_dirty_after_failure():
    async def exercise():
        session = AgentSession(session_type="sub", session_id="tolerant-save")

        def fail_save():
            raise OSError("temporary disk failure")

        session.save = fail_save
        await session.async_save(force=True)
        assert session._save_dirty is True

    asyncio.run(exercise())


def test_loop_gateway_rejects_oversized_sequence_without_truncation():
    mixin = WorkflowLoopMixin()
    definition = SimpleNamespace(
        edges=[
            SimpleNamespace(
                source="gateway",
                target="agent",
                condition={"expression": "for item in jobs"},
            )
        ]
    )
    task = SimpleNamespace(
        task_id="task",
        parameter_values={"jobs": json.dumps(list(range(101)))},
        node_states={},
    )
    step = {
        "loop_body_nodes": ["agent"],
        "exit_target": "done",
        "continue_target": "agent",
        "gateway_id": "gateway",
    }

    result = asyncio.run(
        mixin._execute_loop_gateway(
            definition,
            task,
            step,
            set(),
            None,
            "",
            None,
            False,
            SimpleNamespace(),
        )
    )

    assert result == "failed"
    assert task.node_states == {}
