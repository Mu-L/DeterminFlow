from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from src.extension_host.plugin_preflight import validate_plugin_checkout
from src.plugin_system import InvalidPluginPackageError


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _write_manifest(
    root: Path,
    resources: dict[str, str | list[str]],
    *,
    backend: str = "",
) -> None:
    root.mkdir(parents=True, exist_ok=True)
    lines = [
        "[extension]",
        'id = "demo-plugin"',
        'name = "Demo Plugin"',
        'version = "1.0.0"',
        'api_version = "1"',
        f'backend = "{backend}"',
        "",
        "[resource_namespace]",
        'prefix = "demo"',
        "",
        "[resources]",
    ]
    for resource_type, configured in resources.items():
        if isinstance(configured, list):
            rendered = ", ".join(f'"{item}"' for item in configured)
            lines.append(f"{resource_type} = [{rendered}]")
        else:
            lines.append(f'{resource_type} = "{configured}"')
    (root / "extension.toml").write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
    )


def _write_skill(root: Path, skill_id: str) -> None:
    skill_dir = root / skill_id
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        (
            "---\n"
            f"name: {skill_id}\n"
            f"description: {skill_id} description\n"
            "---\n\n"
            "Skill body\n"
        ),
        encoding="utf-8",
    )


def _write_rule(root: Path, rule_id: str) -> None:
    rule_dir = root / rule_id
    rule_dir.mkdir(parents=True, exist_ok=True)
    (rule_dir / "RULE.md").write_text(
        (
            "---\n"
            f"name: {rule_id}\n"
            f"description: {rule_id} description\n"
            "---\n\n"
            "Rule body\n"
        ),
        encoding="utf-8",
    )


def _checkout_snapshot(root: Path) -> dict[str, bytes | str]:
    result: dict[str, bytes | str] = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            result[relative] = f"symlink:{os.readlink(path)}"
        elif path.is_file():
            result[relative] = path.read_bytes()
        else:
            result[relative] = "directory"
    return result


def test_preflight_validates_every_resource_without_importing_or_executing(
    tmp_path: Path,
) -> None:
    plugin = tmp_path / "plugin"
    marker = plugin / "executed"
    _write_manifest(
        plugin,
        {
            "agents": "resources/agents.json",
            "prompts": "resources/prompts.json",
            "skills": "resources/skills.json",
            "skill_bundles": "resources/skill-bundles",
            "rules": "resources/rules.json",
            "rule_bundles": "resources/rule-bundles",
            "preset_phrases": "resources/phrases.json",
            "workflows": "resources/workflows",
            "script_libraries": "resources/scripts",
        },
        backend="untrusted_backend:create_extension",
    )
    (plugin / "untrusted_backend.py").write_text(
        (
            "from pathlib import Path\n"
            f"Path({str(marker)!r}).write_text('backend imported')\n"
        ),
        encoding="utf-8",
    )
    _write_json(
        plugin / "resources/agents.json",
        {
            "agents": {
                "writer": {
                    "name": "Writer",
                    "prompt_template": "writer",
                }
            }
        },
    )
    _write_json(
        plugin / "resources/prompts.json",
        {"agents": {"writer": {"sections": []}}},
    )
    _write_json(
        plugin / "resources/skills.json",
        {
            "skills": {"outline": {"group_ids": ["writing"]}},
            "skill_configs": {},
            "groups": [{"id": "writing", "skill_ids": ["outline"]}],
        },
    )
    _write_skill(plugin / "resources/skill-bundles", "outline")
    _write_json(
        plugin / "resources/rules.json",
        {
            "rules": {"continuity": {"group_ids": ["quality"]}},
            "rule_configs": {},
            "groups": [{"id": "quality", "rule_ids": ["continuity"]}],
        },
    )
    _write_rule(plugin / "resources/rule-bundles", "continuity")
    _write_json(
        plugin / "resources/phrases.json",
        {"phrases": [{"id": "start", "text": "Start"}]},
    )
    _write_json(
        plugin / "resources/workflows/draft/definition.json",
        {
            "workflow_id": "draft",
            "name": "Draft",
            "nodes": [],
            "edges": [],
        },
    )
    script_dir = plugin / "resources/scripts/tools/danger"
    script_dir.mkdir(parents=True)
    (script_dir / "danger.py").write_text(
        (
            "from pathlib import Path\n"
            f"Path({str(marker)!r}).write_text('script executed')\n"
        ),
        encoding="utf-8",
    )
    before = _checkout_snapshot(plugin)

    manifest = validate_plugin_checkout("demo-plugin", plugin)

    assert manifest.resource_prefix == "demo"
    assert not marker.exists()
    assert _checkout_snapshot(plugin) == before


@pytest.mark.parametrize(
    ("resource_type", "filename", "document", "message"),
    [
        ("agents", "agents.json", {"agents": []}, "agents 必须是对象"),
        (
            "prompts",
            "prompts.json",
            {"agents": {"writer": "not-an-object"}},
            "agents.writer 必须是对象",
        ),
        (
            "preset_phrases",
            "phrases.json",
            {"phrases": [{"id": "same"}, {"id": "same"}]},
            "重复 ID",
        ),
        (
            "skills",
            "skills.json",
            {"skills": {" ": {}}, "skill_configs": {}, "groups": []},
            "skills 包含空 ID",
        ),
    ],
)
def test_preflight_rejects_invalid_json_resource_content(
    tmp_path: Path,
    resource_type: str,
    filename: str,
    document: dict,
    message: str,
) -> None:
    plugin = tmp_path / "plugin"
    _write_manifest(plugin, {resource_type: f"resources/{filename}"})
    _write_json(plugin / f"resources/{filename}", document)

    with pytest.raises(InvalidPluginPackageError, match=message):
        validate_plugin_checkout("demo-plugin", plugin)


def test_preflight_rejects_invalid_workflow_definition(tmp_path: Path) -> None:
    plugin = tmp_path / "plugin"
    _write_manifest(plugin, {"workflows": "resources/workflows"})
    _write_json(
        plugin / "resources/workflows/broken/definition.json",
        {
            "workflow_id": "broken",
            "name": "Broken",
            "nodes": [{"id": "only-node", "agent_type": "default"}],
            "edges": [],
        },
    )

    with pytest.raises(
        InvalidPluginPackageError,
        match="Workflow definition 校验失败.*没有任何连线",
    ):
        validate_plugin_checkout("demo-plugin", plugin)


@pytest.mark.parametrize(
    ("resource_type", "directory", "filename", "message"),
    [
        ("skill_bundles", "skill-bundles/broken", "SKILL.md", "SKILL.md"),
        ("rule_bundles", "rule-bundles/broken", "RULE.md", "Plugin Rule"),
    ],
)
def test_preflight_rejects_invalid_bundle_content(
    tmp_path: Path,
    resource_type: str,
    directory: str,
    filename: str,
    message: str,
) -> None:
    plugin = tmp_path / "plugin"
    bundle_root = f"resources/{directory.rsplit('/', 1)[0]}"
    _write_manifest(plugin, {resource_type: bundle_root})
    target = plugin / "resources" / directory
    target.mkdir(parents=True)
    (target / filename).write_text("missing frontmatter", encoding="utf-8")

    with pytest.raises(InvalidPluginPackageError, match=message):
        validate_plugin_checkout("demo-plugin", plugin)


def test_preflight_rejects_script_identity_conflicts(tmp_path: Path) -> None:
    plugin = tmp_path / "plugin"
    _write_manifest(
        plugin,
        {"script_libraries": ["resources/first", "resources/second"]},
    )
    for root_name in ("first", "second"):
        script_dir = plugin / f"resources/{root_name}/tools/shared"
        script_dir.mkdir(parents=True)
        (script_dir / "shared.py").write_text(
            "def main():\n    return None\n",
            encoding="utf-8",
        )

    with pytest.raises(
        InvalidPluginPackageError,
        match="Script Library resource 冲突",
    ):
        validate_plugin_checkout("demo-plugin", plugin)


@pytest.mark.parametrize("use_symlink", [False, True])
def test_preflight_rejects_resource_path_escape_and_symlinks(
    tmp_path: Path,
    use_symlink: bool,
) -> None:
    plugin = tmp_path / "plugin"
    outside = tmp_path / "outside.json"
    _write_json(outside, {"agents": {}})
    if use_symlink:
        resource = plugin / "resources/agents.json"
        resource.parent.mkdir(parents=True)
        resource.symlink_to(outside)
        configured = "resources/agents.json"
        expected = "symlink"
    else:
        configured = "../outside.json"
        expected = "必须位于 Plugin 目录内"
    _write_manifest(plugin, {"agents": configured})

    with pytest.raises(InvalidPluginPackageError, match=expected):
        validate_plugin_checkout("demo-plugin", plugin)


def test_preflight_rejects_nested_bundle_symlink(tmp_path: Path) -> None:
    plugin = tmp_path / "plugin"
    _write_manifest(
        plugin,
        {"skill_bundles": "resources/skill-bundles"},
    )
    _write_skill(plugin / "resources/skill-bundles", "outline")
    outside = tmp_path / "outside.md"
    outside.write_text("outside", encoding="utf-8")
    references = (
        plugin / "resources/skill-bundles/outline/references"
    )
    references.mkdir()
    (references / "outside.md").symlink_to(outside)

    with pytest.raises(InvalidPluginPackageError, match="symlink"):
        validate_plugin_checkout("demo-plugin", plugin)


def test_preflight_applies_install_prefix_override_before_bundle_validation(
    tmp_path: Path,
) -> None:
    plugin = tmp_path / "plugin"
    _write_manifest(
        plugin,
        {"skill_bundles": "resources/skill-bundles"},
    )
    _write_skill(plugin / "resources/skill-bundles", "s" * 59)

    validate_plugin_checkout("demo-plugin", plugin)

    with pytest.raises(
        InvalidPluginPackageError,
        match="Plugin Skill bundle 无效",
    ):
        validate_plugin_checkout(
            "demo-plugin",
            plugin,
            resource_prefix="installed-prefix",
        )
