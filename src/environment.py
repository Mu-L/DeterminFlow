"""DeterminFlow environment-variable compatibility helpers."""

from __future__ import annotations

import os


def get_determinflow_env(suffix: str, default: str | None = None) -> str | None:
    """Read the current name first, then the legacy ``AI_COMPANY_*`` alias."""
    current_name = f"DETERMINFLOW_{suffix}"
    legacy_name = f"AI_COMPANY_{suffix}"
    if current_name in os.environ:
        return os.environ[current_name]
    return os.getenv(legacy_name, default)


def determinflow_env_is_set(suffix: str) -> bool:
    """Return whether either the current or legacy name is explicitly set."""
    return (
        f"DETERMINFLOW_{suffix}" in os.environ
        or f"AI_COMPANY_{suffix}" in os.environ
    )
