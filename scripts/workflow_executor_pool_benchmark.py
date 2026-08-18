#!/usr/bin/env python3
"""Run real WorkflowExecutorPool load and SIGKILL acceptance cases.

Prints one machine-readable JSON object to stdout. Exits 0 on success and
non-zero on argument or scenario failure. Does not add third-party
dependencies.

Examples:

  # Heavy matrix: members 1/2/4 x tasks 20/50, plus a 2-member SIGKILL case
  python3 scripts/workflow_executor_pool_benchmark.py --matrix

  # Single load cell
  python3 scripts/workflow_executor_pool_benchmark.py --members 2 --tasks 20 \\
      --cpu-busy-seconds 0.15

  # Load plus SIGKILL
  python3 scripts/workflow_executor_pool_benchmark.py --members 2 --tasks 20 --fault
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
HARNESS = (
    REPO_ROOT / "tests" / "workflow_executor_pool_load_harness.py"
)


def _load_harness():
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    spec = importlib.util.spec_from_file_location(
        "workflow_executor_pool_load_harness", HARNESS,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def main(argv: list[str] | None = None) -> int:
    return _load_harness().run_cli(argv)


if __name__ == "__main__":
    raise SystemExit(main())
