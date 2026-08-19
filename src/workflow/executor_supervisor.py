"""Controller-owned lifecycle for one Workflow Executor process."""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import sys
import tempfile
import threading
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from .executor_client import ExecutorUnavailable, WorkflowExecutorClient
from .executor_events import ControllerEventReceiver, EventHandler
from .executor_process import ExecutorProcessTree
from .executor_protocol import ExecutorIdentity
from .executor_transport import (
    AUTH_TOKEN_ENV,
    LOOPBACK_HOST,
    generate_auth_token,
    restrict_private_path,
    wait_for_endpoint_file,
)
from src.config import DATA_DIR


logger = logging.getLogger(__name__)
RestartCallback = Callable[[ExecutorIdentity, ExecutorIdentity], Awaitable[None]]

STATUS_STARTING = "starting"
STATUS_READY = "ready"
STATUS_RESTARTING = "restarting"
STATUS_STOPPING = "stopping"
STATUS_STOPPED = "stopped"
EXECUTOR_HEALTH_STATUSES = (
    STATUS_STARTING,
    STATUS_READY,
    STATUS_RESTARTING,
    STATUS_STOPPING,
    STATUS_STOPPED,
)


@dataclass(frozen=True)
class ExecutorReadinessSnapshot:
    """Immutable health view of one supervised Workflow Executor."""

    status: str
    is_ready: bool
    restart_count: int
    last_exit_code: int | None
    last_ready_at: float | None


class WorkflowExecutorSupervisor:
    def __init__(
        self,
        *,
        executor_id: str = "workflow-executor-0",
        lease_path: Path | None = None,
        startup_timeout: float = 60.0,
        shutdown_timeout: float = 30.0,
        restart_backoff_max: float = 5.0,
        on_restart: RestartCallback | None = None,
        event_handler: EventHandler | None = None,
    ):
        self.executor_id = executor_id
        self.lease_path = Path(lease_path) if lease_path is not None else (
            DATA_DIR / "system" / "workflow-executor.lock"
        )
        self.startup_timeout = startup_timeout
        self.shutdown_timeout = shutdown_timeout
        self.restart_backoff_max = restart_backoff_max
        self.on_restart = on_restart
        self.event_handler = event_handler
        self._runtime_dir: Path | None = None
        self._process: asyncio.subprocess.Process | None = None
        self._process_tree: ExecutorProcessTree | None = None
        self._monitor_task: asyncio.Task | None = None
        self._closing = False
        self.client: WorkflowExecutorClient | None = None
        self._event_receiver: ControllerEventReceiver | None = None
        self._auth_token: str | None = None
        self._state_lock = threading.Lock()
        self._status = STATUS_STOPPED
        self._restart_count = 0
        self._last_exit_code: int | None = None
        self._last_ready_at: float | None = None

    @property
    def identity(self) -> ExecutorIdentity:
        if self.client is None:
            raise ExecutorUnavailable("Workflow Executor has not started")
        return self.client.identity

    @property
    def pid(self) -> int | None:
        return self._process.pid if self._process is not None else None

    @property
    def status(self) -> str:
        with self._state_lock:
            return self._status

    @property
    def is_ready(self) -> bool:
        with self._state_lock:
            return (
                self._status == STATUS_READY
                and self._process is not None
                and self._process.returncode is None
            )

    @property
    def restart_count(self) -> int:
        with self._state_lock:
            return self._restart_count

    @property
    def last_exit_code(self) -> int | None:
        with self._state_lock:
            return self._last_exit_code

    @property
    def last_ready_at(self) -> float | None:
        with self._state_lock:
            return self._last_ready_at

    def snapshot(self) -> ExecutorReadinessSnapshot:
        with self._state_lock:
            is_ready = (
                self._status == STATUS_READY
                and self._process is not None
                and self._process.returncode is None
            )
            return ExecutorReadinessSnapshot(
                status=self._status,
                is_ready=is_ready,
                restart_count=self._restart_count,
                last_exit_code=self._last_exit_code,
                last_ready_at=self._last_ready_at,
            )

    def _record_exit_code(self, exit_code: int | None) -> None:
        if exit_code is None:
            return
        with self._state_lock:
            self._last_exit_code = exit_code

    def _set_status(self, status: str, *, exit_code: int | None = None) -> None:
        with self._state_lock:
            if exit_code is not None:
                self._last_exit_code = exit_code
            if status == STATUS_RESTARTING and self._status != STATUS_RESTARTING:
                self._restart_count += 1
            if status == STATUS_READY:
                self._last_ready_at = time.time()
            self._status = status

    def _mark_ready_if_open(self) -> None:
        if not self._closing:
            self._set_status(STATUS_READY)

    def _reap_process_tree(self) -> None:
        tree = self._process_tree
        self._process_tree = None
        if tree is not None:
            tree.reap()

    async def start(self) -> WorkflowExecutorClient:
        if self._process is not None:
            raise RuntimeError("Workflow Executor already started")
        self._closing = False
        self._set_status(STATUS_STARTING)
        self._runtime_dir = Path(tempfile.mkdtemp(prefix="determinflow-executor-"))
        restrict_private_path(self._runtime_dir, 0o700)
        try:
            if self.event_handler is not None:
                initial_identity = ExecutorIdentity(
                    self.executor_id, uuid.uuid4().hex,
                )
                self._auth_token = generate_auth_token()
                self._event_receiver = ControllerEventReceiver(
                    initial_identity,
                    self.event_handler,
                    auth_token=self._auth_token,
                )
                await self._event_receiver.start()
            await self._spawn()
        except Exception:
            self._process = None
            if self._event_receiver is not None:
                await self._event_receiver.close()
                self._event_receiver = None
            if self._runtime_dir is not None:
                shutil.rmtree(self._runtime_dir, ignore_errors=True)
                self._runtime_dir = None
            self._auth_token = None
            self._set_status(STATUS_STOPPED)
            raise
        self._mark_ready_if_open()
        self._monitor_task = asyncio.create_task(
            self._monitor(), name=f"{self.executor_id}-supervisor"
        )
        assert self.client is not None
        return self.client

    async def _spawn(self) -> None:
        assert self._runtime_dir is not None
        endpoint_path = self._runtime_dir / "rpc.endpoint"
        endpoint_path.unlink(missing_ok=True)
        identity = ExecutorIdentity(self.executor_id, uuid.uuid4().hex)
        auth_token = generate_auth_token()
        self._auth_token = auth_token
        if self._event_receiver is not None:
            self._event_receiver.update_identity(identity)
            self._event_receiver.update_auth_token(auth_token)
        environment = os.environ.copy()
        environment["DETERMINFLOW_RUNTIME_ROLE"] = "workflow-executor"
        environment[AUTH_TOKEN_ENV] = auth_token
        event_host = LOOPBACK_HOST
        event_port = 1
        if self._event_receiver is not None:
            event_host = self._event_receiver.endpoint.host
            event_port = self._event_receiver.endpoint.port
        process_tree = ExecutorProcessTree()
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "src.workflow.executor_worker",
            "--rpc-endpoint-path",
            str(endpoint_path),
            "--executor-id",
            identity.executor_id,
            "--executor-epoch",
            identity.epoch,
            "--parent-pid",
            str(os.getpid()),
            "--lease-path",
            str(self.lease_path),
            "--event-host",
            event_host,
            "--event-port",
            str(event_port),
            env=environment,
            **process_tree.spawn_options(),
        )
        self._process = process
        self._process_tree = process_tree
        try:
            process_tree.attach(process.pid)
        except Exception:
            await self._abort_process(process)
            raise
        deadline = asyncio.get_running_loop().time() + self.startup_timeout
        try:
            endpoint = await wait_for_endpoint_file(
                endpoint_path, process, deadline=deadline,
            )
        except Exception:
            await self._abort_process(process)
            raise
        if self.client is None:
            self.client = WorkflowExecutorClient(
                endpoint, identity, auth_token=auth_token,
            )
        else:
            self.client.update_identity(identity)
            self.client.update_transport(endpoint, auth_token)
        while True:
            if process.returncode is not None:
                self._record_exit_code(process.returncode)
                self._reap_process_tree()
                raise RuntimeError(
                    f"Workflow Executor exited during startup: {process.returncode}"
                )
            try:
                await self.client.call("ping")
                return
            except ExecutorUnavailable:
                if asyncio.get_running_loop().time() >= deadline:
                    await self._abort_process(process)
                    raise TimeoutError("Workflow Executor startup timed out")
                await asyncio.sleep(0.1)

    async def _abort_process(self, process: asyncio.subprocess.Process) -> None:
        if process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
        self._record_exit_code(process.returncode)
        self._reap_process_tree()

    async def _monitor(self) -> None:
        while not self._closing:
            process = self._process
            if process is None:
                return
            return_code = await process.wait()
            self._reap_process_tree()
            if self._closing:
                self._record_exit_code(return_code)
                return
            old_identity = self.identity
            self._set_status(STATUS_RESTARTING, exit_code=return_code)
            logger.error(
                "Workflow Executor exited unexpectedly: id=%s epoch=%s code=%s",
                old_identity.executor_id,
                old_identity.epoch,
                return_code,
            )
            delay = 0.1
            while not self._closing:
                try:
                    await self._spawn()
                    break
                except Exception:
                    logger.exception(
                        "Workflow Executor 重启失败，%.1f 秒后重试",
                        delay,
                    )
                    await asyncio.sleep(delay)
                    delay = min(delay * 2, self.restart_backoff_max)
            if self._closing:
                return
            new_identity = self.identity
            handoff_complete = self.on_restart is None
            if self.on_restart is not None:
                delay = 0.1
                while not self._closing:
                    if self._process is None or self._process.returncode is not None:
                        break
                    try:
                        await self.on_restart(old_identity, new_identity)
                        handoff_complete = True
                        break
                    except Exception:
                        logger.exception(
                            "Workflow Executor 死亡世代交接失败，%.1f 秒后重试",
                            delay,
                        )
                        await asyncio.sleep(delay)
                        delay = min(delay * 2, self.restart_backoff_max)
            if (
                handoff_complete
                and self._process is not None
                and self._process.returncode is None
            ):
                self._mark_ready_if_open()

    async def stop(self) -> None:
        self._closing = True
        self._set_status(STATUS_STOPPING)
        process = self._process
        if process is not None and process.returncode is None:
            try:
                if self.client is not None:
                    await self.client.call("shutdown")
                await asyncio.wait_for(process.wait(), timeout=self.shutdown_timeout)
            except (ExecutorUnavailable, asyncio.TimeoutError):
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), timeout=5.0)
                except asyncio.TimeoutError:
                    process.kill()
                    await process.wait()
        if process is not None and process.returncode is not None:
            self._record_exit_code(process.returncode)
            self._reap_process_tree()
        self._process = None
        if self._monitor_task is not None:
            if self._monitor_task is not asyncio.current_task():
                await asyncio.gather(self._monitor_task, return_exceptions=True)
            self._monitor_task = None
        if self._runtime_dir is not None:
            if self._event_receiver is not None:
                await self._event_receiver.close()
                self._event_receiver = None
            shutil.rmtree(self._runtime_dir, ignore_errors=True)
            self._runtime_dir = None
        self._auth_token = None
        self._set_status(STATUS_STOPPED)
