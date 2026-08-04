"""Prepare an isolated writable runtime for the desktop backend."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path


def bundle_root() -> Path:
    """Return the PyInstaller resource root or the source checkout root."""
    frozen_root = getattr(sys, "_MEIPASS", None)
    if frozen_root:
        return Path(frozen_root).resolve()
    return Path(__file__).resolve().parents[2]


def default_config_dir() -> Path:
    """Locate sanitized defaults created by the desktop staging step."""
    root = bundle_root()
    if getattr(sys, "frozen", False):
        return root / "config"
    return root / "desktop" / "generated" / "default-config"


def seed_user_config(user_root: Path, defaults_dir: Path | None = None) -> list[Path]:
    """Copy missing defaults without overwriting user configuration."""
    source_dir = defaults_dir or default_config_dir()
    if not source_dir.is_dir():
        raise RuntimeError(f"桌面默认配置目录不存在: {source_dir}")

    config_dir = user_root / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    created: list[Path] = []
    for source in sorted(source_dir.glob("*.json")):
        target = config_dir / source.name
        if target.exists():
            continue
        temporary = target.with_suffix(".json.tmp")
        shutil.copyfile(source, temporary)
        os.replace(temporary, target)
        created.append(target)
    return created


def prepare_runtime(user_root: Path, port: int) -> None:
    """Create writable directories and publish their paths before app import."""
    resolved_root = user_root.expanduser().resolve()
    seed_user_config(resolved_root)

    data_dir = resolved_root / "data"
    logs_dir = resolved_root / "logs"
    config_dir = resolved_root / "config"
    for directory in (data_dir, logs_dir, config_dir):
        directory.mkdir(parents=True, exist_ok=True)

    environment = {
        "DETERMINFLOW_DATA_DIR": str(data_dir),
        "DETERMINFLOW_LOGS_DIR": str(logs_dir),
        "DETERMINFLOW_CONFIG_DIR": str(config_dir),
        "DETERMINFLOW_DESKTOP": "1",
        "WEB_HOST": "127.0.0.1",
        "WEB_PORT": str(port),
    }
    os.environ.update(environment)
