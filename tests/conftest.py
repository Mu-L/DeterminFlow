"""Test-process fallback for the community Executor Pool defaults.

Community runtime defaults are process mode with four members. Tests keep the
supported environment-variable rollback to inline/1 unless a case overrides it.
"""

from __future__ import annotations

import os

os.environ.setdefault("DETERMINFLOW_WORKFLOW_EXECUTOR_MODE", "inline")
os.environ.setdefault("DETERMINFLOW_WORKFLOW_EXECUTOR_COUNT", "1")
