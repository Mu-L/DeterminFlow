"""Build the PyInstaller onedir backend used by the Tauri bundle."""

from __future__ import annotations

import logging
import shutil
import subprocess
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from desktop.scripts.stage_defaults import stage_defaults


LOGGER = logging.getLogger("desktop.build_backend")


def build_backend(repo_root: Path) -> Path:
    desktop_root = repo_root / "desktop"
    runtime_root = desktop_root / "runtime"
    backend_dir = runtime_root / "backend"
    work_dir = desktop_root / ".build" / "pyinstaller"
    generated = desktop_root / "generated" / "default-config"

    stage_defaults(repo_root, generated)
    shutil.rmtree(backend_dir, ignore_errors=True)
    subprocess.run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--clean",
            "--distpath",
            str(runtime_root),
            "--workpath",
            str(work_dir),
            str(desktop_root / "pyinstaller" / "determinflow-backend.spec"),
        ],
        cwd=repo_root,
        check=True,
    )

    executable_name = (
        "determinflow-backend.exe" if sys.platform == "win32" else "determinflow-backend"
    )
    executable = backend_dir / executable_name
    if not executable.is_file():
        raise RuntimeError(f"PyInstaller 后端未生成: {executable}")

    python_alias = backend_dir / ("python.exe" if sys.platform == "win32" else "python")
    shutil.copy2(executable, python_alias)
    LOGGER.info("桌面后端已生成: %s", backend_dir)
    return backend_dir


def main() -> int:
    repo_root = Path(__file__).resolve().parents[2]
    build_backend(repo_root)
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    raise SystemExit(main())
