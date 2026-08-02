from __future__ import annotations

import asyncio
import socket
import sys
from pathlib import Path

import pytest

from src.plugin_system import (
    ProcessHealthCheck,
    ProcessManager,
    ProcessSpec,
    ProcessStartError,
)


def _run(coro):
    return asyncio.run(coro)


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def test_process_expands_cwd_env_and_writes_file_log(tmp_path: Path) -> None:
    plugin_dir = tmp_path / "plugin"
    plugin_dir.mkdir()
    manager = ProcessManager(tmp_path / "logs")
    spec = ProcessSpec(
        process_id="worker",
        argv=(
            sys.executable,
            "-c",
            (
                "import os,time;"
                "print(os.getcwd(), flush=True);"
                "print(os.environ['PLUGIN_VALUE'], flush=True);"
                "time.sleep(30)"
            ),
        ),
        cwd="${PLUGIN_DIR}",
        env={"PLUGIN_VALUE": "${CONFIGURED_VALUE}"},
        startup_timeout_seconds=1,
        shutdown_timeout_seconds=1,
    )

    async def exercise() -> None:
        await manager.start(
            "demo-plugin",
            [spec],
            placeholders={
                "PLUGIN_DIR": str(plugin_dir),
                "CONFIGURED_VALUE": "configured",
            },
        )
        statuses = manager.statuses("demo-plugin")
        assert statuses[0]["status"] == "running"
        assert statuses[0]["pid"] > 0
        await manager.stop("demo-plugin")

    _run(exercise())
    log = (tmp_path / "logs/demo-plugin/worker.log").read_text(encoding="utf-8")
    assert str(plugin_dir) in log
    assert "configured" in log
    assert manager.statuses("demo-plugin")[0]["status"] == "stopped"


def test_http_health_check_waits_until_process_is_ready(tmp_path: Path) -> None:
    port = _free_port()
    manager = ProcessManager(tmp_path / "logs")
    spec = ProcessSpec(
        process_id="http",
        argv=(
            sys.executable,
            "-m",
            "http.server",
            "${PORT}",
            "--bind",
            "127.0.0.1",
        ),
        cwd="${plugin_dir}",
        health=ProcessHealthCheck(
            kind="http",
            url="http://127.0.0.1:${PORT}/",
            interval_seconds=0.02,
            request_timeout_seconds=0.2,
        ),
        startup_timeout_seconds=3,
        shutdown_timeout_seconds=1,
    )
    plugin_dir = tmp_path / "plugin"
    plugin_dir.mkdir()

    async def exercise() -> None:
        await manager.start(
            "demo-plugin",
            [spec],
            placeholders={"plugin_dir": str(plugin_dir), "port": str(port)},
        )
        assert manager.statuses("demo-plugin")[0]["status"] == "running"
        await manager.stop_all()

    _run(exercise())
    assert manager.statuses("demo-plugin")[0]["status"] == "stopped"


def test_unexpected_exit_notifies_without_status_polling_and_stops_siblings(
    tmp_path: Path,
) -> None:
    manager = ProcessManager(tmp_path / "logs")
    exited = ProcessSpec(
        process_id="short",
        argv=(sys.executable, "-c", "import time; time.sleep(0.2)"),
        shutdown_timeout_seconds=1,
    )
    sibling = ProcessSpec(
        process_id="long",
        argv=(sys.executable, "-c", "import time; time.sleep(30)"),
        shutdown_timeout_seconds=1,
    )

    async def exercise() -> None:
        notified = asyncio.Event()
        events: list[dict] = []

        async def handle_exit(owner: str, status: dict) -> None:
            assert owner == "demo-plugin"
            events.append(status)
            await manager.stop(owner)
            notified.set()

        manager.set_unexpected_exit_handler(handle_exit)
        await manager.start("demo-plugin", [exited, sibling])
        await asyncio.wait_for(notified.wait(), timeout=2)

        assert [item["process_id"] for item in events] == ["short"]
        statuses = {
            item["process_id"]: item
            for item in manager.statuses("demo-plugin")
        }
        assert statuses["short"]["status"] == "exited"
        assert statuses["long"]["status"] == "stopped"

    _run(exercise())


def test_normal_stop_does_not_notify_unexpected_exit_handler(
    tmp_path: Path,
) -> None:
    manager = ProcessManager(tmp_path / "logs")
    spec = ProcessSpec(
        process_id="worker",
        argv=(sys.executable, "-c", "import time; time.sleep(30)"),
        shutdown_timeout_seconds=1,
    )

    async def exercise() -> None:
        events: list[dict] = []
        manager.set_unexpected_exit_handler(
            lambda _owner, status: events.append(status)
        )
        await manager.start("demo-plugin", [spec])
        await manager.stop("demo-plugin")
        await asyncio.sleep(0)
        assert events == []

    _run(exercise())


def test_status_refresh_cannot_suppress_unexpected_exit_notification(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = ProcessManager(tmp_path / "logs")
    spec = ProcessSpec(
        process_id="short",
        argv=(sys.executable, "-c", "import time; time.sleep(0.1)"),
    )

    async def exercise() -> None:
        process_done = asyncio.Event()
        resume_watcher = asyncio.Event()
        notified = asyncio.Event()
        original_watch = manager._watch_process

        async def delayed_watch(record) -> None:
            await record.process.wait()
            process_done.set()
            await resume_watcher.wait()
            await original_watch(record)

        monkeypatch.setattr(manager, "_watch_process", delayed_watch)
        manager.set_unexpected_exit_handler(
            lambda _owner, _status: notified.set()
        )
        await manager.start("demo-plugin", [spec])
        await asyncio.wait_for(process_done.wait(), timeout=1)
        assert manager.statuses("demo-plugin")[0]["status"] == "exited"
        resume_watcher.set()
        await asyncio.wait_for(notified.wait(), timeout=1)
        await manager.stop("demo-plugin")

    _run(exercise())


def test_second_process_failure_rolls_back_first_process(tmp_path: Path) -> None:
    manager = ProcessManager(tmp_path / "logs")
    first = ProcessSpec(
        process_id="first",
        argv=(sys.executable, "-c", "import time; time.sleep(30)"),
        startup_timeout_seconds=1,
        shutdown_timeout_seconds=1,
    )
    broken = ProcessSpec(
        process_id="broken",
        argv=(str(tmp_path / "missing-executable"),),
        startup_timeout_seconds=1,
        shutdown_timeout_seconds=1,
    )

    with pytest.raises(ProcessStartError, match="broken"):
        _run(manager.start("demo-plugin", [first, broken]))

    statuses = {item["process_id"]: item for item in manager.statuses("demo-plugin")}
    assert statuses["first"]["status"] == "stopped"
    assert statuses["broken"]["status"] == "failed"


def test_failed_http_health_check_terminates_started_process(tmp_path: Path) -> None:
    manager = ProcessManager(tmp_path / "logs")
    spec = ProcessSpec(
        process_id="unhealthy",
        argv=(sys.executable, "-c", "import time; time.sleep(30)"),
        health=ProcessHealthCheck(
            kind="http",
            url="http://127.0.0.1:1/health",
            interval_seconds=0.01,
            request_timeout_seconds=0.01,
        ),
        startup_timeout_seconds=0.1,
        shutdown_timeout_seconds=0.1,
    )

    with pytest.raises(ProcessStartError, match="health"):
        _run(manager.start("demo-plugin", [spec]))

    status = manager.statuses("demo-plugin")[0]
    assert status["status"] == "failed"
    assert status["returncode"] is not None


def test_stop_kills_process_that_ignores_terminate(tmp_path: Path) -> None:
    manager = ProcessManager(tmp_path / "logs")
    spec = ProcessSpec(
        process_id="stubborn",
        argv=(
            sys.executable,
            "-c",
            (
                "import signal,time;"
                "signal.signal(signal.SIGTERM, signal.SIG_IGN);"
                "print('ready', flush=True);"
                "time.sleep(30)"
            ),
        ),
        startup_timeout_seconds=1,
        shutdown_timeout_seconds=0.05,
    )
    async def exercise() -> None:
        await manager.start("demo-plugin", [spec])
        await manager.stop("demo-plugin")

    _run(exercise())

    status = manager.statuses("demo-plugin")[0]
    assert status["status"] == "stopped"
    assert status["was_killed"] is True
    assert status["returncode"] is not None


def test_unknown_placeholder_fails_without_starting_process(tmp_path: Path) -> None:
    manager = ProcessManager(tmp_path / "logs")
    spec = ProcessSpec(
        process_id="invalid",
        argv=(sys.executable, "-c", "print('no')", "${MISSING}"),
    )

    with pytest.raises(ProcessStartError, match="placeholder"):
        _run(manager.start("demo-plugin", [spec]))

    assert manager.statuses("demo-plugin")[0]["status"] == "failed"


def test_plain_braces_are_not_treated_as_placeholders(tmp_path: Path) -> None:
    manager = ProcessManager(tmp_path / "logs")
    spec = ProcessSpec(
        process_id="braces",
        argv=(
            sys.executable,
            "-c",
            "import time; print({'key': 'value'}, flush=True); time.sleep(30)",
        ),
    )

    async def exercise() -> None:
        await manager.start("demo-plugin", [spec])
        await manager.stop("demo-plugin")

    _run(exercise())
    assert "{'key': 'value'}" in (
        tmp_path / "logs/demo-plugin/braces.log"
    ).read_text(encoding="utf-8")


@pytest.mark.parametrize("cwd", ["..", "${OUTSIDE}"])
def test_process_cwd_cannot_escape_plugin_directory(
    tmp_path: Path,
    cwd: str,
) -> None:
    plugin_dir = tmp_path / "plugin"
    plugin_dir.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    manager = ProcessManager(tmp_path / "logs")
    spec = ProcessSpec(
        process_id="escape",
        argv=(sys.executable, "-c", "print('no')"),
        cwd=cwd,
    )

    with pytest.raises(ProcessStartError, match="escapes PLUGIN_DIR"):
        _run(
            manager.start(
                "demo-plugin",
                [spec],
                placeholders={
                    "PLUGIN_DIR": str(plugin_dir),
                    "OUTSIDE": str(outside),
                },
            )
        )


def test_process_cwd_symlink_cannot_escape_plugin_directory(tmp_path: Path) -> None:
    plugin_dir = tmp_path / "plugin"
    plugin_dir.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (plugin_dir / "escaped").symlink_to(outside, target_is_directory=True)
    manager = ProcessManager(tmp_path / "logs")
    spec = ProcessSpec(
        process_id="symlink-escape",
        argv=(sys.executable, "-c", "print('no')"),
        cwd="escaped",
    )

    with pytest.raises(ProcessStartError, match="escapes PLUGIN_DIR"):
        _run(
            manager.start(
                "demo-plugin",
                [spec],
                placeholders={"PLUGIN_DIR": str(plugin_dir)},
            )
        )
