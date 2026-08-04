"""Start the frozen backend and verify the real status endpoint."""

from __future__ import annotations

import argparse
import logging
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path


LOGGER = logging.getLogger("desktop.smoke_backend")


def _free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def smoke_backend(executable: Path, timeout: float = 60.0) -> None:
    port = _free_port()
    with tempfile.TemporaryDirectory(prefix="determinflow-desktop-smoke-") as root:
        log_path = Path(root) / "backend.log"
        with log_path.open("wb") as log_file:
            process = subprocess.Popen(
                [
                    str(executable),
                    "--port",
                    str(port),
                    "--user-data-dir",
                    root,
                ],
                stdin=subprocess.DEVNULL,
                stdout=log_file,
                stderr=subprocess.STDOUT,
            )
            try:
                deadline = time.monotonic() + timeout
                url = f"http://127.0.0.1:{port}/api/system/status"
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        break
                    try:
                        with urllib.request.urlopen(url, timeout=2) as response:
                            if response.status == 200:
                                LOGGER.info("冻结后端状态接口验证通过")
                                return
                    except (urllib.error.URLError, TimeoutError):
                        time.sleep(0.25)
                log_file.flush()
                details = log_path.read_text(encoding="utf-8", errors="replace")[-5000:]
                raise RuntimeError(f"冻结后端未就绪，退出码={process.poll()}\n{details}")
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)


def main() -> int:
    default_name = "determinflow-backend.exe" if sys.platform == "win32" else "determinflow-backend"
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "executable",
        nargs="?",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "runtime" / "backend" / default_name,
    )
    options = parser.parse_args()
    smoke_backend(options.executable.resolve())
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    raise SystemExit(main())
