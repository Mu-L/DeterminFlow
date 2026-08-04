"""Prepare an isolated writable runtime for the desktop backend."""

from __future__ import annotations

import hashlib
import json
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


def bundled_plugins_dir() -> Path:
    """Locate an optional Full-edition Plugin snapshot."""
    root = bundle_root()
    if getattr(sys, "frozen", False):
        return root / "bundled-plugins"
    return root / "desktop" / "generated" / "bundled-plugins"


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


def refresh_official_plugin_sources(
    user_root: Path,
    defaults_dir: Path | None = None,
) -> bool:
    """Refresh Core-owned official sources while preserving every custom source."""
    source_dir = defaults_dir or default_config_dir()
    packaged_path = source_dir / "plugin-sources.json"
    user_path = user_root / "config" / "plugin-sources.json"
    if not packaged_path.is_file() or not user_path.is_file():
        return False
    packaged = json.loads(packaged_path.read_text(encoding="utf-8"))
    current = json.loads(user_path.read_text(encoding="utf-8"))
    official = packaged.get("official_sources")
    custom = current.get("custom_sources", [])
    if not isinstance(official, list) or not isinstance(custom, list):
        raise RuntimeError("plugin-sources.json 的仓库列表无效")
    updated = {
        "schema_version": 1,
        "official_sources": official,
        "custom_sources": custom,
    }
    if updated == current:
        return False
    _write_json_atomic(user_path, updated)
    return True


def _write_json_atomic(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def seed_bundled_plugins(
    user_root: Path,
    snapshot_dir: Path | None = None,
) -> list[str]:
    """Merge a Full installer snapshot once without replacing user Plugin state."""
    source_root = snapshot_dir or bundled_plugins_dir()
    metadata_path = source_root / "release-plugins.json"
    lock_path = source_root / "plugins.lock.json"
    if not metadata_path.is_file() or not lock_path.is_file():
        return []

    metadata_bytes = metadata_path.read_bytes()
    bundle_id = hashlib.sha256(metadata_bytes).hexdigest()
    plugins_root = user_root / "data" / "plugins"
    marker = plugins_root / ".desktop-bundles" / f"{bundle_id}.json"
    if marker.is_file():
        return []

    metadata = json.loads(metadata_bytes)
    bundled_lock = json.loads(lock_path.read_text(encoding="utf-8"))
    bundled_records = bundled_lock.get("plugins")
    bundled_metadata = metadata.get("plugins")
    if not isinstance(bundled_records, dict) or not isinstance(
        bundled_metadata, dict
    ):
        raise RuntimeError("桌面 Full Plugin 快照无效")
    plugin_ids = sorted(bundled_records)
    if set(plugin_ids) != set(bundled_metadata):
        raise RuntimeError("桌面 Full Plugin 快照元数据不一致")

    destination_lock = plugins_root / "plugins.lock.json"
    if destination_lock.is_file():
        current_lock = json.loads(destination_lock.read_text(encoding="utf-8"))
    else:
        current_lock = {"schema_version": 1, "plugins": {}}
    current_records = current_lock.get("plugins")
    if current_lock.get("schema_version") != 1 or not isinstance(
        current_records, dict
    ):
        raise RuntimeError("用户 Plugin 锁文件无效，无法合并 Full 快照")

    installed: list[str] = []
    for plugin_id in plugin_ids:
        if plugin_id in current_records:
            continue
        record = bundled_records[plugin_id]
        checkout = record.get("active_revision", {}).get("checkout")
        relative_checkout = Path(checkout) if isinstance(checkout, str) else Path()
        if (
            not isinstance(checkout, str)
            or relative_checkout.is_absolute()
            or ".." in relative_checkout.parts
            or relative_checkout.parts[:2] != ("checkouts", plugin_id)
        ):
            raise RuntimeError(f"桌面 Full Plugin checkout 无效: {plugin_id}")
        source_checkout = source_root / relative_checkout
        target_checkout = plugins_root / relative_checkout
        if not source_checkout.is_dir():
            raise RuntimeError(f"桌面 Full Plugin checkout 不存在: {plugin_id}")
        if target_checkout.exists():
            raise RuntimeError(
                f"用户 Plugin 目录存在未登记 checkout: {target_checkout}"
            )
        target_checkout.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source_checkout, target_checkout)
        current_records[plugin_id] = record
        installed.append(plugin_id)

    if installed:
        _write_json_atomic(destination_lock, current_lock)

    config_path = user_root / "config" / "extensions.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    enabled = config.get("enabled")
    if not isinstance(enabled, list):
        raise RuntimeError("extensions.json 的 enabled 必须是数组")
    config["enabled"] = list(dict.fromkeys([*enabled, *plugin_ids]))
    _write_json_atomic(config_path, config)
    _write_json_atomic(
        marker,
        {"bundle_id": bundle_id, "plugins": plugin_ids},
    )
    return installed


def prepare_runtime(user_root: Path, port: int) -> None:
    """Create writable directories and publish their paths before app import."""
    resolved_root = user_root.expanduser().resolve()
    seed_user_config(resolved_root)
    refresh_official_plugin_sources(resolved_root)
    seed_bundled_plugins(resolved_root)

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
