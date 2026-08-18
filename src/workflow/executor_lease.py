"""Cross-platform process lease proving that only one Executor is alive."""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import BinaryIO


class ExecutorLeaseUnavailable(RuntimeError):
    pass


class ExecutorProcessLease:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._handle: BinaryIO | None = None

    def acquire(self, timeout: float) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.path.is_symlink():
            raise ExecutorLeaseUnavailable("Executor lease path must not be a symlink")
        handle = self.path.open("a+b")
        os.chmod(self.path, 0o600)
        handle.seek(0)
        if handle.read(1) == b"":
            handle.write(b"0")
            handle.flush()
        deadline = time.monotonic() + timeout
        while True:
            try:
                self._lock(handle)
                self._handle = handle
                return
            except (BlockingIOError, OSError) as exc:
                if time.monotonic() >= deadline:
                    handle.close()
                    raise ExecutorLeaseUnavailable(
                        "another Workflow Executor still owns the process lease"
                    ) from exc
                time.sleep(0.1)

    @staticmethod
    def _lock(handle: BinaryIO) -> None:
        if os.name == "nt":
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            return
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    def release(self) -> None:
        handle = self._handle
        self._handle = None
        if handle is None:
            return
        try:
            if os.name == "nt":
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()

    def __enter__(self):
        return self

    def __exit__(self, _exc_type, _exc, _traceback):
        self.release()
