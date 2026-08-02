from __future__ import annotations

from pathlib import Path

import pytest

from src.extension_api.registrar import OwnedPath
from src.rules.loader import RuleLoader, RuleResourceConflictError
from src.rules.manager import RuleManager
from src.skills.loader import SkillLoader, SkillResourceConflictError
from src.skills.manager import SkillManager


def _write_skill(root: Path, skill_id: str, body: str = "Skill body") -> Path:
    skill_dir = root / skill_id
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        (
            "---\n"
            f"name: {skill_id}\n"
            f"description: {skill_id} description\n"
            "---\n\n"
            f"{body}\n"
        ),
        encoding="utf-8",
    )
    return skill_dir


def _write_rule(root: Path, rule_id: str, body: str = "Rule body") -> Path:
    rule_dir = root / rule_id
    rule_dir.mkdir(parents=True)
    (rule_dir / "RULE.md").write_text(
        (
            "---\n"
            f"name: {rule_id}\n"
            f"description: {rule_id} description\n"
            "---\n\n"
            f"{body}\n"
        ),
        encoding="utf-8",
    )
    return rule_dir


def test_skill_loader_reads_enabled_plugin_bundles_and_preserves_owner(
    tmp_path: Path,
):
    user_root = tmp_path / "user-skills"
    plugin_root = tmp_path / "plugin-skills"
    _write_skill(user_root, "user-skill")
    plugin_skill = _write_skill(plugin_root, "novel-outline")
    (plugin_skill / "references").mkdir()
    (plugin_skill / "references" / "outline.md").write_text(
        "supporting material",
        encoding="utf-8",
    )

    manager = SkillManager(
        user_root,
        resource_roots=[OwnedPath("novel-workflows", plugin_root)],
        owner_enabled=lambda owner: owner == "novel-workflows",
    )

    assert [skill.id for skill in manager.list_all()] == [
        "novel-outline",
        "user-skill",
    ]
    loaded = manager.get_skill("novel-outline")
    assert loaded is not None
    assert loaded.content == "Skill body"
    assert loaded.metadata["resource_owner"] == "novel-workflows"
    assert loaded.metadata["resource_read_only"] is True
    assert loaded.metadata["skill_dir"] == str(plugin_skill.resolve())
    assert manager.get_skill_file(
        "novel-outline",
        "references/outline.md",
    ) == "supporting material"


def test_skill_loader_filters_disabled_plugin_owner(tmp_path: Path):
    user_root = tmp_path / "user-skills"
    plugin_root = tmp_path / "plugin-skills"
    _write_skill(plugin_root, "novel-outline")

    loader = SkillLoader(
        user_root,
        resource_roots=[OwnedPath("novel-workflows", plugin_root)],
        owner_enabled=lambda _owner: False,
    )

    assert loader.load_all() == []


def test_invalid_plugin_skill_bundle_fails_startup(tmp_path: Path):
    plugin_root = tmp_path / "plugin-skills"
    skill_dir = plugin_root / "invalid-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "missing frontmatter",
        encoding="utf-8",
    )
    loader = SkillLoader(
        tmp_path / "user-skills",
        resource_roots=[OwnedPath("novel-workflows", plugin_root)],
    )

    with pytest.raises(ValueError, match="Plugin Skill|SKILL.md"):
        loader.load_all()


def test_skill_loader_rejects_cross_owner_id_conflict(tmp_path: Path):
    user_root = tmp_path / "user-skills"
    first_root = tmp_path / "first-skills"
    second_root = tmp_path / "second-skills"
    _write_skill(first_root, "shared-skill")
    _write_skill(second_root, "shared-skill")

    loader = SkillLoader(
        user_root,
        resource_roots=[
            OwnedPath("first-plugin", first_root),
            OwnedPath("second-plugin", second_root),
        ],
    )

    with pytest.raises(
        SkillResourceConflictError,
        match="shared-skill.*first-plugin.*second-plugin",
    ):
        loader.load_all()


def test_plugin_skill_bundle_is_read_only(tmp_path: Path):
    plugin_root = tmp_path / "plugin-skills"
    skill_dir = _write_skill(plugin_root, "novel-outline")
    original = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
    manager = SkillManager(
        tmp_path / "user-skills",
        resource_roots=[OwnedPath("novel-workflows", plugin_root)],
    )

    with pytest.raises(PermissionError, match="只读"):
        manager.update_skill("novel-outline", {"content": "changed"})
    with pytest.raises(PermissionError, match="只读"):
        manager.delete_skill("novel-outline")

    assert (skill_dir / "SKILL.md").read_text(encoding="utf-8") == original


def test_rule_loader_reads_plugin_bundle_supporting_files_and_owner(
    tmp_path: Path,
):
    user_root = tmp_path / "user-rules"
    plugin_root = tmp_path / "plugin-rules"
    _write_rule(user_root, "user-rule")
    plugin_rule = _write_rule(plugin_root, "novel-continuity")
    (plugin_rule / "references").mkdir()
    (plugin_rule / "references" / "continuity.md").write_text(
        "supporting rule material",
        encoding="utf-8",
    )

    manager = RuleManager(
        user_root,
        resource_roots=[OwnedPath("novel-workflows", plugin_root)],
        owner_enabled=lambda owner: owner == "novel-workflows",
    )

    assert [rule.id for rule in manager.list_all()] == [
        "user-rule",
        "novel-continuity",
    ]
    loaded = manager.get_rule("novel-continuity")
    assert loaded is not None
    assert loaded.content == "Rule body"
    assert loaded.metadata["resource_owner"] == "novel-workflows"
    assert loaded.metadata["resource_read_only"] is True
    assert loaded.metadata["rule_dir"] == str(plugin_rule.resolve())
    assert manager.get_rule_file(
        "novel-continuity",
        "references/continuity.md",
    ) == "supporting rule material"


def test_rule_loader_filters_disabled_plugin_owner(tmp_path: Path):
    plugin_root = tmp_path / "plugin-rules"
    _write_rule(plugin_root, "novel-continuity")
    loader = RuleLoader(
        tmp_path / "user-rules",
        resource_roots=[OwnedPath("novel-workflows", plugin_root)],
        owner_enabled=lambda _owner: False,
    )

    assert loader.load_all() == []


def test_invalid_plugin_rule_bundle_fails_startup(tmp_path: Path):
    plugin_root = tmp_path / "plugin-rules"
    rule_dir = plugin_root / "invalid-rule"
    rule_dir.mkdir(parents=True)
    (rule_dir / "RULE.md").write_text(
        "missing frontmatter",
        encoding="utf-8",
    )
    loader = RuleLoader(
        tmp_path / "user-rules",
        resource_roots=[OwnedPath("novel-workflows", plugin_root)],
    )

    with pytest.raises(ValueError, match="Plugin Rule|RULE.md"):
        loader.load_all()


def test_rule_loader_rejects_cross_owner_id_conflict(tmp_path: Path):
    first_root = tmp_path / "first-rules"
    second_root = tmp_path / "second-rules"
    _write_rule(first_root, "shared-rule")
    _write_rule(second_root, "shared-rule")
    loader = RuleLoader(
        tmp_path / "user-rules",
        resource_roots=[
            OwnedPath("first-plugin", first_root),
            OwnedPath("second-plugin", second_root),
        ],
    )

    with pytest.raises(
        RuleResourceConflictError,
        match="shared-rule.*first-plugin.*second-plugin",
    ):
        loader.load_all()


def test_plugin_rule_bundle_is_read_only(tmp_path: Path):
    plugin_root = tmp_path / "plugin-rules"
    rule_dir = _write_rule(plugin_root, "novel-continuity")
    original = (rule_dir / "RULE.md").read_text(encoding="utf-8")
    manager = RuleManager(
        tmp_path / "user-rules",
        resource_roots=[OwnedPath("novel-workflows", plugin_root)],
    )

    with pytest.raises(PermissionError, match="只读"):
        manager.update_rule("novel-continuity", {"content": "changed"})
    with pytest.raises(PermissionError, match="只读"):
        manager.delete_rule("novel-continuity")

    assert (rule_dir / "RULE.md").read_text(encoding="utf-8") == original
