"""Readiness snapshots and ready-only routing for Workflow Executor members."""

from __future__ import annotations

import asyncio
import dataclasses
import shutil
from pathlib import Path

import pytest

import src.workflow.executor_supervisor as executor_supervisor_module
from src.workflow.executor_client import (
    ExecutorUnavailable,
    WorkflowExecutorClient,
)
from src.workflow.executor_pool import WorkflowExecutorPool
from src.workflow.executor_process import force_kill_pid, forced_exit_code
from src.workflow.executor_protocol import ExecutorIdentity
from src.workflow.executor_transport import LOOPBACK_HOST, LoopbackEndpoint
from src.workflow.executor_supervisor import (
    STATUS_READY,
    STATUS_RESTARTING,
    STATUS_STARTING,
    STATUS_STOPPED,
    STATUS_STOPPING,
    ExecutorReadinessSnapshot,
    WorkflowExecutorSupervisor,
)


class _ControllableProcess:
    def __init__(self, pid: int):
        self.pid = pid
        self.returncode = None
        self._finished = asyncio.Event()

    async def wait(self):
        await self._finished.wait()
        return self.returncode

    def crash(self, code: int) -> None:
        self.returncode = code
        self._finished.set()

    def terminate(self) -> None:
        if self.returncode is None:
            self.returncode = -15
            self._finished.set()

    def kill(self) -> None:
        if self.returncode is None:
            self.returncode = -9
            self._finished.set()


class _FakeMember:
    def __init__(self, executor_id: str, *, ready: bool, epoch: str):
        self.executor_id = executor_id
        self.is_ready = ready
        self.client = WorkflowExecutorClient(
            LoopbackEndpoint(LOOPBACK_HOST, 1),
            ExecutorIdentity(executor_id, epoch),
            auth_token="test-auth-token",
        )


async def _wait_until(predicate, *, timeout: float, message: str) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError(message)


def _started_pool(tmp_path: Path, members: dict[str, _FakeMember]) -> WorkflowExecutorPool:
    pool = WorkflowExecutorPool(len(members), data_dir=tmp_path)
    pool._supervisors = {
        executor_id: members[executor_id] for executor_id in pool.executor_ids
    }
    pool._started = True
    return pool


def test_supervisor_snapshot_starts_stopped_and_is_read_only():
    supervisor = WorkflowExecutorSupervisor()
    snapshot = supervisor.snapshot()

    assert snapshot == ExecutorReadinessSnapshot(
        status=STATUS_STOPPED,
        is_ready=False,
        restart_count=0,
        last_exit_code=None,
        last_ready_at=None,
    )
    assert supervisor.status == STATUS_STOPPED
    assert supervisor.is_ready is False
    assert supervisor.restart_count == 0
    assert supervisor.last_exit_code is None
    assert supervisor.last_ready_at is None
    with pytest.raises(dataclasses.FrozenInstanceError):
        snapshot.status = STATUS_READY


def test_supervisor_ready_state_rejects_an_already_exited_process():
    supervisor = WorkflowExecutorSupervisor()
    process = _ControllableProcess(pid=99999123)
    supervisor._process = process
    supervisor._set_status(STATUS_READY)

    assert supervisor.is_ready is True
    process.returncode = -9

    assert supervisor.status == STATUS_READY
    assert supervisor.is_ready is False
    assert supervisor.snapshot().is_ready is False


def test_supervisor_state_machine_covers_start_crash_restart_and_stop(tmp_path):
    async def scenario():
        supervisor = WorkflowExecutorSupervisor(
            lease_path=tmp_path / "workflow-executor.lock",
            restart_backoff_max=0.001,
        )
        supervisor._reap_process_tree = lambda: None
        process = None
        spawn_count = 0
        first_spawn_started = asyncio.Event()
        first_spawn_gate = asyncio.Event()
        failed_restart_spawn = asyncio.Event()
        allow_restart_success = asyncio.Event()
        restart_spawn_started = asyncio.Event()
        restart_spawn_gate = asyncio.Event()
        stopping_snapshots = []

        async def spawn():
            nonlocal process, spawn_count
            spawn_count += 1
            if spawn_count == 1:
                first_spawn_started.set()
                await first_spawn_gate.wait()
            else:
                if not allow_restart_success.is_set():
                    spawn_count -= 1
                    failed_restart_spawn.set()
                    raise RuntimeError("transient spawn failure")
                restart_spawn_started.set()
                await restart_spawn_gate.wait()
            process = _ControllableProcess(pid=99999100 + spawn_count)
            original_terminate = process.terminate

            def terminate_and_record():
                stopping_snapshots.append(supervisor.snapshot())
                original_terminate()

            process.terminate = terminate_and_record
            supervisor._process = process
            identity = ExecutorIdentity(supervisor.executor_id, f"epoch-{spawn_count}")
            if supervisor.client is None:
                supervisor.client = WorkflowExecutorClient(
                    LoopbackEndpoint(LOOPBACK_HOST, 1),
                    identity,
                    auth_token="test-auth-token",
                )
            else:
                supervisor.client.update_identity(identity)

        supervisor._spawn = spawn
        start_task = asyncio.create_task(supervisor.start())
        await first_spawn_started.wait()
        starting = supervisor.snapshot()
        assert starting.status == STATUS_STARTING
        assert starting.is_ready is False
        assert starting.restart_count == 0
        assert starting.last_ready_at is None

        first_spawn_gate.set()
        await start_task
        first_ready = supervisor.snapshot()
        assert first_ready.status == STATUS_READY
        assert first_ready.is_ready is True
        assert first_ready.restart_count == 0
        assert first_ready.last_exit_code is None
        assert first_ready.last_ready_at is not None
        first_ready_at = first_ready.last_ready_at

        process.crash(-9)
        await _wait_until(
            lambda: supervisor.status == STATUS_RESTARTING,
            timeout=1.0,
            message="supervisor to enter restarting after crash",
        )
        crashed = supervisor.snapshot()
        assert crashed.is_ready is False
        assert crashed.restart_count == 1
        assert crashed.last_exit_code == -9
        assert crashed.last_ready_at == first_ready_at

        await failed_restart_spawn.wait()
        assert supervisor.status == STATUS_RESTARTING
        assert supervisor.restart_count == 1
        allow_restart_success.set()
        await restart_spawn_started.wait()
        assert supervisor.snapshot().restart_count == 1
        assert supervisor.is_ready is False

        await asyncio.sleep(0.02)
        restart_spawn_gate.set()
        await _wait_until(
            lambda: supervisor.status == STATUS_READY,
            timeout=1.0,
            message="supervisor to become ready after restart ping",
        )
        restarted = supervisor.snapshot()
        assert restarted.is_ready is True
        assert restarted.restart_count == 1
        assert restarted.last_exit_code == -9
        assert restarted.last_ready_at >= first_ready_at
        assert supervisor.identity.epoch == "epoch-2"

        await supervisor.stop()
        stopped = supervisor.snapshot()
        assert stopped.status == STATUS_STOPPED
        assert stopped.is_ready is False
        assert stopped.restart_count == 1
        assert stopped.last_exit_code == -15
        assert stopping_snapshots
        assert stopping_snapshots[0].status == STATUS_STOPPING
        assert stopping_snapshots[0].is_ready is False

    asyncio.run(scenario())


def test_select_client_round_robins_only_ready_members(tmp_path):
    first = _FakeMember("workflow-executor-0", ready=True, epoch="epoch-0")
    second = _FakeMember("workflow-executor-1", ready=True, epoch="epoch-1")
    pool = _started_pool(tmp_path, {
        "workflow-executor-0": first,
        "workflow-executor-1": second,
    })

    selected = [pool.select_client(f"task-{index}") for index in range(4)]
    assert [client.identity.executor_id for client in selected] == [
        "workflow-executor-0",
        "workflow-executor-1",
        "workflow-executor-0",
        "workflow-executor-1",
    ]


def test_select_client_skips_member_that_has_not_answered_ping(tmp_path):
    ready = _FakeMember("workflow-executor-0", ready=True, epoch="ready-epoch")
    starting = _FakeMember(
        "workflow-executor-1",
        ready=False,
        epoch="pre-ping-epoch",
    )
    pool = _started_pool(tmp_path, {
        "workflow-executor-0": ready,
        "workflow-executor-1": starting,
    })

    selected = [pool.select_client(f"task-{index}") for index in range(6)]
    assert {client.identity.executor_id for client in selected} == {
        "workflow-executor-0",
    }
    assert {client.identity.epoch for client in selected} == {"ready-epoch"}

    sticky = pool.client_for("workflow-executor-1")
    assert sticky is starting.client
    assert sticky.identity.epoch == "pre-ping-epoch"


def test_select_client_raises_when_no_member_is_ready(tmp_path):
    pool = _started_pool(tmp_path, {
        "workflow-executor-0": _FakeMember(
            "workflow-executor-0", ready=False, epoch="starting-epoch",
        ),
        "workflow-executor-1": _FakeMember(
            "workflow-executor-1", ready=False, epoch="restarting-epoch",
        ),
    })

    with pytest.raises(ExecutorUnavailable, match="No ready Workflow Executor"):
        pool.select_client("task-new")

    assert pool.client_for("workflow-executor-0").identity.epoch == "starting-epoch"
    assert pool.client_for("workflow-executor-1").identity.epoch == "restarting-epoch"


def test_select_client_requires_started_pool_and_keeps_unknown_ids_unavailable(tmp_path):
    pool = WorkflowExecutorPool(1, data_dir=tmp_path)
    with pytest.raises(ExecutorUnavailable, match="has not started"):
        pool.select_client("task-new")

    member = _FakeMember("workflow-executor-0", ready=True, epoch="epoch-0")
    started = _started_pool(tmp_path, {"workflow-executor-0": member})
    with pytest.raises(ExecutorUnavailable, match="not in the active pool"):
        started.client_for("workflow-executor-9")


def test_select_client_resumes_round_robin_after_member_becomes_ready(tmp_path):
    first = _FakeMember("workflow-executor-0", ready=True, epoch="epoch-0")
    second = _FakeMember("workflow-executor-1", ready=False, epoch="old-epoch")
    pool = _started_pool(tmp_path, {
        "workflow-executor-0": first,
        "workflow-executor-1": second,
    })

    while_down = [pool.select_client("down").identity.executor_id for _ in range(3)]
    assert while_down == ["workflow-executor-0"] * 3

    second.is_ready = True
    second.client.update_identity(
        ExecutorIdentity("workflow-executor-1", "new-ready-epoch")
    )
    recovered = [pool.select_client("up").identity for _ in range(4)]
    assert {identity.executor_id for identity in recovered} == {
        "workflow-executor-0",
        "workflow-executor-1",
    }
    assert any(identity.epoch == "new-ready-epoch" for identity in recovered)


def test_real_pool_does_not_bind_before_restart_ping(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    config_dir = tmp_path / "config"
    logs_dir = tmp_path / "logs"
    for directory in (data_dir, logs_dir):
        directory.mkdir()
    shutil.copytree(Path(__file__).resolve().parents[1] / "config", config_dir)
    monkeypatch.setenv("DETERMINFLOW_DATA_DIR", str(data_dir))
    monkeypatch.setenv("DETERMINFLOW_CONFIG_DIR", str(config_dir))
    monkeypatch.setenv("DETERMINFLOW_LOGS_DIR", str(logs_dir))
    monkeypatch.setenv("DETERMINFLOW_WORKFLOW_EXECUTOR_MODE", "inline")
    monkeypatch.setattr(executor_supervisor_module, "DATA_DIR", data_dir)

    async def scenario():
        async def discard_event(_channel, _event):
            return None

        handoff_started = asyncio.Event()
        allow_handoff = asyncio.Event()

        async def complete_handoff(_previous, _current):
            handoff_started.set()
            await allow_handoff.wait()

        pool = WorkflowExecutorPool(
            2,
            data_dir=data_dir,
            startup_timeout=30.0,
            shutdown_timeout=10.0,
            restart_backoff_max=0.1,
            on_restart=complete_handoff,
            event_handler=discard_event,
        )
        ping_started = asyncio.Event()
        allow_ping = asyncio.Event()
        await pool.start()
        try:
            healthy = pool._supervisors["workflow-executor-0"]
            victim = pool._supervisors["workflow-executor-1"]
            assert healthy.snapshot().status == STATUS_READY
            assert victim.snapshot().status == STATUS_READY
            assert healthy.is_ready and victim.is_ready
            original_victim_epoch = victim.identity.epoch
            first_ready_at = victim.last_ready_at
            original_call = victim.client.call

            async def gated_call(operation, **arguments):
                if operation == "ping" and victim.status == STATUS_RESTARTING:
                    ping_started.set()
                    await allow_ping.wait()
                return await original_call(operation, **arguments)

            victim.client.call = gated_call
            assert victim.pid is not None
            force_kill_pid(victim.pid)

            await _wait_until(
                lambda: (
                    victim.status == STATUS_RESTARTING
                    and not victim.is_ready
                    and ping_started.is_set()
                    and victim.identity.epoch != original_victim_epoch
                ),
                timeout=30.0,
                message="restarting member to rotate identity before ping succeeds",
            )
            assert victim.restart_count == 1
            assert victim.last_exit_code == forced_exit_code()
            assert victim.last_ready_at == first_ready_at

            assigned = [
                pool.select_client(f"task-{index}").identity for index in range(8)
            ]
            assert {identity.executor_id for identity in assigned} == {
                "workflow-executor-0",
            }
            sticky = pool.client_for("workflow-executor-1")
            assert sticky.identity.executor_id == "workflow-executor-1"
            assert sticky.identity.epoch == victim.identity.epoch
            assert sticky.identity.epoch != original_victim_epoch

            allow_ping.set()
            await _wait_until(
                lambda: handoff_started.is_set(),
                timeout=30.0,
                message="restart handoff to begin after ping",
            )
            assert victim.status == STATUS_RESTARTING
            assert victim.is_ready is False
            while_handoff = [
                pool.select_client(f"handoff-{index}").identity.executor_id
                for index in range(4)
            ]
            assert set(while_handoff) == {"workflow-executor-0"}

            allow_handoff.set()
            await _wait_until(
                lambda: victim.is_ready and victim.status == STATUS_READY,
                timeout=30.0,
                message="restarted member to become ready after ping",
            )
            assert victim.restart_count == 1
            assert victim.last_ready_at is not None
            assert victim.last_ready_at >= first_ready_at

            recovered = [
                pool.select_client(f"recovered-{index}").identity.executor_id
                for index in range(8)
            ]
            assert set(recovered) == {
                "workflow-executor-0",
                "workflow-executor-1",
            }
        finally:
            allow_ping.set()
            allow_handoff.set()
            await pool.stop()

        assert victim.status == STATUS_STOPPED
        assert victim.is_ready is False
        assert healthy.status == STATUS_STOPPED

    asyncio.run(scenario())
