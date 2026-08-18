from __future__ import annotations

import asyncio
import json
import os
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.workflow.executor_client import ExecutorUnavailable, WorkflowExecutorClient
from src.workflow.executor_events import ControllerEventReceiver, ExecutorEventForwarder
from src.workflow.executor_observability import (
    build_executor_runtime_status,
    collect_workflow_executor_status,
    count_active_tasks,
    reset_controller_event_bridge_counters,
    sanitize_member_status,
)
from src.workflow.executor_protocol import (
    PROTOCOL_VERSION,
    READONLY_OPERATIONS,
    STATUS_OPERATION,
    ExecutorIdentity,
    ExecutorProtocolError,
    validate_executor_generation,
    validate_request,
)
from src.workflow.executor_server import WorkflowExecutorServer
from src.web.api_routes import router


def _request(identity: ExecutorIdentity, **overrides):
    request = {
        "protocol_version": PROTOCOL_VERSION,
        "request_id": "request-1",
        "executor_id": identity.executor_id,
        "executor_epoch": identity.epoch,
        "operation": STATUS_OPERATION,
        "arguments": {},
    }
    request.update(overrides)
    return request


class _LiveTask:
    def done(self) -> bool:
        return False


class _DoneTask:
    def done(self) -> bool:
        return True


class _Manager:
    def __init__(self) -> None:
        self._running_tasks = {"live": _LiveTask(), "done": _DoneTask()}
        self.status_called = False

    def status(self) -> dict:
        self.status_called = True
        raise AssertionError("status RPC must not call workflow_manager.status")


class _Session:
    def get_summary(self) -> dict:
        return {"session_id": "s1"}


class _SessionManager:
    def get_main_session(self):
        return _Session()

    def get_main_session_summaries(self):
        return []

    def get_active_sub_count(self) -> int:
        return 0

    def get_total_session_count(self) -> int:
        return 1


class _PromptManager:
    def get_prompt(self) -> dict:
        return {"version": 1, "last_modified": ""}


class _Mcp:
    connections: dict = {}

    def get_tools(self) -> list:
        return []


class _FakeClient:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.calls: list[str] = []

    async def call(self, operation: str, **_arguments):
        self.calls.append(operation)
        if self.error is not None:
            raise self.error
        return self.result


class _IdentityPool:
    def __init__(self, members: dict[str, tuple[ExecutorIdentity, int | None, _FakeClient]]):
        self._members = members
        self.executor_ids = tuple(members)

    @property
    def identities(self) -> tuple[ExecutorIdentity, ...]:
        return tuple(item[0] for item in self._members.values())

    @property
    def member_pids(self) -> dict[str, int | None]:
        return {executor_id: item[1] for executor_id, item in self._members.items()}

    def client_for(self, executor_id: str) -> _FakeClient:
        return self._members[executor_id][2]


def _status_app(*, pool=None) -> FastAPI:
    app = FastAPI()
    app.state.session_manager = _SessionManager()
    app.state.prompt_manager = _PromptManager()
    app.state.mcp_client = _Mcp()
    app.state.workflow_manager = None
    app.state.workflow_executor_pool = pool
    app.include_router(router)
    return app


def _runtime_status(identity: ExecutorIdentity, **overrides) -> dict:
    payload = {
        "executor_id": identity.executor_id,
        "epoch": identity.epoch,
        "pid": 4242,
        "uptime": 1.5,
        "active_task_count": 2,
        "rpc": {
            "received": 4,
            "succeeded": 3,
            "failed": 0,
            "protocol_errors": 1,
            "by_operation": {STATUS_OPERATION: 1, "ping": 3},
        },
        "event_bridge": {
            "forwarded": 5,
            "reconnect": 1,
            "failure": 0,
            "oversized": 0,
            "malformed_or_stale": 2,
        },
    }
    payload.update(overrides)
    return payload


def test_status_is_readonly_allowlisted_and_generation_checked():
    identity = ExecutorIdentity("workflow-executor-0", "epoch-2")

    assert STATUS_OPERATION in READONLY_OPERATIONS
    validated = validate_request(_request(identity), expected_identity=identity)
    assert validated["operation"] == STATUS_OPERATION

    with pytest.raises(ExecutorProtocolError, match="epoch"):
        validate_executor_generation(
            _request(identity, executor_epoch="epoch-1"),
            expected_identity=identity,
        )
    with pytest.raises(ExecutorProtocolError, match="epoch"):
        validate_request(
            _request(identity, executor_epoch="epoch-1"),
            expected_identity=identity,
        )
    with pytest.raises(ExecutorProtocolError, match="operation"):
        validate_request(
            _request(identity, operation="dump_env"),
            expected_identity=identity,
        )


def test_runtime_status_counts_live_tasks_and_omits_private_fields():
    identity = ExecutorIdentity("workflow-executor-0", "epoch-1")
    manager = _Manager()
    snapshot = build_executor_runtime_status(
        identity=identity,
        started_monotonic=100.0,
        workflow_manager=manager,
        rpc_counts={"received": 2, "succeeded": 2, "failed": 0, "by_operation": {"ping": 2}},
        event_bridge={"forwarded": 1, "socket_path": "/tmp/events.sock"},
        now_monotonic=103.25,
        pid=99,
    )

    assert count_active_tasks(manager) == 1
    assert snapshot["executor_id"] == identity.executor_id
    assert snapshot["epoch"] == identity.epoch
    assert snapshot["pid"] == 99
    assert snapshot["uptime"] == 3.25
    assert snapshot["active_task_count"] == 1
    assert snapshot["rpc"]["received"] == 2
    assert snapshot["event_bridge"]["forwarded"] == 1
    assert "socket_path" not in snapshot
    assert "socket_path" not in snapshot["event_bridge"]


def test_sanitize_member_status_strips_socket_env_credentials_and_task_body():
    dirty = {
        "executor_id": "workflow-executor-0",
        "executor_epoch": "epoch-1",
        "pid": 7,
        "uptime_seconds": 9,
        "active_task_count": 1,
        "socket_path": "/tmp/determinflow-executor/rpc.sock",
        "env": {"DETERMINFLOW_API_KEY": "super-secret", "PATH": "/usr/bin"},
        "password": "hunter2",
        "task": {"prompt": "chapter text that must not leak", "task_id": "task-9"},
        "reason": "/tmp/determinflow-executor/rpc.sock secret=abc",
    }
    clean = sanitize_member_status(dirty)
    dumped = json.dumps(clean)

    assert clean["epoch"] == "epoch-1"
    assert clean["uptime"] == 9.0
    assert "/tmp" not in dumped
    assert "rpc.sock" not in dumped
    assert "super-secret" not in dumped
    assert "hunter2" not in dumped
    assert "chapter text" not in dumped
    assert "task-9" not in dumped
    assert "reason" not in clean


def test_executor_server_status_rpc_is_generation_checked_and_not_delegated():
    async def scenario():
        runtime_dir = Path(tempfile.mkdtemp(prefix="df-executor-status-"))
        socket_path = runtime_dir / "rpc.sock"
        identity = ExecutorIdentity("workflow-executor-0", "epoch-2")
        manager = _Manager()
        server = WorkflowExecutorServer(socket_path, identity, manager)
        await server.start()
        try:
            client = WorkflowExecutorClient(socket_path, identity)
            stale = WorkflowExecutorClient(
                socket_path,
                ExecutorIdentity("workflow-executor-0", "epoch-1"),
            )
            await client.call("ping")
            with pytest.raises(ExecutorUnavailable, match="stale"):
                await stale.call(STATUS_OPERATION)
            status = await client.call(STATUS_OPERATION)
        finally:
            await server.close()
            runtime_dir.rmdir()

        assert manager.status_called is False
        assert status["executor_id"] == identity.executor_id
        assert status["epoch"] == identity.epoch
        assert status["pid"] == os.getpid()
        assert status["uptime"] >= 0
        assert status["active_task_count"] == 1
        assert status["rpc"]["received"] >= 2
        assert status["rpc"]["succeeded"] >= 2
        assert status["rpc"]["by_operation"]["ping"] == 1
        assert status["rpc"]["by_operation"][STATUS_OPERATION] == 1
        assert status["rpc"]["protocol_errors"] == 1
        assert set(status["event_bridge"]) == {
            "forwarded", "reconnect", "failure", "oversized", "malformed_or_stale",
        }

    asyncio.run(scenario())


def test_event_bridge_records_applicable_counters(monkeypatch):
    reset_controller_event_bridge_counters()

    async def scenario():
        import src.workflow.executor_events as events_module

        runtime_dir = Path(tempfile.mkdtemp(prefix="df-executor-obs-events-"))
        socket_path = runtime_dir / "events.sock"
        received = []
        identity = ExecutorIdentity("workflow-executor-0", "epoch-2")

        async def handler(channel, event):
            received.append((channel, event))

        receiver = ControllerEventReceiver(socket_path, identity, handler)
        await receiver.start()
        current = ExecutorEventForwarder(socket_path, identity)
        stale = ExecutorEventForwarder(
            socket_path,
            ExecutorIdentity("workflow-executor-0", "epoch-1"),
        )
        missing = ExecutorEventForwarder(runtime_dir / "missing.sock", identity)
        try:
            await current.emit("events", {"type": "wf_task_update", "task_id": "t1"})
            await current.close()
            await current.emit("events", {"type": "wf_task_update", "task_id": "t2"})
            await stale.emit("events", {"type": "wf_task_update", "task_id": "old"})
            await asyncio.sleep(0.05)
            assert [item[1]["task_id"] for item in received] == ["t1", "t2"]
            assert current.stats()["forwarded"] == 2
            assert current.stats()["reconnect"] == 1
            assert receiver.stats()["forwarded"] == 2
            assert receiver.stats()["malformed_or_stale"] >= 1

            await current.emit("events", {"type": "bad", "payload": object()})
            await missing.emit("events", {"type": "wf_task_update", "task_id": "gone"})
            monkeypatch.setattr(events_module, "MAX_FRAME_BYTES", 64)
            await current.emit("events", {"type": "wf_task_update", "stdout": "x" * 128})
            writer = None
            try:
                _, writer = await asyncio.open_unix_connection(socket_path)
                writer.write(b"x" * 80 + b"\n")
                await writer.drain()
            finally:
                if writer is not None:
                    writer.close()
                    await writer.wait_closed()
            await asyncio.sleep(0.05)
        finally:
            await current.close()
            await stale.close()
            await missing.close()
            await receiver.close()
            runtime_dir.rmdir()

        assert current.stats()["malformed_or_stale"] == 1
        assert current.stats()["oversized"] == 1
        assert missing.stats()["failure"] == 1
        assert receiver.stats()["oversized"] >= 1
        from src.workflow.executor_observability import controller_event_bridge_counters
        assert controller_event_bridge_counters.snapshot()["forwarded"] == 2
        assert controller_event_bridge_counters.snapshot()["malformed_or_stale"] >= 1
        assert controller_event_bridge_counters.snapshot()["oversized"] >= 1

    asyncio.run(scenario())


def test_collect_status_uses_identities_member_pids_and_client_for():
    identity = ExecutorIdentity("workflow-executor-0", "epoch-9")
    client = _FakeClient(_runtime_status(identity))
    pool = _IdentityPool({
        identity.executor_id: (identity, 321, client),
    })

    snapshot = asyncio.run(collect_workflow_executor_status(
        pool, mode="process", configured_count=1,
    ))

    assert client.calls == [STATUS_OPERATION]
    assert snapshot["mode"] == "process"
    assert snapshot["available"] is True
    assert snapshot["degraded"] is False
    assert snapshot["member_count"] == 1
    assert snapshot["reachable_count"] == 1
    assert snapshot["ready_count"] == 0
    member = snapshot["members"][0]
    assert member["executor_id"] == identity.executor_id
    assert member["epoch"] == "epoch-9"
    assert member["pid"] == 4242
    assert member["uptime"] == 1.5
    assert member["active_task_count"] == 2
    assert member["rpc"]["received"] == 4
    assert member["reachable"] is True


def test_collect_status_prefers_supervisor_snapshot_when_present():
    identity = ExecutorIdentity("workflow-executor-1", "epoch-4")

    class Supervisor:
        def status_snapshot(self):
            return _runtime_status(identity, pid=88, active_task_count=3)

    class Pool:
        executor_ids = (identity.executor_id,)
        identities = (identity,)
        member_pids = {identity.executor_id: 88}
        _supervisors = {identity.executor_id: Supervisor()}

        def client_for(self, _executor_id: str):
            raise AssertionError("client_for must not run when snapshot exists")

    snapshot = asyncio.run(collect_workflow_executor_status(
        Pool(), mode="process", configured_count=1,
    ))
    assert snapshot["members"][0]["pid"] == 88
    assert snapshot["members"][0]["active_task_count"] == 3
    assert snapshot["members"][0]["reachable"] is True


def test_collect_status_falls_back_to_client_when_supervisor_snapshot_missing():
    identity = ExecutorIdentity("workflow-executor-0", "epoch-3")
    client = _FakeClient(_runtime_status(identity, pid=17))

    class Supervisor:
        pass

    class Pool:
        executor_ids = (identity.executor_id,)
        identities = (identity,)
        member_pids = {identity.executor_id: 17}
        _supervisors = {identity.executor_id: Supervisor()}

        def client_for(self, _executor_id: str):
            return client

    snapshot = asyncio.run(collect_workflow_executor_status(
        Pool(), mode="process", configured_count=1,
    ))
    assert client.calls == [STATUS_OPERATION]
    assert snapshot["members"][0]["pid"] == 17
    assert snapshot["members"][0]["reachable"] is True


def test_collect_status_uses_local_readiness_and_skips_unready_rpc():
    identity = ExecutorIdentity("workflow-executor-0", "epoch-3")
    client = _FakeClient(_runtime_status(identity, pid=17))

    class Supervisor:
        def snapshot(self):
            return SimpleNamespace(
                status="restarting",
                is_ready=False,
                restart_count=2,
                last_exit_code=-9,
                last_ready_at=123.5,
            )

    class Pool:
        executor_ids = (identity.executor_id,)
        identities = (identity,)
        member_pids = {identity.executor_id: 17}

        def supervisor_for(self, _executor_id: str):
            return Supervisor()

        def client_for(self, _executor_id: str):
            return client

    snapshot = asyncio.run(collect_workflow_executor_status(
        Pool(), mode="process", configured_count=1,
    ))
    member = snapshot["members"][0]

    assert client.calls == []
    assert snapshot["degraded"] is True
    assert snapshot["ready_count"] == 0
    assert snapshot["reason"] == "not_ready"
    assert member["status"] == "restarting"
    assert member["is_ready"] is False
    assert member["restart_count"] == 2
    assert member["last_exit_code"] == -9
    assert member["last_ready_at"] == 123.5
    assert member["reason"] == "not_ready"


def test_collect_status_merges_ready_metadata_with_runtime_rpc():
    identity = ExecutorIdentity("workflow-executor-0", "epoch-3")
    client = _FakeClient(_runtime_status(identity, pid=17))

    class Supervisor:
        def snapshot(self):
            return SimpleNamespace(
                status="ready",
                is_ready=True,
                restart_count=1,
                last_exit_code=-9,
                last_ready_at=321.25,
            )

    class Pool:
        executor_ids = (identity.executor_id,)
        identities = (identity,)
        member_pids = {identity.executor_id: 17}

        def supervisor_for(self, _executor_id: str):
            return Supervisor()

        def client_for(self, _executor_id: str):
            return client

    snapshot = asyncio.run(collect_workflow_executor_status(
        Pool(), mode="process", configured_count=1,
    ))
    member = snapshot["members"][0]

    assert client.calls == [STATUS_OPERATION]
    assert snapshot["degraded"] is False
    assert snapshot["ready_count"] == 1
    assert member["status"] == "ready"
    assert member["is_ready"] is True
    assert member["restart_count"] == 1
    assert member["active_task_count"] == 2


def test_collect_status_falls_back_to_client_when_supervisor_snapshot_raises():
    identity = ExecutorIdentity("workflow-executor-0", "epoch-3")
    client = _FakeClient(_runtime_status(identity, pid=19))

    class Supervisor:
        def status_snapshot(self):
            raise RuntimeError("snapshot unavailable")

    class Pool:
        executor_ids = (identity.executor_id,)
        identities = (identity,)
        member_pids = {identity.executor_id: 19}
        _supervisors = {identity.executor_id: Supervisor()}

        def client_for(self, _executor_id: str):
            return client

    snapshot = asyncio.run(collect_workflow_executor_status(
        Pool(), mode="process", configured_count=1,
    ))
    assert client.calls == [STATUS_OPERATION]
    assert snapshot["members"][0]["reachable"] is True
    assert snapshot["members"][0]["pid"] == 19


def test_collect_status_projects_pool_snapshot_and_strips_secrets():
    identity = ExecutorIdentity("workflow-executor-0", "epoch-1")

    class Pool:
        def status_snapshot(self):
            return {
                "mode": "process",
                "available": True,
                "socket_path": "/var/run/determinflow/rpc.sock",
                "env": {"TOKEN": "abc"},
                "members": [
                    _runtime_status(
                        identity,
                        socket_path="/var/run/determinflow/rpc.sock",
                        task={"content": "secret chapter"},
                    )
                ],
            }

        def client_for(self, _executor_id: str):
            raise AssertionError("unused")

    snapshot = asyncio.run(collect_workflow_executor_status(
        Pool(), mode="process", configured_count=1,
    ))
    dumped = json.dumps(snapshot)
    assert snapshot["mode"] == "process"
    assert snapshot["members"][0]["executor_id"] == identity.executor_id
    assert "rpc.sock" not in dumped
    assert "secret chapter" not in dumped
    assert "TOKEN" not in dumped


def test_collect_status_returns_degraded_member_when_rpc_fails():
    live = ExecutorIdentity("workflow-executor-0", "epoch-1")
    down = ExecutorIdentity("workflow-executor-1", "epoch-2")
    live_client = _FakeClient(_runtime_status(live, pid=11))
    down_client = _FakeClient(error=ExecutorUnavailable(
        "Workflow Executor unavailable: [Errno 2] /tmp/secret.sock"
    ))
    pool = _IdentityPool({
        live.executor_id: (live, 11, live_client),
        down.executor_id: (down, 22, down_client),
    })

    snapshot = asyncio.run(collect_workflow_executor_status(
        pool, mode="process", configured_count=2,
    ))
    dumped = json.dumps(snapshot)
    by_id = {member["executor_id"]: member for member in snapshot["members"]}

    assert snapshot["degraded"] is True
    assert snapshot["reachable_count"] == 1
    assert by_id[live.executor_id]["reachable"] is True
    assert by_id[down.executor_id]["reachable"] is False
    assert by_id[down.executor_id]["reason"] == "unreachable"
    assert by_id[down.executor_id]["pid"] == 22
    assert "/tmp/secret.sock" not in dumped


def test_collect_status_marks_stale_generation_as_degraded():
    identity = ExecutorIdentity("workflow-executor-0", "epoch-live")
    client = _FakeClient(_runtime_status(identity, epoch="epoch-dead"))
    pool = _IdentityPool({
        identity.executor_id: (identity, 5, client),
    })

    snapshot = asyncio.run(collect_workflow_executor_status(
        pool, mode="process", configured_count=1,
    ))
    member = snapshot["members"][0]
    assert member["reachable"] is False
    assert member["reason"] == "epoch_mismatch"
    assert member["epoch"] == "epoch-live"


def test_collect_status_inline_fallback_is_explicit():
    snapshot = asyncio.run(collect_workflow_executor_status(
        None, mode="inline", configured_count=2,
    ))
    assert snapshot == {
        "mode": "inline",
        "degraded": False,
        "available": False,
        "reason": "inline",
        "configured_count": 2,
        "member_count": 0,
        "reachable_count": 0,
        "ready_count": 0,
        "members": [],
        "event_bridge": {
            "forwarded": 0,
            "reconnect": 0,
            "failure": 0,
            "oversized": 0,
            "malformed_or_stale": 0,
        },
    }


def test_system_status_returns_inline_fallback(monkeypatch):
    monkeypatch.setattr("src.config.WORKFLOW_EXECUTOR_MODE", "inline")
    client = TestClient(_status_app(pool=None))

    response = client.get("/api/system/status")

    assert response.status_code == 200
    body = response.json()["workflow_executor"]
    assert body["mode"] == "inline"
    assert body["reason"] == "inline"
    assert body["members"] == []
    assert body["available"] is False


def test_system_status_returns_process_pool_and_survives_unreachable_member(
    monkeypatch,
):
    monkeypatch.setattr("src.config.WORKFLOW_EXECUTOR_MODE", "process")
    live = ExecutorIdentity("workflow-executor-0", "epoch-1")
    down = ExecutorIdentity("workflow-executor-1", "epoch-2")
    pool = _IdentityPool({
        live.executor_id: (live, 11, _FakeClient(_runtime_status(live, pid=11))),
        down.executor_id: (down, 22, _FakeClient(error=RuntimeError(
            "env=SECRET_TOKEN=/tmp/rpc.sock"
        ))),
    })
    client = TestClient(_status_app(pool=pool))

    response = client.get("/api/system/status")

    assert response.status_code == 200
    body = response.json()["workflow_executor"]
    dumped = json.dumps(body)
    assert body["mode"] == "process"
    assert body["member_count"] == 2
    assert body["reachable_count"] == 1
    assert body["degraded"] is True
    assert "SECRET_TOKEN" not in dumped
    assert "rpc.sock" not in dumped


def test_system_status_returns_degraded_snapshot_when_pool_identities_explode(
    monkeypatch,
):
    monkeypatch.setattr("src.config.WORKFLOW_EXECUTOR_MODE", "process")

    class BrokenPool:
        @property
        def identities(self):
            raise RuntimeError("cannot read /tmp/determinflow/rpc.sock API_KEY=leak")

        def client_for(self, _executor_id: str):
            raise AssertionError("should not be called")

    client = TestClient(_status_app(pool=BrokenPool()))
    response = client.get("/api/system/status")

    assert response.status_code == 200
    body = response.json()["workflow_executor"]
    dumped = json.dumps(response.json())
    assert body["mode"] == "process"
    assert body["degraded"] is True
    assert body["reason"] == "identities_unavailable"
    assert "rpc.sock" not in dumped
    assert "API_KEY" not in dumped
    assert "leak" not in dumped
