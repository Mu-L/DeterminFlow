import asyncio
import json
import os
import socket
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import src.workflow.executor_process as executor_process_module
import src.workflow.manager as workflow_manager_module
import src.workflow.executor_supervisor as executor_supervisor_module
from src.workflow.manager import WorkflowManager
from src.workflow.runtime_models import WorkflowTask

from src.workflow.executor_client import (
    ExecutorUnavailable,
    WorkflowExecutorClient,
)
from src.workflow.executor_process import (
    ExecutorProcessTree,
    force_kill_pid,
    forced_exit_code,
    process_is_alive,
)
from src.workflow.executor_protocol import (
    AUTH_TOKEN_FIELD,
    PROTOCOL_VERSION,
    ExecutorIdentity,
    ExecutorProtocolError,
    response_frame,
    validate_request,
)
from src.workflow.executor_server import WorkflowExecutorServer
from src.workflow.executor_lease import (
    ExecutorLeaseUnavailable,
    ExecutorProcessLease,
)
from src.workflow.executor_pool import (
    ExecutorLeaseGroup,
    WorkflowExecutorPool,
    executor_lease_path,
    load_recorded_executor_ids,
)
from src.workflow.executor_events import (
    ControllerEventReceiver,
    ExecutorEventForwarder,
)
from src.workflow.executor_supervisor import WorkflowExecutorSupervisor
from src.workflow.executor_transport import (
    LOOPBACK_HOST,
    LoopbackEndpoint,
    parse_loopback_endpoint,
    start_loopback_server,
)


TEST_AUTH_TOKEN = "test-auth-token"


def _request(identity: ExecutorIdentity, **overrides):
    request = {
        "protocol_version": PROTOCOL_VERSION,
        "request_id": "request-1",
        "executor_id": identity.executor_id,
        "executor_epoch": identity.epoch,
        AUTH_TOKEN_FIELD: TEST_AUTH_TOKEN,
        "operation": "run_task",
        "arguments": {"workflow_id": "wf-1", "task_id": "task-1"},
    }
    request.update(overrides)
    return request


def _unused_endpoint() -> LoopbackEndpoint:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind((LOOPBACK_HOST, 0))
        port = listener.getsockname()[1]
    return LoopbackEndpoint(LOOPBACK_HOST, port)


def _client(identity: ExecutorIdentity, endpoint=None, token=TEST_AUTH_TOKEN):
    return WorkflowExecutorClient(
        endpoint or _unused_endpoint(),
        identity,
        auth_token=token,
    )


def test_community_defaults_to_process_pool_and_allows_inline_rollback():
    environment = {
        key: value
        for key, value in os.environ.items()
        if key not in {
            "DETERMINFLOW_WORKFLOW_EXECUTOR_MODE",
            "DETERMINFLOW_WORKFLOW_EXECUTOR_COUNT",
            "AI_COMPANY_WORKFLOW_EXECUTOR_MODE",
            "AI_COMPANY_WORKFLOW_EXECUTOR_COUNT",
        }
    }
    defaulted = subprocess.run(
        [sys.executable, "-c", (
            "from src.config import WORKFLOW_EXECUTOR_MODE, WORKFLOW_EXECUTOR_COUNT; "
            "print(WORKFLOW_EXECUTOR_MODE, WORKFLOW_EXECUTOR_COUNT)"
        )],
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert defaulted.returncode == 0, defaulted.stderr
    assert defaulted.stdout.strip() == "process 4"

    environment["DETERMINFLOW_WORKFLOW_EXECUTOR_MODE"] = "inline"
    environment["DETERMINFLOW_WORKFLOW_EXECUTOR_COUNT"] = "1"
    rolled_back = subprocess.run(
        [sys.executable, "-c", (
            "from src.config import WORKFLOW_EXECUTOR_MODE, WORKFLOW_EXECUTOR_COUNT; "
            "print(WORKFLOW_EXECUTOR_MODE, WORKFLOW_EXECUTOR_COUNT)"
        )],
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert rolled_back.returncode == 0, rolled_back.stderr
    assert rolled_back.stdout.strip() == "inline 1"


def test_executor_count_reads_prefixed_environment_and_rejects_invalid_value():
    environment = os.environ.copy()
    environment["DETERMINFLOW_WORKFLOW_EXECUTOR_COUNT"] = "3"
    valid = subprocess.run(
        [sys.executable, "-c", (
            "from src.config import WORKFLOW_EXECUTOR_COUNT; "
            "print(WORKFLOW_EXECUTOR_COUNT)"
        )],
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert valid.returncode == 0
    assert valid.stdout.strip() == "3"

    environment["DETERMINFLOW_WORKFLOW_EXECUTOR_COUNT"] = "invalid"
    invalid = subprocess.run(
        [sys.executable, "-c", "import src.config"],
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert invalid.returncode != 0
    assert "WORKFLOW_EXECUTOR_COUNT 必须是整数" in invalid.stderr


def test_protocol_rejects_stale_epoch_and_unknown_operation():
    identity = ExecutorIdentity("workflow-executor-0", "epoch-2")

    with pytest.raises(ExecutorProtocolError, match="epoch"):
        validate_request(
            _request(identity, executor_epoch="epoch-1"),
            expected_identity=identity,
            expected_auth_token=TEST_AUTH_TOKEN,
        )

    with pytest.raises(ExecutorProtocolError, match="operation"):
        validate_request(
            _request(identity, operation="arbitrary_python"),
            expected_identity=identity,
            expected_auth_token=TEST_AUTH_TOKEN,
        )


def test_protocol_rejects_missing_and_wrong_auth_token_before_epoch():
    identity = ExecutorIdentity("workflow-executor-0", "epoch-2")

    with pytest.raises(ExecutorProtocolError, match="auth_token"):
        validate_request(
            _request(identity, **{AUTH_TOKEN_FIELD: "wrong-token"}),
            expected_identity=identity,
            expected_auth_token=TEST_AUTH_TOKEN,
        )
    with pytest.raises(ExecutorProtocolError, match="auth_token"):
        payload = _request(identity)
        payload.pop(AUTH_TOKEN_FIELD)
        validate_request(
            payload,
            expected_identity=identity,
            expected_auth_token=TEST_AUTH_TOKEN,
        )
    with pytest.raises(ExecutorProtocolError, match="auth_token"):
        validate_request(
            _request(
                identity,
                executor_epoch="epoch-1",
                **{AUTH_TOKEN_FIELD: "wrong-token"},
            ),
            expected_identity=identity,
            expected_auth_token=TEST_AUTH_TOKEN,
        )


def test_loopback_endpoint_rejects_non_loopback_hosts():
    with pytest.raises(ExecutorProtocolError, match="loopback"):
        parse_loopback_endpoint({"host": "0.0.0.0", "port": 8080})
    with pytest.raises(ExecutorProtocolError, match="loopback"):
        parse_loopback_endpoint({"host": "8.8.8.8", "port": 53})
    with pytest.raises(ExecutorProtocolError, match="port"):
        parse_loopback_endpoint({"host": LOOPBACK_HOST, "port": 0})
    parsed = parse_loopback_endpoint({"host": LOOPBACK_HOST, "port": 4321})
    assert parsed == LoopbackEndpoint(LOOPBACK_HOST, 4321)


def test_client_round_trip_preserves_executor_identity():
    async def scenario():
        identity = ExecutorIdentity("workflow-executor-0", "epoch-1")
        received = []

        async def handle(reader, writer):
            request = json.loads(await reader.readline())
            received.append(request)
            writer.write(
                json.dumps(
                    response_frame(request["request_id"], result={"success": True})
                ).encode()
                + b"\n"
            )
            await writer.drain()
            writer.close()
            await writer.wait_closed()

        server = await start_loopback_server(handle)
        try:
            client = WorkflowExecutorClient(
                LoopbackEndpoint(LOOPBACK_HOST, server.sockets[0].getsockname()[1]),
                identity,
                auth_token=TEST_AUTH_TOKEN,
            )
            result = await client.call(
                "run_task", workflow_id="wf-1", task_id="task-1"
            )
        finally:
            server.close()
            await server.wait_closed()

        assert result == {"success": True}
        assert received[0]["executor_id"] == identity.executor_id
        assert received[0]["executor_epoch"] == identity.epoch
        assert received[0][AUTH_TOKEN_FIELD] == TEST_AUTH_TOKEN

    asyncio.run(scenario())


def test_client_fails_closed_when_executor_is_absent():
    async def scenario():
        client = _client(ExecutorIdentity("workflow-executor-0", "epoch-1"))
        with pytest.raises(ExecutorUnavailable, match="unavailable"):
            await client.call("ping")
        with pytest.raises(ExecutorUnavailable, match="unavailable"):
            client.call_sync("ping")

    asyncio.run(scenario())


def test_client_call_sync_round_trip_on_live_server():
    async def scenario():
        identity = ExecutorIdentity("workflow-executor-0", "epoch-1")

        class Manager:
            def ping(self):
                raise AssertionError("status/ping must not reach the manager")

        server = WorkflowExecutorServer(identity, Manager(), auth_token=TEST_AUTH_TOKEN)
        await server.start()
        try:
            client = WorkflowExecutorClient(
                server.endpoint, identity, auth_token=TEST_AUTH_TOKEN,
            )
            result = await asyncio.to_thread(client.call_sync, "ping")
        finally:
            await server.close()

        assert result["executor_id"] == identity.executor_id
        assert result["executor_epoch"] == identity.epoch
        assert result["pid"] == os.getpid()

    asyncio.run(scenario())


def test_supervisor_retries_spawn_and_generation_handoff_failures(tmp_path):
    class ExitedProcess:
        returncode = -9
        pid = 99999999

        async def wait(self):
            return self.returncode

    class LiveProcess:
        returncode = None
        pid = 99999998

        async def wait(self):
            await asyncio.Event().wait()

    async def scenario():
        handoffs = []
        supervisor = WorkflowExecutorSupervisor(restart_backoff_max=0.001)
        supervisor.client = _client(
            ExecutorIdentity("workflow-executor-0", "epoch-old"),
        )
        supervisor._process = ExitedProcess()
        spawn_attempts = 0

        async def spawn():
            nonlocal spawn_attempts
            spawn_attempts += 1
            if spawn_attempts == 1:
                raise RuntimeError("transient spawn failure")
            supervisor._process = LiveProcess()
            supervisor.client.update_identity(
                ExecutorIdentity("workflow-executor-0", "epoch-new")
            )

        async def handoff(previous, current):
            handoffs.append((previous.epoch, current.epoch))
            if len(handoffs) == 1:
                raise RuntimeError("transient handoff failure")
            supervisor._closing = True

        supervisor._spawn = spawn
        supervisor.on_restart = handoff
        await supervisor._monitor()

        assert spawn_attempts == 2
        assert handoffs == [
            ("epoch-old", "epoch-new"),
            ("epoch-old", "epoch-new"),
        ]

    asyncio.run(scenario())


def _spawn_descendant_group(pid_file: Path) -> tuple[ExecutorProcessTree, asyncio.subprocess.Process]:
    tree = ExecutorProcessTree()
    script = (
        "import os, subprocess, sys, time\n"
        "from pathlib import Path\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(180)'])\n"
        f"Path({str(pid_file)!r}).write_text("
        "f'{os.getpid()} {child.pid}', encoding='utf-8')\n"
        "try:\n"
        "    time.sleep(180)\n"
        "finally:\n"
        "    if child.poll() is None:\n"
        "        child.kill()\n"
    )
    return tree, script


async def _start_descendant_group(tmp_path: Path, name: str):
    pid_file = tmp_path / f"{name}.pids"
    tree, script = _spawn_descendant_group(pid_file)
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        script,
        **tree.spawn_options(),
    )
    tree.attach(process.pid)
    deadline = asyncio.get_running_loop().time() + 5.0
    while asyncio.get_running_loop().time() < deadline:
        if pid_file.is_file():
            text = pid_file.read_text(encoding="utf-8").strip()
            if text:
                leader_text, child_text = text.split()
                return tree, process, int(leader_text), int(child_text)
        if process.returncode is not None:
            break
        await asyncio.sleep(0.02)
    tree.reap()
    raise AssertionError(f"{name} descendant group did not publish pids")


def test_supervisor_reaps_executor_descendants_without_touching_siblings(tmp_path):
    async def scenario():
        victim_tree, victim, victim_leader, victim_child = await _start_descendant_group(
            tmp_path, "victim",
        )
        sibling_tree, sibling, sibling_leader, sibling_child = await _start_descendant_group(
            tmp_path, "sibling",
        )
        try:
            assert process_is_alive(victim_child)
            assert process_is_alive(sibling_child)
            force_kill_pid(victim_leader)
            await victim.wait()
            victim_tree.reap()
            deadline = asyncio.get_running_loop().time() + 5.0
            while asyncio.get_running_loop().time() < deadline:
                if not process_is_alive(victim_child):
                    break
                await asyncio.sleep(0.02)
            else:
                pytest.fail("Executor descendant survived process-tree reap")
            assert process_is_alive(sibling_leader)
            assert process_is_alive(sibling_child)
            assert process_is_alive(os.getpid())
        finally:
            sibling_tree.reap()
            if sibling.returncode is None:
                force_kill_pid(sibling.pid)
                await sibling.wait()
            if victim.returncode is None:
                force_kill_pid(victim.pid)
                await victim.wait()

    asyncio.run(scenario())


def test_windows_process_tree_fails_closed_when_job_assignment_fails(monkeypatch):
    closed = False

    class FakeJob:
        def assign(self, _pid):
            return False

        def close(self):
            nonlocal closed
            closed = True

    monkeypatch.setattr(executor_process_module.os, "name", "nt")
    monkeypatch.setattr(
        executor_process_module._WindowsJobObject,
        "create",
        classmethod(lambda _cls: FakeJob()),
    )
    tree = ExecutorProcessTree()

    with pytest.raises(RuntimeError, match="assignment failed"):
        tree.attach(12345)

    assert closed is True


def test_windows_process_tree_closes_job_when_thread_resume_fails(monkeypatch):
    terminated = False
    closed = False

    class FakeJob:
        def assign(self, _pid):
            return True

        def terminate(self):
            nonlocal terminated
            terminated = True

        def close(self):
            nonlocal closed
            closed = True

    monkeypatch.setattr(executor_process_module.os, "name", "nt")
    monkeypatch.setattr(
        executor_process_module._WindowsJobObject,
        "create",
        classmethod(lambda _cls: FakeJob()),
    )

    def fail_resume(_pid):
        raise RuntimeError("resume failed")

    monkeypatch.setattr(executor_process_module, "_resume_process_threads", fail_resume)
    tree = ExecutorProcessTree()

    with pytest.raises(RuntimeError, match="resume failed"):
        tree.attach(12345)

    assert terminated is True
    assert closed is True


def test_real_executor_process_restarts_with_new_pid_and_epoch(
    tmp_path, monkeypatch,
):
    data_dir = tmp_path / "data"
    config_dir = tmp_path / "config"
    logs_dir = tmp_path / "logs"
    for directory in (data_dir, logs_dir):
        directory.mkdir()
    shutil.copytree(Path(__file__).parents[1] / "config", config_dir)
    monkeypatch.setenv("DETERMINFLOW_DATA_DIR", str(data_dir))
    monkeypatch.setenv("DETERMINFLOW_CONFIG_DIR", str(config_dir))
    monkeypatch.setenv("DETERMINFLOW_LOGS_DIR", str(logs_dir))
    monkeypatch.setenv("DETERMINFLOW_WORKFLOW_EXECUTOR_MODE", "inline")
    monkeypatch.setattr(executor_supervisor_module, "DATA_DIR", data_dir)

    async def scenario():
        async def discard_event(_channel, _event):
            return None

        supervisor = WorkflowExecutorSupervisor(
            startup_timeout=30.0,
            shutdown_timeout=10.0,
            restart_backoff_max=0.1,
            event_handler=discard_event,
        )
        await supervisor.start()
        try:
            old_process = supervisor._process
            old_identity = supervisor.identity
            assert old_process is not None
            old_pid = old_process.pid
            force_kill_pid(old_pid)

            deadline = asyncio.get_running_loop().time() + 30.0
            while asyncio.get_running_loop().time() < deadline:
                process = supervisor._process
                if (
                    process is not None
                    and process.pid != old_pid
                    and supervisor.identity.epoch != old_identity.epoch
                ):
                    try:
                        pong = await supervisor.client.call("ping")
                    except ExecutorUnavailable:
                        await asyncio.sleep(0.05)
                        continue
                    assert pong["pid"] == process.pid
                    assert pong["executor_epoch"] == supervisor.identity.epoch
                    break
                await asyncio.sleep(0.05)
            else:
                pytest.fail("Workflow Executor was not restarted after SIGKILL")
        finally:
            await supervisor.stop()

        assert old_process.returncode == forced_exit_code()
        assert supervisor._process is None

    asyncio.run(scenario())


class _Sessions:
    def __init__(self):
        self.sessions = {}


class _Delegate:
    def __init__(self, identity):
        self.identity = identity
        self.calls = []

    async def call(self, operation, **arguments):
        self.calls.append((operation, arguments))
        return {"success": True, "operation": operation}


class _PoolDelegate:
    def __init__(self, *delegates):
        self.delegates = {
            delegate.identity.executor_id: delegate for delegate in delegates
        }
        self._index = 0

    @property
    def identities(self):
        return tuple(delegate.identity for delegate in self.delegates.values())

    def client_for(self, executor_id):
        return self.delegates[executor_id]

    def select_client(self, _task_key):
        ordered = list(self.delegates.values())
        selected = ordered[self._index % len(ordered)]
        self._index += 1
        return selected

    def call_sync(self, operation, **arguments):
        self.calls.append((operation, arguments))
        return {"success": True, "operation": operation}


def _manager(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    workflows_dir = data_dir / "workflows"
    monkeypatch.setattr(workflow_manager_module, "DATA_DIR", data_dir)
    monkeypatch.setattr(workflow_manager_module, "WORKFLOWS_DIR", workflows_dir)
    manager = WorkflowManager(_Sessions())
    manager.create_workflow({
        "workflow_id": "wf-1",
        "name": "Executor test",
        "nodes": [],
        "edges": [],
    })
    return manager


def test_controller_assigns_once_and_routes_run_and_stop_to_same_executor(
    tmp_path, monkeypatch,
):
    manager = _manager(tmp_path, monkeypatch)
    delegate = _Delegate(ExecutorIdentity("workflow-executor-0", "epoch-1"))
    manager.attach_execution_delegate(delegate)
    created = manager.create_task("wf-1")

    started = asyncio.run(manager.run_task("wf-1", created["task_id"]))
    stopped = asyncio.run(manager.stop_task("wf-1", created["task_id"]))
    persisted = manager._load_task("wf-1", created["task_id"])

    assert started["success"] is True
    assert stopped["success"] is True
    assert persisted.executor_id == "workflow-executor-0"
    assert persisted.executor_epoch == "epoch-1"
    assert [call[0] for call in delegate.calls] == ["run_task", "stop_task"]


def test_pool_balances_new_tasks_and_keeps_each_task_sticky(tmp_path, monkeypatch):
    manager = _manager(tmp_path, monkeypatch)
    first = _Delegate(ExecutorIdentity("workflow-executor-0", "epoch-0"))
    second = _Delegate(ExecutorIdentity("workflow-executor-1", "epoch-1"))
    pool = _PoolDelegate(first, second)
    manager.attach_execution_delegate(pool)
    task_ids = [manager.create_task("wf-1")["task_id"] for _ in range(4)]

    for task_id in task_ids:
        assert asyncio.run(manager.run_task("wf-1", task_id))["success"]
        assert asyncio.run(manager.stop_task("wf-1", task_id))["success"]

    persisted = [manager._load_task("wf-1", task_id) for task_id in task_ids]
    assert [task.executor_id for task in persisted] == [
        "workflow-executor-0",
        "workflow-executor-1",
        "workflow-executor-0",
        "workflow-executor-1",
    ]
    assert [call[0] for call in first.calls] == [
        "run_task", "stop_task", "run_task", "stop_task",
    ]
    assert [call[0] for call in second.calls] == [
        "run_task", "stop_task", "run_task", "stop_task",
    ]


def test_pool_does_not_move_task_when_assigned_member_is_unavailable(
    tmp_path, monkeypatch,
):
    manager = _manager(tmp_path, monkeypatch)
    only = _Delegate(ExecutorIdentity("workflow-executor-0", "epoch-0"))
    manager.attach_execution_delegate(_PoolDelegate(only))
    task = WorkflowTask(
        task_id="task-retired",
        workflow_id="wf-1",
        status="running",
        executor_id="workflow-executor-9",
        executor_epoch="old",
    )
    manager._save_task(task)

    result = asyncio.run(manager.stop_task("wf-1", task.task_id))

    assert result["error"] == "executor_unavailable"
    persisted = manager._load_task("wf-1", task.task_id)
    assert persisted.executor_id == "workflow-executor-9"
    assert only.calls == []


def test_pool_routes_retry_skip_and_approval_to_persisted_owner(
    tmp_path, monkeypatch,
):
    manager = _manager(tmp_path, monkeypatch)
    first = _Delegate(ExecutorIdentity("workflow-executor-0", "epoch-0"))
    second = _Delegate(ExecutorIdentity("workflow-executor-1", "epoch-1"))
    manager.attach_execution_delegate(_PoolDelegate(first, second))
    task = WorkflowTask(
        task_id="task-controls",
        workflow_id="wf-1",
        status="failed",
        executor_id="workflow-executor-1",
        executor_epoch="epoch-1",
    )
    manager._save_task(task)

    retried = asyncio.run(manager.retry_node(
        "wf-1", task.task_id, "node-1", 1,
    ))
    skipped = asyncio.run(manager.skip_node(
        "wf-1", task.task_id, "node-1", 1,
    ))
    approved = asyncio.run(manager.approve_node_async(
        "wf-1", task.task_id, "node-1", True,
    ))

    assert retried["success"] is True
    assert skipped["success"] is True
    assert approved["success"] is True
    assert first.calls == []
    assert [call[0] for call in second.calls] == [
        "retry_node", "skip_node", "approve_node",
    ]
    persisted = manager._load_task("wf-1", task.task_id)
    assert (persisted.executor_id, persisted.executor_epoch) == (
        "workflow-executor-1", "epoch-1",
    )


def test_legacy_stop_workflow_includes_remote_running_task(tmp_path, monkeypatch):
    manager = _manager(tmp_path, monkeypatch)
    delegate = _Delegate(ExecutorIdentity("workflow-executor-0", "epoch-1"))
    manager.attach_execution_delegate(delegate)
    task = WorkflowTask(
        task_id="task-running",
        workflow_id="wf-1",
        status="running",
        executor_id="workflow-executor-0",
        executor_epoch="epoch-1",
    )
    manager._save_task(task)

    result = asyncio.run(manager.stop_workflow("wf-1"))

    assert result["success"] is True
    assert [call[0] for call in delegate.calls] == ["stop_task"]


def test_main_session_cascade_stops_remote_task_before_deleting_file(
    tmp_path, monkeypatch,
):
    manager = _manager(tmp_path, monkeypatch)
    delegate = _Delegate(ExecutorIdentity("workflow-executor-0", "epoch-1"))
    manager.attach_execution_delegate(delegate)
    task = WorkflowTask(
        task_id="task-running",
        workflow_id="wf-1",
        status="running",
        executor_id="workflow-executor-0",
        executor_epoch="epoch-1",
    )
    manager._save_task(task)

    result = asyncio.run(
        manager._delete_task_for_main_session("wf-1", task.task_id)
    )

    assert result["success"] is True
    assert [call[0] for call in delegate.calls] == ["stop_task"]
    assert manager._load_task("wf-1", task.task_id) is None


def test_workflow_list_counts_remote_active_tasks_from_persisted_state(
    tmp_path, monkeypatch,
):
    manager = _manager(tmp_path, monkeypatch)
    manager._save_task(WorkflowTask(
        task_id="task-remote",
        workflow_id="wf-1",
        status="running",
        executor_id="workflow-executor-0",
        executor_epoch="epoch-1",
    ))

    workflow = manager.list_workflows()[0]

    assert workflow["status"] == "running"
    assert workflow["running_tasks"] == 1


def test_delete_workflow_stops_remote_tasks_before_removing_definition(
    tmp_path, monkeypatch,
):
    manager = _manager(tmp_path, monkeypatch)
    delegate = _Delegate(ExecutorIdentity("workflow-executor-0", "epoch-1"))
    manager.attach_execution_delegate(delegate)
    manager._save_task(WorkflowTask(
        task_id="task-remote",
        workflow_id="wf-1",
        status="running",
        executor_id="workflow-executor-0",
        executor_epoch="epoch-1",
    ))

    deleted = asyncio.run(manager.delete_workflow("wf-1"))

    assert deleted is True
    assert [call[0] for call in delegate.calls] == ["stop_task"]
    assert manager.get_workflow("wf-1") is None


def test_shutdown_cancels_and_reaps_local_task_runners(tmp_path, monkeypatch):
    manager = _manager(tmp_path, monkeypatch)

    async def scenario():
        cancelled = asyncio.Event()

        async def runner():
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        task = asyncio.create_task(runner())
        await asyncio.sleep(0)
        manager._running_tasks["task-running"] = task
        await manager.shutdown_running_tasks()

        assert cancelled.is_set()
        assert task.done()
        assert manager._running_tasks == {}

    asyncio.run(scenario())


def test_retry_and_skip_are_executed_by_owner_to_keep_retry_timer_sticky(
    tmp_path, monkeypatch,
):
    manager = _manager(tmp_path, monkeypatch)
    delegate = _Delegate(ExecutorIdentity("workflow-executor-0", "epoch-1"))
    manager.attach_execution_delegate(delegate)
    task = WorkflowTask(
        task_id="task-failed",
        workflow_id="wf-1",
        status="failed",
        executor_id="workflow-executor-0",
        executor_epoch="epoch-1",
    )
    manager._save_task(task)

    retried = asyncio.run(manager.retry_node("wf-1", task.task_id, "node-1", 2))
    skipped = asyncio.run(manager.skip_node("wf-1", task.task_id, "node-1", 2))

    assert retried["success"] is True
    assert skipped["success"] is True
    assert [call[0] for call in delegate.calls] == ["retry_node", "skip_node"]


def test_async_approval_routes_without_blocking_controller_loop(
    tmp_path, monkeypatch,
):
    manager = _manager(tmp_path, monkeypatch)
    delegate = _Delegate(ExecutorIdentity("workflow-executor-0", "epoch-1"))
    manager.attach_execution_delegate(delegate)
    task = WorkflowTask(
        task_id="task-approval",
        workflow_id="wf-1",
        status="running",
        executor_id="workflow-executor-0",
        executor_epoch="epoch-1",
    )
    manager._save_task(task)

    result = asyncio.run(manager.approve_node_async(
        "wf-1", task.task_id, "node-1", True,
    ))

    assert result["success"] is True
    assert [call[0] for call in delegate.calls] == ["approve_node"]


def test_controller_rejects_commands_for_stale_executor_epoch(tmp_path, monkeypatch):
    manager = _manager(tmp_path, monkeypatch)
    delegate = _Delegate(ExecutorIdentity("workflow-executor-0", "epoch-1"))
    manager.attach_execution_delegate(delegate)
    created = manager.create_task("wf-1")
    assert asyncio.run(manager.run_task("wf-1", created["task_id"]))["success"]
    delegate.identity = ExecutorIdentity("workflow-executor-0", "epoch-2")

    result = asyncio.run(manager.stop_task("wf-1", created["task_id"]))

    assert result["error"] == "executor_epoch_stale"
    assert [call[0] for call in delegate.calls] == ["run_task"]


def test_executor_manager_rejects_task_owned_by_another_epoch(tmp_path, monkeypatch):
    manager = _manager(tmp_path, monkeypatch)
    manager.set_local_executor_identity(
        ExecutorIdentity("workflow-executor-0", "epoch-2")
    )
    task = WorkflowTask(
        task_id="task-old",
        workflow_id="wf-1",
        status="pending",
        snapshot_definition={
            "workflow_id": "wf-1",
            "name": "Executor test",
            "nodes": [],
            "edges": [],
        },
        executor_id="workflow-executor-0",
        executor_epoch="epoch-1",
    )
    manager._save_task(task)

    result = asyncio.run(manager.run_task("wf-1", task.task_id))

    assert result["error"] == "executor_epoch_stale"
    assert manager._running_tasks == {}


def test_executor_server_dispatches_allowlisted_operation_and_rejects_old_epoch():
    class Manager:
        def __init__(self):
            self.calls = []

        async def stop_task(self, workflow_id, task_id):
            self.calls.append((workflow_id, task_id))
            return {"success": True}

    async def scenario():
        identity = ExecutorIdentity("workflow-executor-0", "epoch-2")
        manager = Manager()
        server = WorkflowExecutorServer(identity, manager, auth_token=TEST_AUTH_TOKEN)
        await server.start()
        try:
            assert server.endpoint.host == LOOPBACK_HOST
            current = WorkflowExecutorClient(
                server.endpoint, identity, auth_token=TEST_AUTH_TOKEN,
            )
            result = await current.call(
                "stop_task", workflow_id="wf-1", task_id="task-1"
            )
            stale = WorkflowExecutorClient(
                server.endpoint,
                ExecutorIdentity("workflow-executor-0", "epoch-1"),
                auth_token=TEST_AUTH_TOKEN,
            )
            with pytest.raises(ExecutorUnavailable, match="stale"):
                await stale.call("stop_task", workflow_id="wf-1", task_id="task-1")
            wrong_token = WorkflowExecutorClient(
                server.endpoint, identity, auth_token="wrong-token",
            )
            with pytest.raises(ExecutorUnavailable, match="auth_token"):
                await wrong_token.call("stop_task", workflow_id="wf-1", task_id="task-1")
        finally:
            await server.close()
        assert result == {"success": True}
        assert manager.calls == [("wf-1", "task-1")]

    asyncio.run(scenario())


def test_executor_server_runs_recovery_inside_owning_executor():
    class Manager:
        def __init__(self):
            self.identity = None

        async def recover_workflow_tasks(self, *, executor_identity=None):
            self.identity = executor_identity
            return {"scanned": 2, "resumed": 1}

    async def scenario():
        identity = ExecutorIdentity("workflow-executor-1", "epoch-2")
        manager = Manager()
        server = WorkflowExecutorServer(identity, manager, auth_token=TEST_AUTH_TOKEN)
        await server.start()
        try:
            client = WorkflowExecutorClient(
                server.endpoint, identity, auth_token=TEST_AUTH_TOKEN,
            )
            result = await client.call("recover_owned_tasks")
        finally:
            await server.close()

        assert result == {"scanned": 2, "resumed": 1}
        assert manager.identity == identity

    asyncio.run(scenario())


def test_executor_transport_binds_loopback_and_restricts_endpoint_file(tmp_path):
    async def scenario():
        endpoint_path = tmp_path / "rpc.endpoint"
        server = WorkflowExecutorServer(
            ExecutorIdentity("workflow-executor-0", "epoch-1"),
            object(),
            auth_token=TEST_AUTH_TOKEN,
            endpoint_path=endpoint_path,
        )
        await server.start()
        try:
            assert server.endpoint.host == LOOPBACK_HOST
            bound_host, bound_port = server._server.sockets[0].getsockname()[:2]
            assert bound_host == LOOPBACK_HOST
            assert bound_port == server.endpoint.port
            published = json.loads(endpoint_path.read_text(encoding="utf-8"))
            assert published == {
                "host": LOOPBACK_HOST,
                "port": server.endpoint.port,
            }
            assert AUTH_TOKEN_FIELD not in published
            if os.name != "nt":
                assert endpoint_path.stat().st_mode & 0o777 == 0o600
        finally:
            await server.close()
        assert not endpoint_path.exists()

    asyncio.run(scenario())


def test_executor_process_lease_proves_single_live_owner(tmp_path):
    lease_path = tmp_path / "system" / "executor.lock"
    first = ExecutorProcessLease(lease_path)
    second = ExecutorProcessLease(lease_path)
    first.acquire(0.1)
    try:
        with pytest.raises(ExecutorLeaseUnavailable, match="another"):
            second.acquire(0.1)
    finally:
        first.release()

    second.acquire(0.1)
    second.release()
    assert lease_path.stat().st_mode & 0o777 == 0o600


def test_cold_start_reassigns_only_recoverable_tasks_from_same_executor(
    tmp_path, monkeypatch,
):
    manager = _manager(tmp_path, monkeypatch)
    tasks = [
        WorkflowTask(
            task_id="task-running",
            workflow_id="wf-1",
            status="running",
            executor_id="workflow-executor-0",
            executor_epoch="old",
        ),
        WorkflowTask(
            task_id="task-completed",
            workflow_id="wf-1",
            status="completed",
            executor_id="workflow-executor-0",
            executor_epoch="old",
        ),
        WorkflowTask(
            task_id="task-pending",
            workflow_id="wf-1",
            status="pending",
            executor_id="workflow-executor-0",
            executor_epoch="old",
        ),
        WorkflowTask(
            task_id="task-failed",
            workflow_id="wf-1",
            status="failed",
            executor_id="workflow-executor-0",
            executor_epoch="older-than-previous",
        ),
        WorkflowTask(
            task_id="task-other",
            workflow_id="wf-1",
            status="running",
            executor_id="workflow-executor-9",
            executor_epoch="old",
        ),
    ]
    for task in tasks:
        manager._save_task(task)

    count = manager.reassign_stale_executor_generations(
        ExecutorIdentity("workflow-executor-0", "new")
    )

    assert count == 3
    assert manager._load_task("wf-1", "task-running").executor_epoch == "new"
    assert manager._load_task("wf-1", "task-pending").executor_epoch == "new"
    assert manager._load_task("wf-1", "task-failed").executor_epoch == "new"
    assert manager._load_task("wf-1", "task-completed").executor_epoch == "old"
    assert manager._load_task("wf-1", "task-other").executor_id == "workflow-executor-9"


def test_cold_start_reconciles_stale_and_retired_pool_assignments(
    tmp_path, monkeypatch,
):
    manager = _manager(tmp_path, monkeypatch)
    first = _Delegate(ExecutorIdentity("workflow-executor-0", "new-0"))
    second = _Delegate(ExecutorIdentity("workflow-executor-1", "new-1"))
    pool = _PoolDelegate(first, second)
    tasks = [
        WorkflowTask(
            task_id="task-stale",
            workflow_id="wf-1",
            status="running",
            executor_id="workflow-executor-0",
            executor_epoch="old-0",
        ),
        WorkflowTask(
            task_id="task-retired",
            workflow_id="wf-1",
            status="retry_waiting",
            executor_id="workflow-executor-4",
            executor_epoch="old-4",
        ),
        WorkflowTask(
            task_id="task-new",
            workflow_id="wf-1",
            status="pending",
        ),
        WorkflowTask(
            task_id="task-completed",
            workflow_id="wf-1",
            status="completed",
            executor_id="workflow-executor-4",
            executor_epoch="old-4",
        ),
    ]
    for task in tasks:
        manager._save_task(task)

    count = manager.reconcile_executor_pool(pool)

    assert count == 3
    stale = manager._load_task("wf-1", "task-stale")
    retired = manager._load_task("wf-1", "task-retired")
    new = manager._load_task("wf-1", "task-new")
    completed = manager._load_task("wf-1", "task-completed")
    assert (stale.executor_id, stale.executor_epoch) == (
        "workflow-executor-0", "new-0",
    )
    assert (retired.executor_id, retired.executor_epoch) == (
        "workflow-executor-0", "new-0",
    )
    assert (new.executor_id, new.executor_epoch) == (
        "workflow-executor-1", "new-1",
    )
    assert (completed.executor_id, completed.executor_epoch) == (
        "workflow-executor-4", "old-4",
    )


def test_cold_start_keeps_main_takeover_recovery_on_controller(
    tmp_path, monkeypatch,
):
    manager = _manager(tmp_path, monkeypatch)
    first = _Delegate(ExecutorIdentity("workflow-executor-0", "new-0"))
    pool = _PoolDelegate(first)
    task = WorkflowTask(
        task_id="task-main-takeover",
        workflow_id="wf-1",
        status="running",
        main_takeover=True,
    )
    manager._save_task(task)

    count = manager.reconcile_executor_pool(pool)

    assert count == 1
    persisted = manager._load_task("wf-1", task.task_id)
    assert (persisted.executor_id, persisted.executor_epoch) == (
        "controller", "inline",
    )
    assert first.calls == []


def test_inline_lease_group_blocks_every_recorded_pool_member(tmp_path):
    state_path = tmp_path / "system" / "workflow-executor-pool.json"
    state_path.parent.mkdir(parents=True)
    state_path.write_text(json.dumps({
        "version": 1,
        "executor_ids": [
            "workflow-executor-0",
            "workflow-executor-1",
            "workflow-executor-2",
        ],
    }), encoding="utf-8")
    group = ExecutorLeaseGroup.for_inline(tmp_path, configured_count=1)
    group.acquire(0.2)
    try:
        for executor_id in load_recorded_executor_ids(tmp_path):
            competing = ExecutorProcessLease(
                executor_lease_path(tmp_path, executor_id)
            )
            with pytest.raises(ExecutorLeaseUnavailable):
                competing.acquire(0.01)
    finally:
        group.release()


def test_real_executor_pool_starts_two_members_and_restarts_only_failed_member(
    tmp_path, monkeypatch,
):
    data_dir = tmp_path / "data"
    config_dir = tmp_path / "config"
    logs_dir = tmp_path / "logs"
    for directory in (data_dir, logs_dir):
        directory.mkdir()
    shutil.copytree(Path(__file__).parents[1] / "config", config_dir)
    monkeypatch.setenv("DETERMINFLOW_DATA_DIR", str(data_dir))
    monkeypatch.setenv("DETERMINFLOW_CONFIG_DIR", str(config_dir))
    monkeypatch.setenv("DETERMINFLOW_LOGS_DIR", str(logs_dir))
    monkeypatch.setenv("DETERMINFLOW_WORKFLOW_EXECUTOR_MODE", "inline")

    async def scenario():
        handoffs = []

        async def on_restart(previous, current):
            handoffs.append((previous, current))

        async def discard_event(_channel, _event):
            return None

        pool = WorkflowExecutorPool(
            2,
            data_dir=data_dir,
            startup_timeout=30.0,
            shutdown_timeout=10.0,
            restart_backoff_max=0.1,
            on_restart=on_restart,
            event_handler=discard_event,
        )
        await pool.start()
        try:
            original_pids = dict(pool.member_pids)
            original_identities = {
                identity.executor_id: identity for identity in pool.identities
            }
            assert len(set(original_pids.values())) == 2
            victim = pool._supervisors["workflow-executor-0"]
            assert victim.pid is not None
            force_kill_pid(victim.pid)

            deadline = asyncio.get_running_loop().time() + 30.0
            while asyncio.get_running_loop().time() < deadline:
                current_pids = pool.member_pids
                current = {identity.executor_id: identity for identity in pool.identities}
                if (
                    current_pids["workflow-executor-0"] != original_pids["workflow-executor-0"]
                    and current["workflow-executor-0"].epoch
                    != original_identities["workflow-executor-0"].epoch
                    and handoffs
                ):
                    break
                await asyncio.sleep(0.05)
            else:
                pytest.fail("failed pool member was not restarted")

            assert pool.member_pids["workflow-executor-1"] == original_pids[
                "workflow-executor-1"
            ]
            assert (
                pool.client_for("workflow-executor-1").identity.epoch
                == original_identities["workflow-executor-1"].epoch
            )
            assert handoffs[0][0].executor_id == "workflow-executor-0"
            assert handoffs[0][1].executor_id == "workflow-executor-0"
            for identity in pool.identities:
                pong = await pool.client_for(identity.executor_id).call("ping")
                assert pong["pid"] == pool.member_pids[identity.executor_id]
        finally:
            await pool.stop()

        assert all(pid is not None for pid in original_pids.values())
        assert pool.member_pids == {}

    asyncio.run(scenario())


def test_event_bridge_forwards_current_epoch_and_drops_stale_epoch():
    async def scenario():
        received = []
        current_identity = ExecutorIdentity("workflow-executor-0", "epoch-2")

        async def handler(channel, event):
            received.append((channel, event))

        receiver = ControllerEventReceiver(
            current_identity, handler, auth_token=TEST_AUTH_TOKEN,
        )
        endpoint = await receiver.start()
        current = ExecutorEventForwarder(
            endpoint, current_identity, auth_token=TEST_AUTH_TOKEN,
        )
        stale = ExecutorEventForwarder(
            endpoint,
            ExecutorIdentity("workflow-executor-0", "epoch-1"),
            auth_token=TEST_AUTH_TOKEN,
        )
        wrong_token = ExecutorEventForwarder(
            endpoint, current_identity, auth_token="wrong-token",
        )
        try:
            await current.emit("events", {"type": "wf_task_update", "task_id": "t1"})
            await stale.emit("events", {"type": "wf_task_update", "task_id": "old"})
            await wrong_token.emit("events", {"type": "wf_task_update", "task_id": "bad"})
            await asyncio.sleep(0.05)
        finally:
            await current.close()
            await stale.close()
            await wrong_token.close()
            await receiver.close()

        assert received == [
            ("events", {"type": "wf_task_update", "task_id": "t1"})
        ]
        assert receiver.stats()["malformed_or_stale"] >= 2

    asyncio.run(scenario())


def test_event_bridge_accepts_frame_larger_than_default_stream_limit():
    async def scenario():
        received = []
        identity = ExecutorIdentity("workflow-executor-0", "epoch-1")

        async def handler(_channel, event):
            received.append(event)

        receiver = ControllerEventReceiver(
            identity, handler, auth_token=TEST_AUTH_TOKEN,
        )
        endpoint = await receiver.start()
        forwarder = ExecutorEventForwarder(
            endpoint, identity, auth_token=TEST_AUTH_TOKEN,
        )
        event = {"type": "wf_task_update", "stdout": "x" * (128 * 1024)}
        try:
            await forwarder.emit("events", event)
            await asyncio.sleep(0.05)
        finally:
            await forwarder.close()
            await receiver.close()

        assert received == [event]

    asyncio.run(scenario())
