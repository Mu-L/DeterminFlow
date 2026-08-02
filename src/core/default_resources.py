"""Provision versioned Core resources into an instance data directory."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil


DEFAULT_RESOURCES_DIR = Path(__file__).parent / "defaults"
CORE_RESOURCE_MARKER = ".core-resources.json"
LEGACY_CORE_RESOURCE_HASHES = {
    # workflow-guide 2.5.0 was installed before Core tracked ownership hashes.
    "workflow-guide/SKILL.md": {
        "09438130631522c4a34ff5f236c424788d89bf4358ee41da69ffb0ae698d1390",  # pragma: allowlist secret
    },
}


def _file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_marker(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    return value if isinstance(value, dict) else {}


def provision_core_skills(skills_dir: Path) -> list[Path]:
    """Synchronize unmodified Core Skills while preserving customizations."""
    source_dir = DEFAULT_RESOURCES_DIR / "skills"
    if not source_dir.is_dir():
        return []

    skills_dir.mkdir(parents=True, exist_ok=True)
    marker_path = skills_dir / CORE_RESOURCE_MARKER
    previous_files = dict(_read_marker(marker_path).get("files", {}))
    installed_files: dict[str, dict[str, str]] = {}
    synchronized: list[Path] = []
    for source in sorted(
        path
        for path in source_dir.rglob("*")
        if path.is_file()
        and "__pycache__" not in path.parts
        and path.suffix not in {".pyc", ".pyo"}
    ):
        relative = source.relative_to(source_dir).as_posix()
        target = skills_dir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        source_hash = _file_hash(source)
        target_hash = _file_hash(target) if target.is_file() else ""
        previous_hash = str(
            (previous_files.get(relative) or {}).get("installed_hash", "")
        )
        legacy_owned = target_hash in LEGACY_CORE_RESOURCE_HASHES.get(relative, set())
        can_update = not target.exists() or target_hash == source_hash or (
            previous_hash and target_hash == previous_hash
        ) or legacy_owned
        if not can_update:
            continue
        if target_hash != source_hash:
            with source.open("rb") as source_file, target.open("wb") as target_file:
                shutil.copyfileobj(source_file, target_file)
            synchronized.append(target)
        installed_files[relative] = {"installed_hash": source_hash}

    for skill_name in sorted(path.name for path in source_dir.iterdir() if path.is_dir()):
        installed_skill = skills_dir / skill_name
        if not installed_skill.is_dir():
            continue
        for generated_file in sorted(installed_skill.rglob("*"), reverse=True):
            if not generated_file.is_file():
                continue
            if (
                "__pycache__" in generated_file.relative_to(installed_skill).parts
                or generated_file.suffix in {".pyc", ".pyo"}
            ):
                generated_file.unlink()
                parent = generated_file.parent
                while parent != installed_skill and parent.exists():
                    try:
                        parent.rmdir()
                    except OSError:
                        break
                    parent = parent.parent

    marker_data = {"files": installed_files}
    temp_marker = marker_path.with_suffix(".json.tmp")
    temp_marker.write_text(
        json.dumps(marker_data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.replace(temp_marker, marker_path)
    return synchronized
