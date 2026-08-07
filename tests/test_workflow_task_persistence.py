from __future__ import annotations

import json
import os

import pytest

from src.workflow import task_persistence


def _windows_permission_error(winerror: int) -> PermissionError:
    error = PermissionError(f"simulated WinError {winerror}")
    error.winerror = winerror
    return error


def _temporary_files(target) -> list:
    return list(target.parent.glob(f"{target.stem}.*.tmp"))


def test_task_state_write_retries_transient_windows_lock(tmp_path, monkeypatch):
    target = tmp_path / "task-demo.json"
    target.write_text('{"status": "running"}', encoding="utf-8")
    real_replace = os.replace
    attempts = 0
    sleeps: list[float] = []

    def flaky_replace(source, destination):
        nonlocal attempts
        attempts += 1
        if attempts <= 2:
            raise _windows_permission_error(5)
        real_replace(source, destination)

    monkeypatch.setattr(task_persistence.os, "replace", flaky_replace)
    monkeypatch.setattr(task_persistence.time, "sleep", sleeps.append)

    task_persistence.write_task_state_file(
        target,
        {"status": "completed", "node_states": {}},
    )

    assert attempts == 3
    assert sleeps == list(task_persistence.WINDOWS_REPLACE_RETRY_DELAYS[:2])
    assert json.loads(target.read_text(encoding="utf-8"))["status"] == "completed"
    assert _temporary_files(target) == []


def test_task_state_write_does_not_retry_other_errors(tmp_path, monkeypatch):
    target = tmp_path / "task-demo.json"
    target.write_text('{"status": "running"}', encoding="utf-8")
    sleeps: list[float] = []
    attempts = 0

    def fail_replace(_source, _destination):
        nonlocal attempts
        attempts += 1
        raise _windows_permission_error(3)

    monkeypatch.setattr(task_persistence.os, "replace", fail_replace)
    monkeypatch.setattr(task_persistence.time, "sleep", sleeps.append)

    with pytest.raises(PermissionError, match="WinError 3"):
        task_persistence.write_task_state_file(target, {"status": "completed"})

    assert attempts == 1
    assert sleeps == []
    assert json.loads(target.read_text(encoding="utf-8"))["status"] == "running"
    assert _temporary_files(target) == []


def test_task_state_write_stops_after_retry_budget(tmp_path, monkeypatch):
    target = tmp_path / "task-demo.json"
    target.write_text('{"status": "running"}', encoding="utf-8")
    sleeps: list[float] = []
    attempts = 0

    def fail_replace(_source, _destination):
        nonlocal attempts
        attempts += 1
        raise _windows_permission_error(32)

    monkeypatch.setattr(task_persistence.os, "replace", fail_replace)
    monkeypatch.setattr(task_persistence.time, "sleep", sleeps.append)

    with pytest.raises(PermissionError, match="WinError 32"):
        task_persistence.write_task_state_file(target, {"status": "completed"})

    assert attempts == len(task_persistence.WINDOWS_REPLACE_RETRY_DELAYS) + 1
    assert sleeps == list(task_persistence.WINDOWS_REPLACE_RETRY_DELAYS)
    assert json.loads(target.read_text(encoding="utf-8"))["status"] == "running"
    assert _temporary_files(target) == []
