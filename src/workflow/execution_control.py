"""Persistent admission control for controlled DeterminFlow releases."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
NORMAL_MODE = "normal"
DRAINING_MODE = "draining"
ACTIVE_TASK_STATUSES = frozenset({"running", "retry_waiting", "resume_pending"})
DEFAULT_RETRY_AFTER_SECONDS = 30


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _validate_retry_after(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError("retry_after_seconds must be an integer")
    if not 1 <= value <= 3600:
        raise ValueError("retry_after_seconds must be between 1 and 3600")
    return value


class ExecutionControl:
    """Read and atomically update the persistent Workflow admission mode."""

    def __init__(self, data_dir: Path, workflows_dir: Path | None = None):
        self.data_dir = Path(data_dir)
        self.workflows_dir = Path(workflows_dir or self.data_dir / "workflows")
        self.path = self.data_dir / "system" / "execution-control.json"

    def _normal_default(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "mode": NORMAL_MODE,
            "reason": "",
            "changed_at": None,
            "retry_after_seconds": DEFAULT_RETRY_AFTER_SECONDS,
            "state_valid": True,
        }

    def read(self) -> dict[str, Any]:
        """Return a validated snapshot; malformed state fails closed."""
        if not self.path.exists():
            state = self._normal_default()
            state["source"] = "default"
            return self._project(state)
        try:
            if self.path.is_symlink() or not self.path.is_file():
                raise ValueError("control path must be a regular file")
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise TypeError("control document must be an object")
            if raw.get("schema_version") != SCHEMA_VERSION:
                raise ValueError("unsupported schema_version")
            mode = raw.get("mode")
            if mode not in {NORMAL_MODE, DRAINING_MODE}:
                raise ValueError("unsupported execution mode")
            reason = raw.get("reason", "")
            changed_at = raw.get("changed_at")
            if not isinstance(reason, str) or not isinstance(changed_at, str):
                raise TypeError("reason and changed_at must be strings")
            retry_after = _validate_retry_after(raw.get("retry_after_seconds"))
            state = {
                "schema_version": SCHEMA_VERSION,
                "mode": mode,
                "reason": reason,
                "changed_at": changed_at,
                "retry_after_seconds": retry_after,
                "state_valid": True,
                "source": "file",
            }
            return self._project(state)
        except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
            return self._project({
                "schema_version": SCHEMA_VERSION,
                "mode": DRAINING_MODE,
                "reason": "invalid_control_state",
                "changed_at": None,
                "retry_after_seconds": DEFAULT_RETRY_AFTER_SECONDS,
                "state_valid": False,
                "source": "invalid",
                "validation_error": str(exc),
            })

    @staticmethod
    def _project(state: dict[str, Any]) -> dict[str, Any]:
        projected = dict(state)
        projected["accepting_new_tasks"] = (
            state["state_valid"] and state["mode"] == NORMAL_MODE
        )
        return projected

    def write(
        self,
        mode: str,
        *,
        reason: str = "",
        retry_after_seconds: int = DEFAULT_RETRY_AFTER_SECONDS,
    ) -> dict[str, Any]:
        if mode not in {NORMAL_MODE, DRAINING_MODE}:
            raise ValueError("mode must be normal or draining")
        if not isinstance(reason, str):
            raise TypeError("reason must be a string")
        retry_after = _validate_retry_after(retry_after_seconds)
        parent = self.path.parent
        parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if parent.is_symlink() or not parent.is_dir():
            raise ValueError("control directory must be a real directory")
        document = {
            "schema_version": SCHEMA_VERSION,
            "mode": mode,
            "reason": reason,
            "changed_at": _now_iso(),
            "retry_after_seconds": retry_after,
        }
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".execution-control.", suffix=".tmp", dir=parent,
        )
        temporary = Path(temporary_name)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(document, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
            directory_fd = os.open(parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            temporary.unlink(missing_ok=True)
        return self.read()

    def activity(self) -> dict[str, Any]:
        counts = {status: 0 for status in sorted(ACTIVE_TASK_STATUSES)}
        unreadable_task_files = 0
        scanned_task_files = 0
        if self.workflows_dir.exists():
            for task_path in self.workflows_dir.glob("*/tasks/*.json"):
                scanned_task_files += 1
                try:
                    if task_path.is_symlink() or not task_path.is_file():
                        raise ValueError("task path must be a regular file")
                    task = json.loads(task_path.read_text(encoding="utf-8"))
                    if not isinstance(task, dict) or not isinstance(
                        task.get("status"), str
                    ):
                        raise TypeError("task document has no status")
                except (
                    OSError,
                    UnicodeError,
                    json.JSONDecodeError,
                    TypeError,
                    ValueError,
                ):
                    unreadable_task_files += 1
                    continue
                status = task["status"]
                if status in counts:
                    counts[status] += 1
        active_task_count = sum(counts.values())
        return {
            "active_task_count": active_task_count,
            "active_status_counts": counts,
            "scanned_task_files": scanned_task_files,
            "unreadable_task_files": unreadable_task_files,
            "quiescent": active_task_count == 0 and unreadable_task_files == 0,
        }

    def snapshot(self) -> dict[str, Any]:
        return {**self.read(), "workflow_activity": self.activity()}


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--workflows-dir", type=Path)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("status")
    drain = subparsers.add_parser("drain")
    drain.add_argument("--reason", default="production_release")
    drain.add_argument(
        "--retry-after-seconds", type=int, default=DEFAULT_RETRY_AFTER_SECONDS,
    )
    resume = subparsers.add_parser("resume")
    resume.add_argument("--reason", default="production_release_complete")
    resume.add_argument(
        "--retry-after-seconds", type=int, default=DEFAULT_RETRY_AFTER_SECONDS,
    )
    subparsers.add_parser("activity")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    control = ExecutionControl(args.data_dir, args.workflows_dir)
    if args.command == "drain":
        result = control.write(
            DRAINING_MODE,
            reason=args.reason,
            retry_after_seconds=args.retry_after_seconds,
        )
    elif args.command == "resume":
        result = control.write(
            NORMAL_MODE,
            reason=args.reason,
            retry_after_seconds=args.retry_after_seconds,
        )
    elif args.command == "activity":
        result = control.activity()
    else:
        result = control.snapshot()
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result.get("state_valid", True) else 2


if __name__ == "__main__":
    raise SystemExit(main())
