"""Safe synchronization of Plugin-owned workflow definitions."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
from pathlib import Path

from src.extension_api.registrar import OwnedPath

logger = logging.getLogger(__name__)


def _read_marker(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _write_marker(path: Path, marker: dict) -> None:
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(marker, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _remove_empty_parents(path: Path, stop: Path) -> None:
    stop = stop.resolve()
    current = path.resolve()
    while current != stop:
        try:
            current.rmdir()
        except OSError:
            break
        current = current.parent


def _safe_workflow_target(
    target_dir: Path,
    relative_path: str,
) -> Path | None:
    target_root = target_dir.resolve()
    candidate = target_dir / relative_path
    try:
        candidate.resolve().relative_to(target_root)
    except ValueError:
        logger.warning(
            "忽略越界的扩展 Workflow marker 路径: %s",
            relative_path,
        )
        return None
    return candidate


def _sync_owned_file(
    source: Path,
    target: Path,
    previous: dict,
) -> dict[str, str]:
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    current_hash = (
        hashlib.sha256(target.read_bytes()).hexdigest()
        if target.exists()
        else ""
    )
    previous_installed_hash = str(previous.get("installed_hash", ""))
    should_install = (
        not target.exists()
        or current_hash == source_hash
        or (
            bool(previous_installed_hash)
            and current_hash == previous_installed_hash
        )
    )
    if should_install:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        installed_hash = source_hash
    else:
        installed_hash = previous_installed_hash
    return {
        "source_hash": source_hash,
        "installed_hash": installed_hash,
    }


def provision_plugin_workflows(
    owned_roots: list[OwnedPath],
    target_root: Path,
    *,
    active_owners: set[str],
) -> list[str]:
    """Synchronize active definitions and deactivate workflows removed upstream."""
    target_root.mkdir(parents=True, exist_ok=True)
    warnings: list[str] = []
    claimed_workflows: dict[str, str] = {}
    workflow_owners = set(active_owners)
    for owned_root in owned_roots:
        for source_dir in sorted(owned_root.path.iterdir()):
            definition = source_dir / "definition.json"
            if not source_dir.is_dir() or not definition.exists():
                continue
            existing_owner = claimed_workflows.get(source_dir.name)
            if existing_owner is not None:
                raise ValueError(
                    f"扩展 Workflow 冲突: {source_dir.name} "
                    f"({existing_owner} vs {owned_root.owner})"
                )
            claimed_workflows[source_dir.name] = owned_root.owner

            target_dir = target_root / source_dir.name
            marker_path = target_dir / ".extension.json"
            marker = _read_marker(marker_path)
            marker_owner = marker.get("owner")
            if marker_owner and marker_owner != owned_root.owner:
                raise ValueError(
                    f"扩展 Workflow 冲突: {source_dir.name} "
                    f"({marker_owner} vs {owned_root.owner})"
                )
            if (target_dir / "definition.json").exists() and not marker_owner:
                raise ValueError(
                    f"扩展 Workflow 与用户/Core Workflow 冲突: {source_dir.name}"
                )

            target_dir.mkdir(parents=True, exist_ok=True)
            previous_files = dict(marker.get("files", {}))
            previous_orphans = dict(marker.get("orphaned_files", {}))
            if marker.get("installed_hash"):
                previous_files.setdefault(
                    "definition.json",
                    {"installed_hash": marker["installed_hash"]},
                )

            source_files = [definition]
            source_scripts = source_dir / "script"
            if source_scripts.exists():
                source_files.extend(
                    path
                    for path in sorted(source_scripts.rglob("*"))
                    if path.is_file()
                    and "__pycache__" not in path.parts
                    and path.suffix not in {".pyc", ".pyo"}
                )

            installed_files = {}
            for source_file in source_files:
                relative_path = source_file.relative_to(source_dir).as_posix()
                installed_files[relative_path] = _sync_owned_file(
                    source_file,
                    target_dir / relative_path,
                    previous_files.get(relative_path, {}),
                )

            orphaned_files: dict[str, dict[str, str]] = {}
            current_paths = set(installed_files)
            stale_files = {
                **previous_orphans,
                **{
                    path: metadata
                    for path, metadata in previous_files.items()
                    if path not in current_paths
                },
            }
            for relative_path, previous in stale_files.items():
                if relative_path in current_paths:
                    continue
                target_file = _safe_workflow_target(target_dir, relative_path)
                if target_file is None or not target_file.exists():
                    continue
                if (
                    "__pycache__" in Path(relative_path).parts
                    or target_file.suffix in {".pyc", ".pyo"}
                ):
                    target_file.unlink()
                    _remove_empty_parents(target_file.parent, target_dir)
                    continue
                current_hash = hashlib.sha256(
                    target_file.read_bytes()
                ).hexdigest()
                installed_hash = str(previous.get("installed_hash", ""))
                if installed_hash and current_hash == installed_hash:
                    target_file.unlink()
                    _remove_empty_parents(target_file.parent, target_dir)
                    continue
                warning = (
                    f"扩展 Workflow {source_dir.name} 已移除文件 "
                    f"{relative_path}，检测到用户修改，已保留"
                )
                warnings.append(warning)
                logger.warning(warning)
                orphaned_files[relative_path] = {
                    "reason": "user_modified",
                    "installed_hash": installed_hash,
                    "current_hash": current_hash,
                }

            for generated_file in sorted(target_dir.rglob("*"), reverse=True):
                if not generated_file.is_file():
                    continue
                if (
                    "__pycache__" in generated_file.relative_to(target_dir).parts
                    or generated_file.suffix in {".pyc", ".pyo"}
                ):
                    generated_file.unlink()
                    _remove_empty_parents(generated_file.parent, target_dir)

            marker_data = {
                "owner": owned_root.owner,
                "active": True,
                "files": installed_files,
            }
            if orphaned_files:
                marker_data["orphaned_files"] = orphaned_files
            _write_marker(marker_path, marker_data)

    for target_dir in sorted(target_root.iterdir()):
        if not target_dir.is_dir() or target_dir.name in claimed_workflows:
            continue
        marker_path = target_dir / ".extension.json"
        marker = _read_marker(marker_path)
        if marker.get("owner") not in workflow_owners:
            continue
        marker["active"] = False
        _write_marker(marker_path, marker)
    return warnings
