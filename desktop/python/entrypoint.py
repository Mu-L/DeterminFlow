"""Executable entry for the bundled DeterminFlow backend."""

from __future__ import annotations

import argparse
import multiprocessing
import runpy
import sys
from pathlib import Path

from desktop.python.runtime import prepare_runtime


def _run_python_compatibility_mode(arguments: list[str]) -> bool:
    """Let the frozen executable act as the Workflow Python interpreter."""
    if not arguments:
        return False
    if arguments[0] == "-m" and len(arguments) >= 2:
        module_name = arguments[1]
        sys.argv = [module_name, *arguments[2:]]
        runpy.run_module(module_name, run_name="__main__", alter_sys=True)
        return True
    script = Path(arguments[0])
    if script.suffix.lower() != ".py":
        return False
    sys.argv = arguments
    runpy.run_path(str(script), run_name="__main__")
    return True


def _parse_server_arguments(arguments: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="DeterminFlow desktop backend")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--user-data-dir", type=Path, required=True)
    return parser.parse_args(arguments)


def main(arguments: list[str] | None = None) -> int:
    multiprocessing.freeze_support()
    values = list(sys.argv[1:] if arguments is None else arguments)
    if _run_python_compatibility_mode(values):
        return 0

    options = _parse_server_arguments(values)
    prepare_runtime(options.user_data_dir, options.port)

    import uvicorn
    from src.web_server import app

    uvicorn.run(
        app,
        host="127.0.0.1",
        port=options.port,
        log_config=None,
        access_log=False,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
