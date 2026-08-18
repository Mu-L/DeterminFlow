"""Controller-owned pool of sticky local Workflow Executors."""

from __future__ import annotations

import asyncio
import json
import os
import threading
import time
from collections.abc import Awaitable, Callable
from pathlib import Path

from src.config import DATA_DIR

from .executor_client import ExecutorUnavailable, WorkflowExecutorClient
from .executor_events import EventHandler
from .executor_lease import ExecutorProcessLease
from .executor_protocol import ExecutorIdentity
from .executor_supervisor import WorkflowExecutorSupervisor


POOL_STATE_VERSION = 1
POOL_STATE_FILENAME = "workflow-executor-pool.json"
RestartCallback = Callable[[ExecutorIdentity, ExecutorIdentity], Awaitable[None]]


def executor_ids(count: int) -> tuple[str, ...]:
    if not 1 <= count <= 32:
        raise ValueError("Workflow Executor count must be between 1 and 32")
    return tuple(f"workflow-executor-{index}" for index in range(count))


def executor_lease_path(data_dir: Path, executor_id: str) -> Path:
    prefix = "workflow-executor-"
    suffix = executor_id[len(prefix):] if executor_id.startswith(prefix) else ""
    if not suffix.isdigit():
        raise ValueError(f"invalid Workflow Executor id: {executor_id!r}")
    index = int(suffix)
    filename = (
        "workflow-executor.lock"
        if index == 0
        else f"workflow-executor-{index}.lock"
    )
    return Path(data_dir) / "system" / filename


def _pool_state_path(data_dir: Path) -> Path:
    return Path(data_dir) / "system" / POOL_STATE_FILENAME


def load_recorded_executor_ids(data_dir: Path) -> tuple[str, ...]:
    """Return the last configured pool, defaulting to the Phase 1 member."""
    path = _pool_state_path(data_dir)
    if not path.exists():
        return ("workflow-executor-0",)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("Workflow Executor pool state is unreadable") from exc
    if not isinstance(raw, dict) or raw.get("version") != POOL_STATE_VERSION:
        raise RuntimeError("Workflow Executor pool state version is unsupported")
    ids = raw.get("executor_ids")
    if not isinstance(ids, list) or not ids:
        raise RuntimeError("Workflow Executor pool state has no executor_ids")
    parsed = tuple(str(executor_id) for executor_id in ids)
    if parsed != executor_ids(len(parsed)):
        raise RuntimeError("Workflow Executor pool state has invalid executor_ids")
    return parsed


def _save_pool_state(data_dir: Path, ids: tuple[str, ...]) -> None:
    path = _pool_state_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    payload = json.dumps(
        {"version": POOL_STATE_VERSION, "executor_ids": list(ids)},
        ensure_ascii=False,
        indent=2,
    ) + "\n"
    temporary = path.with_suffix(f".tmp-{os.getpid()}")
    temporary.write_text(payload, encoding="utf-8")
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)


class ExecutorLeaseGroup:
    """Acquire several member leases as one fail-closed ownership barrier."""

    def __init__(self, leases: list[ExecutorProcessLease]):
        self._leases = leases

    @classmethod
    def for_inline(cls, data_dir: Path, configured_count: int) -> ExecutorLeaseGroup:
        ids = set(load_recorded_executor_ids(data_dir))
        ids.update(executor_ids(configured_count))
        return cls([
            ExecutorProcessLease(executor_lease_path(data_dir, executor_id))
            for executor_id in sorted(ids)
        ])

    def acquire(self, timeout: float) -> None:
        deadline = time.monotonic() + timeout
        acquired: list[ExecutorProcessLease] = []
        try:
            for lease in self._leases:
                lease.acquire(max(0.0, deadline - time.monotonic()))
                acquired.append(lease)
        except Exception:
            for lease in reversed(acquired):
                lease.release()
            raise

    def release(self) -> None:
        for lease in reversed(self._leases):
            lease.release()


class WorkflowExecutorPool:
    """Supervise a fixed local pool and select owners for new Tasks."""

    def __init__(
        self,
        executor_count: int,
        *,
        data_dir: Path = DATA_DIR,
        startup_timeout: float = 60.0,
        shutdown_timeout: float = 30.0,
        restart_backoff_max: float = 5.0,
        on_restart: RestartCallback | None = None,
        event_handler: EventHandler | None = None,
    ):
        self.executor_ids = executor_ids(executor_count)
        self.data_dir = Path(data_dir)
        self.startup_timeout = startup_timeout
        self.shutdown_timeout = shutdown_timeout
        self.restart_backoff_max = restart_backoff_max
        self.on_restart = on_restart
        self.event_handler = event_handler
        self._supervisors: dict[str, WorkflowExecutorSupervisor] = {}
        self._retired_leases: list[ExecutorProcessLease] = []
        self._selection_lock = threading.Lock()
        self._selection_index = 0
        self._started = False

    @property
    def identities(self) -> tuple[ExecutorIdentity, ...]:
        return tuple(
            self._supervisors[executor_id].identity
            for executor_id in self.executor_ids
        )

    @property
    def member_pids(self) -> dict[str, int | None]:
        return {
            executor_id: supervisor.pid
            for executor_id, supervisor in self._supervisors.items()
        }

    def client_for(self, executor_id: str) -> WorkflowExecutorClient:
        supervisor = self._supervisors.get(executor_id)
        if supervisor is None or supervisor.client is None:
            raise ExecutorUnavailable(
                f"Workflow Executor is not in the active pool: {executor_id}"
            )
        return supervisor.client

    def supervisor_for(self, executor_id: str) -> WorkflowExecutorSupervisor | None:
        """Return local lifecycle metadata without exposing it over Executor RPC."""
        return self._supervisors.get(executor_id)

    def select_client(self, _task_key: str) -> WorkflowExecutorClient:
        if not self._started:
            raise ExecutorUnavailable("Workflow Executor pool has not started")
        with self._selection_lock:
            member_count = len(self.executor_ids)
            for _ in range(member_count):
                executor_id = self.executor_ids[self._selection_index % member_count]
                self._selection_index += 1
                supervisor = self._supervisors.get(executor_id)
                if (
                    supervisor is not None
                    and supervisor.is_ready
                    and supervisor.client is not None
                ):
                    return supervisor.client
        raise ExecutorUnavailable("No ready Workflow Executor is available")

    async def _handle_restart(
        self,
        previous: ExecutorIdentity,
        current: ExecutorIdentity,
    ) -> None:
        if self._started and self.on_restart is not None:
            await self.on_restart(previous, current)

    async def start(self) -> WorkflowExecutorPool:
        if self._started or self._supervisors:
            raise RuntimeError("Workflow Executor pool already started")
        previous_ids = set(load_recorded_executor_ids(self.data_dir))
        retired_ids = sorted(previous_ids.difference(self.executor_ids))
        try:
            for executor_id in retired_ids:
                lease = ExecutorProcessLease(
                    executor_lease_path(self.data_dir, executor_id)
                )
                await asyncio.to_thread(lease.acquire, self.startup_timeout)
                self._retired_leases.append(lease)
            for executor_id in self.executor_ids:
                supervisor = WorkflowExecutorSupervisor(
                    executor_id=executor_id,
                    lease_path=executor_lease_path(self.data_dir, executor_id),
                    startup_timeout=self.startup_timeout,
                    shutdown_timeout=self.shutdown_timeout,
                    restart_backoff_max=self.restart_backoff_max,
                    on_restart=self._handle_restart,
                    event_handler=self.event_handler,
                )
                self._supervisors[executor_id] = supervisor
                await supervisor.start()
            _save_pool_state(self.data_dir, self.executor_ids)
            self._started = True
            return self
        except Exception:
            await self.stop()
            raise

    async def stop(self) -> None:
        self._started = False
        supervisors = list(reversed(self._supervisors.values()))
        self._supervisors.clear()
        if supervisors:
            await asyncio.gather(
                *(supervisor.stop() for supervisor in supervisors),
                return_exceptions=True,
            )
        for lease in reversed(self._retired_leases):
            lease.release()
        self._retired_leases.clear()
