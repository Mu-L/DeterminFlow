import asyncio

from src.roundtable.models import RoundtableSession, Seat
from src.roundtable.runner import RoundtableManager
from src.web.event_bus import EventBus, event_bus


def _session() -> RoundtableSession:
    return RoundtableSession(
        topic="恢复测试",
        seats=[Seat(seat_id="seat-1", role_name="研究员", system_prompt="分析")],
    )


def test_roundtable_detail_exposes_active_turn_draft() -> None:
    session = _session()

    assert session.to_dict()["active_turn"] is None

    session.begin_active_turn(session.seats[0], round_number=2)
    session.append_active_turn("第一段")
    session.append_active_turn("第二段")

    assert session.to_dict()["active_turn"] == {
        "seat_id": "seat-1",
        "speaker_name": "研究员",
        "content": "第一段第二段",
        "round": 2,
    }

    session.end_active_turn()
    assert session.to_dict()["active_turn"] is None


def test_roundtable_detail_flattens_summary_fields_for_frontend_restore() -> None:
    session = _session()
    detail = session.to_dict()

    assert detail["seat_count"] == 1
    assert detail["current_round"] == 1
    assert detail["max_rounds"] == 3
    assert detail["transcript_count"] == 0


def test_roundtable_active_turn_is_not_restored_after_process_restart() -> None:
    session = _session()
    session.begin_active_turn(session.seats[0], round_number=1)
    session.append_active_turn("未完成草稿")

    restored = RoundtableSession.from_dict(session.to_dict())

    assert restored.active_turn is None


def test_interrupted_visible_turn_is_committed_to_history(monkeypatch) -> None:
    session = _session()
    session.begin_active_turn(session.seats[0], round_number=2)
    session.append_active_turn("已经展示的部分回复")
    emitted: list[dict] = []

    async def capture(event: dict) -> None:
        emitted.append(event)

    monkeypatch.setattr(event_bus, "emit_chat", capture)
    asyncio.run(RoundtableManager()._finalize_interrupted_turn(session))

    assert session.active_turn is None
    assert session.transcript[-1].content == "已经展示的部分回复"
    assert emitted == [{
        "type": "rt_turn_end",
        "roundtable_id": session.session_id,
        "seat_id": "seat-1",
        "speaker_name": "研究员",
        "round": 2,
        "full_content": "已经展示的部分回复",
        "interrupted": True,
    }]


def test_delete_paused_roundtable_waits_for_cancelled_runner_before_unlink(
    monkeypatch,
    tmp_path,
) -> None:
    async def scenario():
        monkeypatch.setattr("src.roundtable.runner.SESSIONS_DIR", tmp_path)
        session = _session()
        session.status = "paused"
        manager = RoundtableManager()
        manager.sessions[session.session_id] = session
        session_file = tmp_path / f"{session.session_id}.json"
        session_file.write_text("original", encoding="utf-8")
        cancel_seen = asyncio.Event()
        allow_late_save = asyncio.Event()
        never_finish = asyncio.Event()

        async def paused_runner():
            try:
                await never_finish.wait()
            except asyncio.CancelledError:
                cancel_seen.set()
                await allow_late_save.wait()
                session_file.write_text("late save", encoding="utf-8")

        runner_task = asyncio.create_task(paused_runner())
        await asyncio.sleep(0)
        manager._tasks[session.session_id] = runner_task
        delete_task = asyncio.create_task(manager.delete(session.session_id))
        await cancel_seen.wait()
        await asyncio.sleep(0)
        waited_for_runner = not delete_task.done()
        allow_late_save.set()
        result = await delete_task
        await runner_task
        return manager, session, session_file, result, waited_for_runner

    manager, session, session_file, result, waited_for_runner = asyncio.run(scenario())
    assert waited_for_runner is True
    assert result["success"] is True
    assert session.session_id not in manager.sessions
    assert session_file.exists() is False


def test_roundtable_events_have_snapshot_watermark() -> None:
    async def scenario():
        bus = EventBus()
        await bus.emit_chat({
            "type": "rt_turn_start",
            "roundtable_id": "roundtable-1",
        })
        await bus.emit_chat({
            "type": "rt_token",
            "roundtable_id": "roundtable-1",
            "content": "partial",
        })
        event = {
            "type": "rt_turn_end",
            "roundtable_id": "roundtable-1",
            "full_content": "partial",
        }
        await bus.emit_chat(event)
        return bus.get_roundtable_revision("roundtable-1"), bus._event_log[-1]

    revision, final_event = asyncio.run(scenario())
    assert revision == 3
    assert final_event["roundtable_revision"] == 3
