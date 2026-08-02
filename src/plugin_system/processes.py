"""Minimal same-host lifecycle management for trusted plugin processes."""

from __future__ import annotations

import asyncio
import logging
import os
import re
import signal
import time
import urllib.error
import urllib.request
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from inspect import isawaitable
from pathlib import Path
from typing import Any, Iterable, Mapping

from .models import ProcessHealthCheck, ProcessSpec, validate_plugin_id


_PLACEHOLDER_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
logger = logging.getLogger(__name__)


class ProcessManagerError(RuntimeError):
    """Base process lifecycle error."""


class ProcessStartError(ProcessManagerError):
    """Raised when a declared plugin process cannot become healthy."""


@dataclass
class _ManagedProcess:
    owner: str
    spec: ProcessSpec
    process: asyncio.subprocess.Process | None = None
    log_handle: Any = None
    status: str = "pending"
    error: str = ""
    returncode: int | None = None
    was_killed: bool = False
    started_at: float | None = None
    stop_requested: bool = False
    exit_notified: bool = False
    watcher: asyncio.Task[None] | None = None


@dataclass(frozen=True)
class _ExpandedProcess:
    argv: tuple[str, ...]
    cwd: Path
    env: dict[str, str]
    log_file: Path
    health: ProcessHealthCheck


class ProcessManager:
    """Start and stop trusted plugin child processes without a shell."""

    def __init__(self, log_root: Path):
        self.log_root = Path(log_root).expanduser().resolve()
        self.log_root.mkdir(parents=True, exist_ok=True)
        self._records: dict[str, list[_ManagedProcess]] = {}
        self._lock = asyncio.Lock()
        self._unexpected_exit_handler: (
            Callable[[str, dict[str, Any]], Awaitable[None] | None] | None
        ) = None

    def set_unexpected_exit_handler(
        self,
        handler: Callable[[str, dict[str, Any]], Awaitable[None] | None] | None,
    ) -> None:
        """Register the owner lifecycle callback for unexpected process exits."""
        self._unexpected_exit_handler = handler

    async def start(
        self,
        owner: str,
        specs: Iterable[ProcessSpec],
        *,
        placeholders: Mapping[str, str] | None = None,
    ) -> list[dict[str, Any]]:
        owner = validate_plugin_id(owner)
        declared = list(specs)
        if len({spec.process_id for spec in declared}) != len(declared):
            raise ProcessStartError(f"duplicate process id for plugin {owner}")
        values = {
            str(key).upper(): str(value)
            for key, value in (placeholders or {}).items()
        }
        async with self._lock:
            active = [
                record
                for record in self._records.get(owner, [])
                if record.process is not None and record.process.returncode is None
            ]
            if active:
                raise ProcessStartError(f"plugin processes already running: {owner}")
            records: list[_ManagedProcess] = []
            self._records[owner] = records
            for spec in declared:
                record = _ManagedProcess(owner=owner, spec=spec)
                records.append(record)
                try:
                    expanded = self._expand(owner, spec, values)
                    await self._start_one(record, expanded)
                except Exception as exc:
                    record.status = "failed"
                    record.error = str(exc)
                    await self._stop_record(record, preserve_failure=True)
                    for previous in reversed(records[:-1]):
                        await self._stop_record(previous)
                    if isinstance(exc, ProcessStartError):
                        raise
                    raise ProcessStartError(
                        f"process {owner}/{spec.process_id} failed: {exc}"
                    ) from exc
            for record in records:
                record.watcher = asyncio.create_task(
                    self._watch_process(record),
                    name=f"plugin-process:{owner}:{record.spec.process_id}",
                )
            return self.statuses(owner)

    async def stop(self, owner: str) -> None:
        owner = validate_plugin_id(owner)
        async with self._lock:
            for record in reversed(self._records.get(owner, [])):
                await self._stop_record(record)

    def begin_stop(self, owner: str) -> None:
        """Mark owner shutdown before its Extension asks children to exit."""
        owner = validate_plugin_id(owner)
        for record in self._records.get(owner, []):
            record.stop_requested = True

    async def stop_all(self) -> None:
        async with self._lock:
            for owner in reversed(list(self._records)):
                for record in reversed(self._records[owner]):
                    await self._stop_record(record)

    def statuses(self, owner: str | None = None) -> list[dict[str, Any]]:
        selected = (
            {owner: self._records.get(owner, [])}
            if owner is not None
            else self._records
        )
        result: list[dict[str, Any]] = []
        for current_owner in sorted(selected):
            for record in selected[current_owner]:
                self._refresh_record(record)
                result.append(
                    {
                        "owner": current_owner,
                        "process_id": record.spec.process_id,
                        "status": record.status,
                        "pid": record.process.pid if record.process is not None else None,
                        "returncode": record.returncode,
                        "error": record.error,
                        "was_killed": record.was_killed,
                    }
                )
        return result

    async def _start_one(
        self,
        record: _ManagedProcess,
        expanded: _ExpandedProcess,
    ) -> None:
        expanded.log_file.parent.mkdir(parents=True, exist_ok=True)
        record.log_handle = expanded.log_file.open("ab", buffering=0)
        record.status = "starting"
        try:
            record.process = await asyncio.create_subprocess_exec(
                *expanded.argv,
                cwd=str(expanded.cwd),
                env=expanded.env,
                stdout=record.log_handle,
                stderr=asyncio.subprocess.STDOUT,
                start_new_session=os.name == "posix",
            )
        except Exception:
            self._close_log(record)
            raise
        record.started_at = time.time()
        try:
            await self._wait_until_healthy(record, expanded.health)
        except Exception as exc:
            raise ProcessStartError(
                f"process {record.owner}/{record.spec.process_id} health check failed: {exc}"
            ) from exc
        record.status = "running"

    async def _wait_until_healthy(
        self,
        record: _ManagedProcess,
        health: ProcessHealthCheck,
    ) -> None:
        process = record.process
        if process is None:
            raise ProcessStartError("process was not created")
        if health.kind == "alive":
            await asyncio.sleep(
                min(0.05, max(0.001, record.spec.startup_timeout_seconds))
            )
            if process.returncode is not None:
                raise ProcessStartError(
                    f"process exited with return code {process.returncode}"
                )
            return

        deadline = asyncio.get_running_loop().time() + record.spec.startup_timeout_seconds
        last_error = "HTTP endpoint did not become ready"
        while asyncio.get_running_loop().time() < deadline:
            if process.returncode is not None:
                raise ProcessStartError(
                    f"process exited with return code {process.returncode}"
                )
            try:
                status = await asyncio.to_thread(
                    self._http_status,
                    health.url,
                    health.request_timeout_seconds,
                )
                if health.expected_status_min <= status <= health.expected_status_max:
                    return
                last_error = f"unexpected HTTP status {status}"
            except (OSError, urllib.error.URLError) as exc:
                last_error = str(exc)
            await asyncio.sleep(health.interval_seconds)
        raise ProcessStartError(last_error)

    async def _stop_record(
        self,
        record: _ManagedProcess,
        *,
        preserve_failure: bool = False,
    ) -> None:
        record.stop_requested = True
        process = record.process
        if process is not None and process.returncode is None:
            self._signal_process(process, signal.SIGTERM)
            try:
                await asyncio.wait_for(
                    process.wait(),
                    timeout=record.spec.shutdown_timeout_seconds,
                )
            except asyncio.TimeoutError:
                self._signal_process(process, signal.SIGKILL)
                record.was_killed = True
                await process.wait()
        if process is not None:
            record.returncode = process.returncode
        self._close_log(record)
        if not preserve_failure and record.status not in {"failed", "exited"}:
            record.status = "stopped"

    async def _watch_process(self, record: _ManagedProcess) -> None:
        process = record.process
        if process is None:
            return
        try:
            returncode = await process.wait()
            record.returncode = returncode
            if record.stop_requested:
                if record.status in {"running", "starting"}:
                    record.status = "stopped"
                self._close_log(record)
                return
            if record.status in {"running", "starting"}:
                record.status = "exited"
                record.error = (
                    f"process exited unexpectedly with return code {returncode}"
                )
            elif record.status != "exited":
                return
            self._close_log(record)
            if record.exit_notified:
                return
            record.exit_notified = True
            handler = self._unexpected_exit_handler
            if handler is not None:
                result = handler(record.owner, self._status(record))
                if isawaitable(result):
                    await result
        except asyncio.CancelledError:
            raise
        except Exception:
            # The process state remains observable even if owner cleanup fails.
            logger.exception(
                "Plugin 进程退出清理失败: %s/%s",
                record.owner,
                record.spec.process_id,
            )

    def _expand(
        self,
        owner: str,
        spec: ProcessSpec,
        placeholders: Mapping[str, str],
    ) -> _ExpandedProcess:
        try:
            argv = tuple(
                self._replace_placeholders(item, placeholders)
                for item in spec.argv
            )
            raw_cwd = (
                self._replace_placeholders(spec.cwd, placeholders)
                if spec.cwd
                else ""
            )
            env_values = {
                key: self._replace_placeholders(value, placeholders)
                for key, value in spec.env.items()
            }
            raw_log = (
                self._replace_placeholders(spec.log_file, placeholders)
                if spec.log_file
                else ""
            )
            health_url = (
                self._replace_placeholders(spec.health.url, placeholders)
                if spec.health.url
                else ""
            )
        except KeyError as exc:
            raise ProcessStartError(
                f"process {owner}/{spec.process_id} has unknown placeholder: {exc.args[0]}"
            ) from exc
        plugin_dir = Path(
            placeholders.get("PLUGIN_DIR", os.getcwd())
        ).expanduser().resolve()
        if not plugin_dir.is_dir():
            raise ProcessStartError(
                f"process {owner}/{spec.process_id} PLUGIN_DIR does not exist: "
                f"{plugin_dir}"
            )
        cwd = Path(raw_cwd).expanduser() if raw_cwd else plugin_dir
        if not cwd.is_absolute():
            cwd = plugin_dir / cwd
        cwd = cwd.resolve()
        if not cwd.is_dir():
            raise ProcessStartError(
                f"process {owner}/{spec.process_id} cwd does not exist: {cwd}"
            )
        try:
            cwd.relative_to(plugin_dir)
        except ValueError as exc:
            raise ProcessStartError(
                f"process {owner}/{spec.process_id} cwd escapes PLUGIN_DIR: {cwd}"
            ) from exc
        log_file = Path(raw_log).expanduser() if raw_log else (
            self.log_root / owner / f"{spec.process_id}.log"
        )
        if not log_file.is_absolute():
            log_file = self.log_root / owner / log_file
        env = os.environ.copy()
        env.update(env_values)
        health = ProcessHealthCheck(
            kind=spec.health.kind,
            url=health_url,
            interval_seconds=spec.health.interval_seconds,
            request_timeout_seconds=spec.health.request_timeout_seconds,
            expected_status_min=spec.health.expected_status_min,
            expected_status_max=spec.health.expected_status_max,
        )
        return _ExpandedProcess(
            argv=argv,
            cwd=cwd,
            env=env,
            log_file=log_file.resolve(),
            health=health,
        )

    @staticmethod
    def _http_status(url: str, timeout: float) -> int:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return int(response.status)

    @staticmethod
    def _replace_placeholders(
        value: str,
        placeholders: Mapping[str, str],
    ) -> str:
        def replace(match: re.Match[str]) -> str:
            key = match.group(1).upper()
            if key not in placeholders:
                raise KeyError(match.group(1))
            return placeholders[key]

        return _PLACEHOLDER_RE.sub(replace, value)

    @staticmethod
    def _close_log(record: _ManagedProcess) -> None:
        if record.log_handle is not None:
            record.log_handle.close()
            record.log_handle = None

    def _refresh_record(self, record: _ManagedProcess) -> None:
        process = record.process
        if process is None or process.returncode is None:
            return
        record.returncode = process.returncode
        if record.status in {"running", "starting"}:
            record.status = "stopped" if record.stop_requested else "exited"
            if not record.stop_requested:
                record.error = (
                    f"process exited unexpectedly with return code {process.returncode}"
                )
            self._close_log(record)

    @staticmethod
    def _status(record: _ManagedProcess) -> dict[str, Any]:
        return {
            "owner": record.owner,
            "process_id": record.spec.process_id,
            "status": record.status,
            "pid": record.process.pid if record.process is not None else None,
            "returncode": record.returncode,
            "error": record.error,
            "was_killed": record.was_killed,
        }

    @staticmethod
    def _signal_process(
        process: asyncio.subprocess.Process,
        process_signal: signal.Signals,
    ) -> None:
        try:
            if os.name == "posix":
                os.killpg(process.pid, process_signal)
            else:
                process.send_signal(process_signal)
        except ProcessLookupError:
            return
