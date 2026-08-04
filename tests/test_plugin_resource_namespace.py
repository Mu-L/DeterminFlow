from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from src.extension_api import CoreRuntime, ExtensionManifest
from src.extension_api.registrar import OwnedPath
from src.extension_host.manager import ExtensionManager
from src.extension_host.resource_ids import build_resource_id_plan
from src.extension_host.resource_ids import ResourceIdResolver
from src.extension_host.resource_preparation import (
    _atomic_replace_directory,
    prepare_plugin_resources,
)
from src.extension_host.resources import LayeredJsonConfig
from src.extension_host.workflow_provisioning import (
    provision_plugin_workflows,
)
from src.plugin_system import PluginStore
from src.skills.config_manager import SkillConfigManager
from src.skills.manager import SkillManager


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )


def test_prepares_namespaced_resources_without_modifying_plugin_source(
    tmp_path: Path,
) -> None:
    plugin_root = tmp_path / "plugin"
    resources = plugin_root / "resources"
    agents = resources / "agents.json"
    prompts = resources / "prompts.json"
    skills = resources / "skills.json"
    rules = resources / "rules.json"
    phrases = resources / "phrases.json"
    _write_json(
        agents,
        {
            "agents": {
                "writer": {
                    "prompt_template": "writer",
                    "visible_skill_group_ids": ["writers", "default"],
                    "visible_rule_group_ids": ["safe"],
                }
            }
        },
    )
    _write_json(prompts, {"agents": {"writer": {"sections": []}}})
    _write_json(
        skills,
        {
            "skills": {
                "craft": {
                    "group_ids": ["writers", "default"],
                    "auto_inject": True,
                }
            },
            "skill_configs": {
                "craft": {
                    "enabled": True,
                    "priority": 70,
                    "auto_inject": True,
                    "workflow_only": False,
                }
            },
            "groups": [{"id": "writers", "skill_ids": ["craft"]}],
        },
    )
    _write_json(
        rules,
        {
            "rules": {"safety": {"group_ids": ["safe"]}},
            "rule_configs": {
                "safety": {
                    "group_ids": ["safe"],
                    "agent_types": ["writer", "main"],
                }
            },
            "groups": [{"id": "safe", "rule_ids": ["safety"]}],
        },
    )
    _write_json(
        phrases,
        {"phrases": [{"id": "continue", "label": "Continue"}]},
    )

    skill_bundle = resources / "skill-bundles" / "craft"
    skill_bundle.mkdir(parents=True)
    (skill_bundle / "SKILL.md").write_text(
        "---\n"
        "name: craft\n"
        "description: Writing craft\n"
        "metadata:\n"
        "  agent_types: [writer, main]\n"
        "---\n"
        "Use the craft.\n",
        encoding="utf-8",
    )
    rule_bundle = resources / "rule-bundles" / "safety"
    rule_bundle.mkdir(parents=True)
    (rule_bundle / "RULE.md").write_text(
        "---\nname: Safety\ndescription: Safe writing\n---\nStay safe.\n",
        encoding="utf-8",
    )

    workflow = resources / "workflows" / "build"
    workflow.mkdir(parents=True)
    _write_json(
        workflow / "definition.json",
        {
            "workflow_id": "build",
            "name": "Build",
            "version": 1,
            "nodes": [
                {
                    "id": "writer-node",
                    "agent_type": "writer",
                    "node_params": {"script_group": "scripts"},
                    "sub_workflow_params": {},
                },
                {
                    "id": "core-node",
                    "agent_type": "default",
                    "node_params": {},
                    "sub_workflow_params": {},
                },
            ],
            "edges": [],
            "gateways": [],
            "variables": [],
            "execution_schemes": [],
        },
    )
    script = resources / "script-library" / "scripts" / "prepare"
    script.mkdir(parents=True)
    (script / "prepare.py").write_text("VALUE = 1\n", encoding="utf-8")

    paths = {
        "agents": [OwnedPath("demo-plugin", agents)],
        "prompts": [OwnedPath("demo-plugin", prompts)],
        "skills": [OwnedPath("demo-plugin", skills)],
        "rules": [OwnedPath("demo-plugin", rules)],
        "preset_phrases": [OwnedPath("demo-plugin", phrases)],
        "skill_bundles": [
            OwnedPath("demo-plugin", resources / "skill-bundles")
        ],
        "rule_bundles": [
            OwnedPath("demo-plugin", resources / "rule-bundles")
        ],
        "workflows": [OwnedPath("demo-plugin", resources / "workflows")],
        "script_libraries": [
            OwnedPath("demo-plugin", resources / "script-library")
        ],
    }
    resolver = ResourceIdResolver()
    prepared = prepare_plugin_resources(
        ExtensionManifest(
            extension_id="demo-plugin",
            name="Demo Plugin",
            version="1.0.0",
            resource_prefix="demo",
        ),
        paths,
        runtime_root=tmp_path / "runtime",
        resolver=resolver,
        revision="abc123:sha256",
    )

    prepared_agents = json.loads(
        prepared.paths["agents"][0].path.read_text(encoding="utf-8")
    )
    assert set(prepared_agents["agents"]) == {"demo-writer"}
    assert prepared_agents["agents"]["demo-writer"]["prompt_template"] == (
        "demo-writer"
    )
    assert prepared_agents["agents"]["demo-writer"][
        "visible_skill_group_ids"
    ] == ["demo-writers", "default"]
    assert prepared_agents["agents"]["demo-writer"][
        "visible_rule_group_ids"
    ] == ["demo-safe"]

    prepared_workflow = (
        prepared.paths["workflows"][0].path
        / "demo-build"
        / "definition.json"
    )
    workflow_document = json.loads(
        prepared_workflow.read_text(encoding="utf-8")
    )
    assert workflow_document["workflow_id"] == "demo-build"
    assert workflow_document["nodes"][0]["id"] == "writer-node"
    assert workflow_document["nodes"][0]["agent_type"] == "demo-writer"
    assert workflow_document["nodes"][0]["node_params"]["script_group"] == (
        "demo-scripts"
    )
    assert workflow_document["nodes"][1]["agent_type"] == "default"

    prepared_skill = (
        prepared.paths["skill_bundles"][0].path
        / "demo-craft"
        / "SKILL.md"
    )
    assert "name: demo-craft" in prepared_skill.read_text(encoding="utf-8")
    assert (
        prepared.paths["rule_bundles"][0].path
        / "demo-safety"
        / "RULE.md"
    ).is_file()
    assert (
        prepared.paths["script_libraries"][0].path / "demo-scripts"
    ).is_dir()
    assert resolver.resolve("demo-plugin", "workflow", "build") == "demo-build"
    assert prepared.paths["script_libraries"][0].revision == "abc123:sha256"

    prepared_skills = json.loads(
        prepared.paths["skills"][0].path.read_text(encoding="utf-8")
    )
    assert prepared_skills["skills"]["demo-craft"] == {
        "group_ids": ["demo-writers", "default"],
        "auto_inject": True,
    }
    assert prepared_skills["skill_configs"]["demo-craft"]["auto_inject"] is True

    base_skills = tmp_path / "config" / "skills_config.json"
    _write_json(
        base_skills,
        {
            "skills": {},
            "skill_configs": {},
            "groups": [{"id": "default", "name": "Default"}],
        },
    )
    config_store = LayeredJsonConfig(
        base_skills,
        prepared.paths["skills"],
        dict_sections=("skills", "skill_configs"),
        list_sections=("groups",),
    )
    skill_manager = SkillManager(
        tmp_path / "user-skills",
        SkillConfigManager(base_skills, config_store=config_store),
        resource_roots=prepared.paths["skill_bundles"],
    )
    injected = skill_manager.list_by_agent_type(
        "main",
        auto_inject_only=True,
        visible_skill_group_ids=["default"],
    )
    assert [skill.id for skill in injected] == ["demo-craft"]

    assert json.loads(agents.read_text(encoding="utf-8"))["agents"] == {
        "writer": {
            "prompt_template": "writer",
            "visible_skill_group_ids": ["writers", "default"],
            "visible_rule_group_ids": ["safe"],
        }
    }


def test_core_runtime_resolves_own_and_cross_plugin_resource_ids() -> None:
    resolver = ResourceIdResolver()
    resolver.register(
        build_resource_id_plan(
            "novel-workflows",
            "novel",
            {"workflows": ["build"]},
        )
    )
    resolver.register(
        build_resource_id_plan(
            "novel-api",
            "novel-api",
            {"prompts": ["summary"]},
        )
    )
    runtime = CoreRuntime(
        app=object(),
        session_manager=object(),
        workflow_runtime=object(),
        tool_registry=object(),
        event_publisher=None,
        resource_owner="novel-api",
        resource_dependencies=("novel-workflows",),
        resource_resolver=resolver,
    )

    assert runtime.resolve_resource("prompt", "summary") == (
        "novel-api-summary"
    )
    assert runtime.resolve_resource(
        "workflow",
        "build",
        plugin_id="novel-workflows",
    ) == "novel-build"

    undeclared = CoreRuntime(
        app=object(),
        session_manager=object(),
        workflow_runtime=object(),
        tool_registry=object(),
        event_publisher=None,
        resource_owner="novel-api",
        resource_resolver=resolver,
    )
    with pytest.raises(RuntimeError, match="未声明资源依赖"):
        undeclared.resolve_resource(
            "workflow",
            "build",
            plugin_id="novel-workflows",
        )


def test_installed_prefix_override_drives_runtime_projection(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.name", "Plugin Test")
    _git(repo, "config", "user.email", "plugin-test@example.invalid")
    (repo / "extension.toml").write_text(
        """
[extension]
id = "demo-plugin"
name = "Demo"
version = "1.0.0"

[resource_namespace]
prefix = "developer-default"

[resources]
agents = "resources/agents.json"
""",
        encoding="utf-8",
    )
    _write_json(
        repo / "resources" / "agents.json",
        {"agents": {"writer": {"model": "test"}}},
    )
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "initial")

    store = PluginStore(
        tmp_path / "plugins",
        official_sources=[str(repo)],
    )
    store.install(
        "demo-plugin",
        str(repo),
        resource_prefix="installed-prefix",
    )
    manager = ExtensionManager(
        tmp_path / "core",
        plugin_store=store,
        enabled=["demo-plugin"],
        discover_entry_points=False,
    )

    document = json.loads(
        manager.resource_paths("agents")[0].path.read_text(encoding="utf-8")
    )
    assert set(document["agents"]) == {"installed-prefix-writer"}
    assert manager.resource_resolver.resolve(
        "demo-plugin",
        "agent",
        "writer",
    ) == "installed-prefix-writer"
    assert manager.get_statuses()[0]["resource_prefix"] == "installed-prefix"


def test_runtime_projection_restores_previous_directory_if_swap_fails(
    tmp_path: Path,
    monkeypatch,
) -> None:
    staging = tmp_path / "staging"
    destination = tmp_path / "runtime" / "demo-plugin"
    staging.mkdir(parents=True)
    destination.mkdir(parents=True)
    (staging / "new.txt").write_text("new", encoding="utf-8")
    (destination / "old.txt").write_text("old", encoding="utf-8")
    real_replace = os.replace

    def fail_staging_swap(source, target) -> None:
        if Path(source) == staging:
            raise OSError("simulated swap failure")
        real_replace(source, target)

    monkeypatch.setattr(os, "replace", fail_staging_swap)

    with pytest.raises(OSError, match="simulated swap failure"):
        _atomic_replace_directory(staging, destination)

    assert (destination / "old.txt").read_text(encoding="utf-8") == "old"
    assert not destination.with_name(".demo-plugin.previous").exists()


def test_workflow_namespace_upgrade_keeps_legacy_history_inactive(
    tmp_path: Path,
) -> None:
    target_root = tmp_path / "data" / "workflows"
    legacy = target_root / "wf-nvl-build"
    legacy_task = legacy / "tasks" / "task-existing.json"
    _write_json(
        legacy / "definition.json",
        {"workflow_id": "wf-nvl-build", "name": "Legacy Build"},
    )
    _write_json(legacy_task, {"task_id": "task-existing"})
    _write_json(
        legacy / ".extension.json",
        {"owner": "novel-workflows", "active": True, "files": {}},
    )

    source_root = tmp_path / "runtime-workflows"
    _write_json(
        source_root / "novel-build" / "definition.json",
        {"workflow_id": "novel-build", "name": "Namespaced Build"},
    )

    provision_plugin_workflows(
        [OwnedPath("novel-workflows", source_root, "revision")],
        target_root,
        active_owners={"novel-workflows"},
    )

    legacy_marker = json.loads(
        (legacy / ".extension.json").read_text(encoding="utf-8")
    )
    current_marker = json.loads(
        (
            target_root
            / "novel-build"
            / ".extension.json"
        ).read_text(encoding="utf-8")
    )
    assert legacy_marker["active"] is False
    assert legacy_task.is_file()
    assert current_marker["active"] is True
    assert current_marker["owner"] == "novel-workflows"
