"""Helpers for real WorkflowExecutorPool(2) Script Task scenario tests."""

from __future__ import annotations

import asyncio
import inspect
import json
import os
import signal
import shutil
import subprocess
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

import pytest

import src.config as config_module
import src.workflow.executor_supervisor as executor_supervisor_module
import src.workflow.manager as workflow_manager_module
import src.workflow.task_recovery as task_recovery_module
from src.workflow.executor_pool import WorkflowExecutorPool
from src.workflow.executor_process import force_kill_pid, process_is_alive
from src.workflow.manager import WorkflowManager


REPO_ROOT = Path(__file__).resolve().parents[1]
POOL_SIZE = 2
POOL_STARTUP_TIMEOUT = 30.0
POOL_SHUTDOWN_TIMEOUT = 10.0
WAIT_INTERVAL = 0.05
TASK_WAIT_TIMEOUT = 20.0
RESTART_WAIT_TIMEOUT = 30.0
PROCESS_WAIT_TIMEOUT = 10.0

QUICK_SCRIPT = """\
import json
from pathlib import Path
import os
import time

root = Path(os.environ["DETERMINFLOW_MARKER_DIR"])
root.mkdir(parents=True, exist_ok=True)
started_at = time.time()
cpu_started_at = time.process_time()
value = 0
while time.process_time() - cpu_started_at < 0.15:
    value = (value * 33 + 17) % 1000003
(root / os.environ["TASK_ID"]).write_text(json.dumps({
    "pid": os.getpid(),
    "ppid": os.getppid(),
    "started_at": started_at,
    "completed_at": time.time(),
    "value": value,
}), encoding="utf-8")
print("<script_out>ok</script_out>")
"""

HOLD_SCRIPT = """\
import os
import subprocess
import sys
import time
from pathlib import Path

task_id = os.environ["TASK_ID"]
root = Path(os.environ["DETERMINFLOW_HOLD_DIR"]) / task_id
root.mkdir(parents=True, exist_ok=True)
(root / "script.pid").write_text(str(os.getpid()), encoding="utf-8")
child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(180)"])
(root / "child.pid").write_text(str(child.pid), encoding="utf-8")
(root / "ready").write_text("1", encoding="utf-8")
try:
    release = root / "release"
    while not release.exists():
        time.sleep(0.05)
finally:
    if child.poll() is None:
        child.terminate()
        try:
            child.wait(timeout=1)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait(timeout=1)
print("<script_out>released</script_out>")
"""


@dataclass
class IsolatedExecutorRuntime:
    data_dir: Path
    config_dir: Path
    logs_dir: Path
    workflows_dir: Path
    hold_dir: Path
    marker_dir: Path


@dataclass
class ProcessTracker:
    pids: set[int] = field(default_factory=set)
    group_ids: set[int] = field(default_factory=set)

    def add_pid(self, pid: int | None) -> None:
        if pid:
            self.pids.add(int(pid))

    def add_group(self, pid: int | None) -> None:
        if not pid:
            return
        self.add_pid(pid)
        self.group_ids.add(int(pid))

    def add_pool(self, pool: WorkflowExecutorPool) -> None:
        for pid in pool.member_pids.values():
            self.add_group(pid)

    def live(self) -> list[int]:
        live = {pid for pid in self.pids if process_alive(pid)}
        if os.name != "nt":
            for group_id in self.group_ids:
                live.update(pids_with_pgid(group_id))
        return sorted(live)


def process_alive(pid: int) -> bool:
    return process_is_alive(pid)


def pids_with_pgid(pgid: int) -> set[int]:
    if pgid <= 0:
        return set()
    try:
        listing = subprocess.run(
            ["ps", "-axo", "pid=,pgid="],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return set()
    found: set[int] = set()
    for line in listing.splitlines():
        try:
            pid_text, group_text = line.split()
            if int(group_text) == pgid:
                found.add(int(pid_text))
        except (ValueError, TypeError):
            continue
    return found


def read_int_file(path: Path) -> int | None:
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return None
    return int(text)


def task_binding(task) -> dict:
    return {
        "executor_id": task.executor_id,
        "executor_epoch": task.executor_epoch,
        "status": task.status,
        "node_statuses": {
            node_id: state.status
            for node_id, state in task.node_states.items()
        },
    }


class SimplePatcher:
    """Restore env and module attributes without depending on pytest."""

    def __init__(self):
        self._env: list[tuple[str, str | None]] = []
        self._attrs: list[tuple[object, str, object]] = []

    def setenv(self, name: str, value: str) -> None:
        self._env.append((name, os.environ.get(name)))
        os.environ[name] = value

    def setattr(self, target: object, name: str, value) -> None:
        self._attrs.append((target, name, getattr(target, name)))
        setattr(target, name, value)

    def undo(self) -> None:
        for target, name, value in reversed(self._attrs):
            setattr(target, name, value)
        for name, value in reversed(self._env):
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


@contextmanager
def executor_runtime_isolation(tmp_path: Path):
    patcher = SimplePatcher()
    runtime = isolate_executor_runtime(tmp_path, patcher)
    try:
        yield runtime, patcher
    finally:
        patcher.undo()


def isolate_executor_runtime(tmp_path: Path, monkeypatch) -> IsolatedExecutorRuntime:
    data_dir = tmp_path / "data"
    config_dir = tmp_path / "config"
    logs_dir = tmp_path / "logs"
    hold_dir = tmp_path / "holds"
    marker_dir = tmp_path / "markers"
    data_dir.mkdir()
    logs_dir.mkdir()
    hold_dir.mkdir()
    marker_dir.mkdir()
    shutil.copytree(REPO_ROOT / "config", config_dir)
    (config_dir / "models_config.json").write_text(
        json.dumps(
            {
                "default_params": {
                    "thinking_enabled": False,
                    "reasoning_effort": "low",
                    "temperature": 0,
                    "top_p": 1.0,
                    "presence_penalty": 0.0,
                    "thinking_budget": None,
                },
                "providers": {
                    "local-test": {
                        "name": "Local Test",
                        "provider_type": "openai_compatible",
                        "base_url": "http://127.0.0.1:9/v1",
                        "api_key": "test-key-not-used",
                        "models": ["test-model"],
                    }
                },
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    agents_path = config_dir / "agents_config.json"
    agents = json.loads(agents_path.read_text(encoding="utf-8"))
    agents["agents"]["main"]["model"] = "local-test:test-model"
    agents_path.write_text(
        json.dumps(agents, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    workflows_dir = data_dir / "workflows"
    monkeypatch.setenv("DETERMINFLOW_DATA_DIR", str(data_dir))
    monkeypatch.setenv("DETERMINFLOW_CONFIG_DIR", str(config_dir))
    monkeypatch.setenv("DETERMINFLOW_LOGS_DIR", str(logs_dir))
    monkeypatch.setenv("DETERMINFLOW_WORKFLOW_EXECUTOR_MODE", "inline")
    monkeypatch.setenv("DETERMINFLOW_HOLD_DIR", str(hold_dir))
    monkeypatch.setenv("DETERMINFLOW_MARKER_DIR", str(marker_dir))
    monkeypatch.setattr(config_module, "DATA_DIR", data_dir)
    monkeypatch.setattr(config_module, "CONFIG_DIR", config_dir)
    monkeypatch.setattr(config_module, "LOGS_DIR", logs_dir)
    monkeypatch.setattr(config_module, "WORKFLOWS_DIR", workflows_dir)
    monkeypatch.setattr(
        config_module, "WORKFLOW_WORKSPACES_DIR", data_dir / "workspaces",
    )
    monkeypatch.setattr(workflow_manager_module, "DATA_DIR", data_dir)
    monkeypatch.setattr(workflow_manager_module, "WORKFLOWS_DIR", workflows_dir)
    monkeypatch.setattr(task_recovery_module, "WORKFLOWS_DIR", workflows_dir)
    monkeypatch.setattr(executor_supervisor_module, "DATA_DIR", data_dir)
    return IsolatedExecutorRuntime(
        data_dir=data_dir,
        config_dir=config_dir,
        logs_dir=logs_dir,
        workflows_dir=workflows_dir,
        hold_dir=hold_dir,
        marker_dir=marker_dir,
    )


def controller_manager() -> WorkflowManager:
    return WorkflowManager(SimpleNamespace(sessions={}))


def install_script_workflow(
    manager: WorkflowManager,
    runtime: IsolatedExecutorRuntime,
    *,
    workflow_id: str,
    script_name: str,
    source: str,
    timeout: str = "45",
    auto_retry_count: int = 0,
    auto_retry_interval_seconds: int = 0,
) -> None:
    created = manager.create_workflow({
        "workflow_id": workflow_id,
        "name": workflow_id,
        "nodes": [{
            "id": script_name,
            "label": script_name,
            "node_type": "script",
            "auto_retry_count": auto_retry_count,
            "auto_retry_interval_seconds": auto_retry_interval_seconds,
            "node_params": {
                "script_source": "inline",
                "script_type": "python",
                "script_name": script_name,
                "timeout": timeout,
            },
        }],
        "edges": [],
    })
    if "definition" not in created:
        raise AssertionError(f"failed to create workflow: {created}")
    script_dir = runtime.workflows_dir / workflow_id / "script"
    script_dir.mkdir(parents=True, exist_ok=True)
    (script_dir / f"{script_name}.py").write_text(source, encoding="utf-8")


async def start_real_pool(
    runtime: IsolatedExecutorRuntime,
    *,
    on_restart=None,
    executor_count: int = POOL_SIZE,
) -> WorkflowExecutorPool:
    async def discard_event(_channel, _event):
        return None

    pool = WorkflowExecutorPool(
        executor_count,
        data_dir=runtime.data_dir,
        startup_timeout=POOL_STARTUP_TIMEOUT,
        shutdown_timeout=POOL_SHUTDOWN_TIMEOUT,
        restart_backoff_max=0.1,
        on_restart=on_restart,
        event_handler=discard_event,
    )
    await pool.start()
    return pool


def expected_executor_ids(executor_count: int) -> list[str]:
    return [f"workflow-executor-{index}" for index in range(executor_count)]


def assert_distinct_member_pids(
    pool: WorkflowExecutorPool,
    expected_count: int | None = None,
) -> dict[str, int]:
    pids = pool.member_pids
    count = expected_count if expected_count is not None else len(pool.executor_ids)
    expected_ids = set(expected_executor_ids(count))
    assert set(pids) == expected_ids
    assert all(pid is not None for pid in pids.values())
    assert len(set(pids.values())) == count
    return {executor_id: int(pid) for executor_id, pid in pids.items()}


async def wait_until(predicate, *, timeout: float, message: str) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    last_error: Exception | None = None
    while asyncio.get_running_loop().time() < deadline:
        try:
            ready = predicate()
            if inspect.isawaitable(ready):
                raise TypeError("wait_until predicate must be synchronous")
            if ready:
                return
        except Exception as exc:  # noqa: BLE001 - surface last probe error
            last_error = exc
        await asyncio.sleep(WAIT_INTERVAL)
    suffix = f": {last_error}" if last_error is not None else ""
    pytest.fail(f"timed out waiting for {message}{suffix}")


async def wait_processes_dead(pids: list[int], *, timeout: float, message: str) -> None:
    await wait_until(
        lambda: all(not process_alive(pid) for pid in pids),
        timeout=timeout,
        message=message,
    )


def release_hold(runtime: IsolatedExecutorRuntime, task_id: str | None = None) -> None:
    roots = (
        [runtime.hold_dir / task_id]
        if task_id is not None
        else [path for path in runtime.hold_dir.iterdir() if path.is_dir()]
    )
    for root in roots:
        root.mkdir(parents=True, exist_ok=True)
        (root / "release").write_text("1", encoding="utf-8")


def force_kill_pids(pids: list[int]) -> None:
    for pid in pids:
        force_kill_pid(pid)
        if os.name == "nt":
            continue
        try:
            os.killpg(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            pass


async def stop_pool_and_collect_leftovers(
    pool: WorkflowExecutorPool | None,
    tracker: ProcessTracker,
    runtime: IsolatedExecutorRuntime,
) -> list[int]:
    release_hold(runtime)
    stop_error = None
    if pool is not None:
        try:
            await pool.stop()
        except Exception as exc:  # noqa: BLE001 - assert after process cleanup
            stop_error = exc
    leftover = tracker.live()
    if leftover:
        force_kill_pids(leftover)
        await asyncio.sleep(0.05)
    if stop_error is not None:
        raise stop_error
    return leftover
