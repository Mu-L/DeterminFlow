"""Validation for file-backed Plugin resource bundles."""

from __future__ import annotations

from pathlib import Path

from src.extension_api.registrar import ExtensionContributions
from src.rules.loader import RuleLoader
from src.skills.loader import SkillLoader
from src.workflow.script_library import ScriptLibraryCatalog


def validate_file_resources(
    base_dir: Path,
    existing: ExtensionContributions,
    pending: ExtensionContributions,
) -> None:
    """Validate bundles and script libraries across all active owners."""

    def owner_enabled(_owner: str) -> bool:
        return True

    skill_roots = [
        *existing.resource_paths.get("skill_bundles", []),
        *pending.resource_paths.get("skill_bundles", []),
    ]
    if skill_roots:
        SkillLoader(
            base_dir / "data" / "skills",
            resource_roots=skill_roots,
            owner_enabled=owner_enabled,
        ).validate_sources()

    rule_roots = [
        *existing.resource_paths.get("rule_bundles", []),
        *pending.resource_paths.get("rule_bundles", []),
    ]
    if rule_roots:
        RuleLoader(
            base_dir / "data" / "rules",
            resource_roots=rule_roots,
            owner_enabled=owner_enabled,
        ).validate_sources()

    script_roots = [
        *existing.resource_paths.get("script_libraries", []),
        *pending.resource_paths.get("script_libraries", []),
    ]
    if script_roots:
        ScriptLibraryCatalog(
            base_dir / "data" / "script-library",
            extension_roots=script_roots,
            owner_enabled=owner_enabled,
        ).validate_sources()
