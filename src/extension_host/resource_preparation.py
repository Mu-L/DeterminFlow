"""Cold-start preparation of namespaced Plugin resources.

Plugin source files always keep developer-local IDs. The Host builds an
immutable runtime projection with effective IDs, so Plugin authors do not need
to rewrite internal references and users only see the installed prefix.
"""

from __future__ import annotations

import copy
import json
import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import yaml

from src.extension_api.models import ExtensionManifest
from src.extension_api.registrar import OwnedPath

from .resource_ids import ResourceIdPlan, ResourceIdResolver, build_resource_id_plan


_JSON_RESOURCE_TYPES = frozenset({
    "agents",
    "prompts",
    "skills",
    "rules",
    "preset_phrases",
})
_BUNDLE_RESOURCE_TYPES = frozenset({"skill_bundles", "rule_bundles"})
_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n(.*)$", re.DOTALL)


@dataclass(frozen=True)
class PreparedPluginResources:
    """Runtime resource roots and their explicit resource-ID plan."""

    paths: dict[str, list[OwnedPath]]
    plan: ResourceIdPlan


def _read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        document = json.load(handle)
    if not isinstance(document, dict):
        raise ValueError(f"Plugin 资源顶层必须是对象: {path}")
    return document


def _resource_paths(
    resource_paths: dict[str, list[OwnedPath]],
    resource_type: str,
) -> list[OwnedPath]:
    return list(resource_paths.get(resource_type, ()))


def _dict_ids(document: dict[str, Any], section: str, path: Path) -> set[str]:
    values = document.get(section, {})
    if not isinstance(values, dict):
        raise ValueError(f"{path}: {section} 必须是对象")
    return {str(item_id) for item_id in values}


def _list_ids(document: dict[str, Any], section: str, path: Path) -> set[str]:
    values = document.get(section, [])
    if values is None:
        return set()
    if not isinstance(values, list):
        raise ValueError(f"{path}: {section} 必须是数组")
    result: set[str] = set()
    for value in values:
        if not isinstance(value, dict) or not str(value.get("id", "")).strip():
            raise ValueError(f"{path}: {section} 项必须包含非空 id")
        result.add(str(value["id"]))
    return result


def _bundle_ids(root: Path, filename: str) -> set[str]:
    if not root.is_dir():
        raise ValueError(f"Plugin Bundle 资源必须是目录: {root}")
    return {
        child.name
        for child in root.iterdir()
        if child.is_dir() and (child / filename).is_file()
    }


def _workflow_ids(root: Path) -> set[str]:
    if not root.is_dir():
        raise ValueError(f"Plugin Workflow 资源必须是目录: {root}")
    result: set[str] = set()
    for child in root.iterdir():
        definition = child / "definition.json"
        if not child.is_dir() or not definition.is_file():
            continue
        document = _read_json(definition)
        workflow_id = str(document.get("workflow_id", "")).strip()
        if workflow_id != child.name:
            raise ValueError(
                "Workflow definition ID 必须与目录名一致: "
                f"{workflow_id} != {child.name}"
            )
        result.add(child.name)
    return result


def _script_group_ids(root: Path) -> set[str]:
    if not root.is_dir():
        raise ValueError(f"Plugin Script Library 资源必须是目录: {root}")
    return {
        child.name
        for child in root.iterdir()
        if child.is_dir() and not child.name.startswith(".")
    }


def build_plugin_resource_plan(
    manifest: ExtensionManifest,
    resource_paths: dict[str, list[OwnedPath]],
) -> ResourceIdPlan:
    """Inspect declared resources and build one explicit local/effective map."""
    ids: dict[str, set[str]] = {
        "agent": set(),
        "prompt": set(),
        "skill": set(),
        "skill_group": set(),
        "rule": set(),
        "rule_group": set(),
        "preset_phrase": set(),
        "workflow": set(),
        "script_library": set(),
    }
    for owned_path in _resource_paths(resource_paths, "agents"):
        ids["agent"].update(
            _dict_ids(_read_json(owned_path.path), "agents", owned_path.path)
        )
    for owned_path in _resource_paths(resource_paths, "prompts"):
        ids["prompt"].update(
            _dict_ids(_read_json(owned_path.path), "agents", owned_path.path)
        )
    for owned_path in _resource_paths(resource_paths, "skills"):
        document = _read_json(owned_path.path)
        ids["skill"].update(_dict_ids(document, "skills", owned_path.path))
        ids["skill"].update(
            _dict_ids(document, "skill_configs", owned_path.path)
        )
        ids["skill_group"].update(
            _list_ids(document, "groups", owned_path.path)
        )
    for owned_path in _resource_paths(resource_paths, "rules"):
        document = _read_json(owned_path.path)
        ids["rule"].update(_dict_ids(document, "rules", owned_path.path))
        ids["rule"].update(
            _dict_ids(document, "rule_configs", owned_path.path)
        )
        ids["rule_group"].update(
            _list_ids(document, "groups", owned_path.path)
        )
    for owned_path in _resource_paths(resource_paths, "preset_phrases"):
        ids["preset_phrase"].update(
            _list_ids(_read_json(owned_path.path), "phrases", owned_path.path)
        )
    for owned_path in _resource_paths(resource_paths, "workflows"):
        ids["workflow"].update(_workflow_ids(owned_path.path))
    for owned_path in _resource_paths(resource_paths, "script_libraries"):
        ids["script_library"].update(_script_group_ids(owned_path.path))
    for owned_path in _resource_paths(resource_paths, "skill_bundles"):
        ids["skill"].update(_bundle_ids(owned_path.path, "SKILL.md"))
    for owned_path in _resource_paths(resource_paths, "rule_bundles"):
        ids["rule"].update(_bundle_ids(owned_path.path, "RULE.md"))
    return build_resource_id_plan(
        manifest.extension_id,
        manifest.resource_prefix,
        ids,
    )


def _maybe_resolve(
    resolver: ResourceIdResolver,
    owner: str,
    resource_type: str,
    local_id: Any,
) -> Any:
    if not isinstance(local_id, str) or not local_id:
        return local_id
    try:
        return resolver.resolve(owner, resource_type, local_id)
    except KeyError:
        return local_id


def _rewrite_id_mapping(
    values: Any,
    *,
    resolver: ResourceIdResolver,
    owner: str,
    resource_type: str,
) -> dict[str, Any]:
    if not isinstance(values, dict):
        raise ValueError("Plugin 映射资源必须是对象")
    return {
        resolver.resolve(owner, resource_type, str(local_id)): copy.deepcopy(value)
        for local_id, value in values.items()
    }


def _rewrite_id_list(
    values: Any,
    *,
    resolver: ResourceIdResolver,
    owner: str,
    resource_type: str,
) -> list[Any]:
    if not isinstance(values, list):
        return []
    return [
        _maybe_resolve(resolver, owner, resource_type, value)
        for value in values
    ]


def _rewrite_groups(
    values: Any,
    *,
    resolver: ResourceIdResolver,
    owner: str,
    group_type: str,
    member_field: str,
    member_type: str,
) -> list[dict[str, Any]]:
    if values is None:
        return []
    if not isinstance(values, list):
        raise ValueError("Plugin group 资源必须是数组")
    result: list[dict[str, Any]] = []
    for raw in values:
        if not isinstance(raw, dict):
            raise ValueError("Plugin group 项必须是对象")
        value = copy.deepcopy(raw)
        value["id"] = resolver.resolve(owner, group_type, str(value["id"]))
        if member_field in value:
            value[member_field] = _rewrite_id_list(
                value[member_field],
                resolver=resolver,
                owner=owner,
                resource_type=member_type,
            )
        result.append(value)
    return result


def rewrite_json_resource(
    resource_type: str,
    document: dict[str, Any],
    *,
    resolver: ResourceIdResolver,
    owner: str,
) -> dict[str, Any]:
    """Rewrite only known structured ID fields, never free-form content."""
    result = copy.deepcopy(document)
    if resource_type == "agents":
        result["agents"] = _rewrite_id_mapping(
            result.get("agents", {}),
            resolver=resolver,
            owner=owner,
            resource_type="agent",
        )
        for definition in result["agents"].values():
            if not isinstance(definition, dict):
                continue
            definition["prompt_template"] = _maybe_resolve(
                resolver,
                owner,
                "prompt",
                definition.get("prompt_template"),
            )
            for field_name, group_type in (
                ("visible_skill_group_ids", "skill_group"),
                ("visible_rule_group_ids", "rule_group"),
            ):
                if field_name in definition:
                    definition[field_name] = _rewrite_id_list(
                        definition[field_name],
                        resolver=resolver,
                        owner=owner,
                        resource_type=group_type,
                    )
        return result
    if resource_type == "prompts":
        result["agents"] = _rewrite_id_mapping(
            result.get("agents", {}),
            resolver=resolver,
            owner=owner,
            resource_type="prompt",
        )
        return result
    if resource_type == "skills":
        for section in ("skills", "skill_configs"):
            result[section] = _rewrite_id_mapping(
                result.get(section, {}),
                resolver=resolver,
                owner=owner,
                resource_type="skill",
            )
        for section in ("skills", "skill_configs"):
            for config in result[section].values():
                if isinstance(config, dict) and "group_ids" in config:
                    config["group_ids"] = _rewrite_id_list(
                        config["group_ids"],
                        resolver=resolver,
                        owner=owner,
                        resource_type="skill_group",
                    )
        result["groups"] = _rewrite_groups(
            result.get("groups", []),
            resolver=resolver,
            owner=owner,
            group_type="skill_group",
            member_field="skill_ids",
            member_type="skill",
        )
        return result
    if resource_type == "rules":
        for section in ("rules", "rule_configs"):
            result[section] = _rewrite_id_mapping(
                result.get(section, {}),
                resolver=resolver,
                owner=owner,
                resource_type="rule",
            )
        for section in ("rules", "rule_configs"):
            for config in result[section].values():
                if not isinstance(config, dict):
                    continue
                if "group_ids" in config:
                    config["group_ids"] = _rewrite_id_list(
                        config["group_ids"],
                        resolver=resolver,
                        owner=owner,
                        resource_type="rule_group",
                    )
                if "agent_types" in config:
                    config["agent_types"] = _rewrite_id_list(
                        config["agent_types"],
                        resolver=resolver,
                        owner=owner,
                        resource_type="agent",
                    )
        result["groups"] = _rewrite_groups(
            result.get("groups", []),
            resolver=resolver,
            owner=owner,
            group_type="rule_group",
            member_field="rule_ids",
            member_type="rule",
        )
        return result
    if resource_type == "preset_phrases":
        phrases = result.get("phrases", [])
        if not isinstance(phrases, list):
            raise ValueError("Plugin phrases 资源必须是数组")
        for phrase in phrases:
            if not isinstance(phrase, dict):
                raise ValueError("Plugin phrase 项必须是对象")
            phrase["id"] = resolver.resolve(
                owner,
                "preset_phrase",
                str(phrase["id"]),
            )
        return result
    raise ValueError(f"不支持的 JSON Plugin 资源类型: {resource_type}")


def rewrite_workflow_definition(
    document: dict[str, Any],
    *,
    resolver: ResourceIdResolver,
    owner: str,
) -> dict[str, Any]:
    """Rewrite Workflow-global references while preserving node-local IDs."""
    result = copy.deepcopy(document)
    result["workflow_id"] = resolver.resolve(
        owner,
        "workflow",
        str(result["workflow_id"]),
    )
    nodes = result.get("nodes", [])
    if not isinstance(nodes, list):
        raise ValueError("Workflow nodes 必须是数组")
    for node in nodes:
        if not isinstance(node, dict):
            continue
        node["agent_type"] = _maybe_resolve(
            resolver,
            owner,
            "agent",
            node.get("agent_type"),
        )
        node_params = node.get("node_params")
        if isinstance(node_params, dict):
            if "script_group" in node_params:
                node_params["script_group"] = _maybe_resolve(
                    resolver,
                    owner,
                    "script_library",
                    node_params.get("script_group"),
                )
            for field_name in ("workflow_id", "sub_workflow_id"):
                if field_name in node_params:
                    node_params[field_name] = _maybe_resolve(
                        resolver,
                        owner,
                        "workflow",
                        node_params[field_name],
                    )
        sub_workflow = node.get("sub_workflow_params")
        if isinstance(sub_workflow, dict):
            for field_name in ("workflow_id", "sub_workflow_id"):
                if field_name in sub_workflow:
                    sub_workflow[field_name] = _maybe_resolve(
                        resolver,
                        owner,
                        "workflow",
                        sub_workflow[field_name],
                    )
    return result


def _rewrite_skill_frontmatter(
    path: Path,
    *,
    resolver: ResourceIdResolver,
    owner: str,
    local_id: str,
) -> None:
    content = path.read_text(encoding="utf-8")
    match = _FRONTMATTER_RE.match(content)
    if match is None:
        raise ValueError(f"SKILL.md 缺少 YAML frontmatter: {path}")
    frontmatter = yaml.safe_load(match.group(1))
    if not isinstance(frontmatter, dict):
        raise ValueError(f"SKILL.md frontmatter 必须是对象: {path}")
    declared_name = str(frontmatter.get("name", "")).strip()
    if declared_name != local_id:
        raise ValueError(
            f"SKILL.md name 必须与目录名一致: {declared_name} != {local_id}"
        )
    frontmatter["name"] = resolver.resolve(owner, "skill", local_id)
    metadata = frontmatter.get("metadata")
    if isinstance(metadata, dict) and "agent_types" in metadata:
        metadata["agent_types"] = _rewrite_id_list(
            metadata["agent_types"],
            resolver=resolver,
            owner=owner,
            resource_type="agent",
        )
    rendered = yaml.safe_dump(
        frontmatter,
        allow_unicode=True,
        sort_keys=False,
    )
    path.write_text(f"---\n{rendered}---\n{match.group(2)}", encoding="utf-8")


def _copy_bundle_root(
    source_root: Path,
    target_root: Path,
    *,
    filename: str,
    resource_type: str,
    resolver: ResourceIdResolver,
    owner: str,
) -> None:
    target_root.mkdir(parents=True, exist_ok=True)
    for source_dir in sorted(source_root.iterdir()):
        source_file = source_dir / filename
        if not source_dir.is_dir() or not source_file.is_file():
            continue
        effective_id = resolver.resolve(owner, resource_type, source_dir.name)
        target_dir = target_root / effective_id
        shutil.copytree(
            source_dir,
            target_dir,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"),
        )
        if filename == "SKILL.md":
            _rewrite_skill_frontmatter(
                target_dir / filename,
                resolver=resolver,
                owner=owner,
                local_id=source_dir.name,
            )


def _atomic_replace_directory(staging: Path, destination: Path) -> None:
    previous = destination.with_name(f".{destination.name}.previous")
    shutil.rmtree(previous, ignore_errors=True)
    if destination.exists():
        os.replace(destination, previous)
    try:
        os.replace(staging, destination)
    except Exception:
        if previous.exists() and not destination.exists():
            os.replace(previous, destination)
        raise
    shutil.rmtree(previous, ignore_errors=True)


def prepare_plugin_resources(
    manifest: ExtensionManifest,
    resource_paths: dict[str, list[OwnedPath]],
    *,
    runtime_root: Path,
    resolver: ResourceIdResolver,
    revision: str = "",
) -> PreparedPluginResources:
    """Build an atomic, namespaced runtime projection for one Plugin."""
    plan = build_plugin_resource_plan(manifest, resource_paths)
    resolver.register(plan)
    runtime_root = Path(runtime_root).resolve()
    runtime_root.mkdir(parents=True, exist_ok=True)
    staging = Path(
        tempfile.mkdtemp(
            prefix=f".{manifest.extension_id}.",
            dir=runtime_root,
        )
    )
    destination = runtime_root / manifest.extension_id
    prepared: dict[str, list[OwnedPath]] = {}
    try:
        for resource_type, owned_paths in resource_paths.items():
            for index, owned_path in enumerate(owned_paths):
                if owned_path.owner != manifest.extension_id:
                    raise ValueError(
                        f"Plugin 资源 owner 不匹配: "
                        f"{owned_path.owner} != {manifest.extension_id}"
                    )
                if resource_type in _JSON_RESOURCE_TYPES:
                    document = rewrite_json_resource(
                        resource_type,
                        _read_json(owned_path.path),
                        resolver=resolver,
                        owner=manifest.extension_id,
                    )
                    target = staging / "json" / resource_type / f"{index}.json"
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(
                        json.dumps(document, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8",
                    )
                elif resource_type == "workflows":
                    target = staging / "workflows" / str(index)
                    target.mkdir(parents=True, exist_ok=True)
                    for source_dir in sorted(owned_path.path.iterdir()):
                        definition = source_dir / "definition.json"
                        if not source_dir.is_dir() or not definition.is_file():
                            continue
                        effective_id = resolver.resolve(
                            manifest.extension_id,
                            "workflow",
                            source_dir.name,
                        )
                        target_dir = target / effective_id
                        shutil.copytree(
                            source_dir,
                            target_dir,
                            ignore=shutil.ignore_patterns(
                                "__pycache__",
                                "*.pyc",
                                "*.pyo",
                            ),
                        )
                        transformed = rewrite_workflow_definition(
                            _read_json(target_dir / "definition.json"),
                            resolver=resolver,
                            owner=manifest.extension_id,
                        )
                        (target_dir / "definition.json").write_text(
                            json.dumps(
                                transformed,
                                ensure_ascii=False,
                                indent=2,
                            )
                            + "\n",
                            encoding="utf-8",
                        )
                elif resource_type == "script_libraries":
                    target = staging / "script-libraries" / str(index)
                    target.mkdir(parents=True, exist_ok=True)
                    for source_group in sorted(owned_path.path.iterdir()):
                        if not source_group.is_dir() or source_group.name.startswith("."):
                            continue
                        effective_group = resolver.resolve(
                            manifest.extension_id,
                            "script_library",
                            source_group.name,
                        )
                        shutil.copytree(
                            source_group,
                            target / effective_group,
                            ignore=shutil.ignore_patterns(
                                "__pycache__",
                                "*.pyc",
                                "*.pyo",
                            ),
                        )
                elif resource_type in _BUNDLE_RESOURCE_TYPES:
                    target = staging / resource_type.replace("_", "-") / str(index)
                    _copy_bundle_root(
                        owned_path.path,
                        target,
                        filename=(
                            "SKILL.md"
                            if resource_type == "skill_bundles"
                            else "RULE.md"
                        ),
                        resource_type=(
                            "skill"
                            if resource_type == "skill_bundles"
                            else "rule"
                        ),
                        resolver=resolver,
                        owner=manifest.extension_id,
                    )
                else:
                    target = staging / "passthrough" / resource_type / str(index)
                    if owned_path.path.is_dir():
                        shutil.copytree(owned_path.path, target)
                    else:
                        target.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(owned_path.path, target)
                prepared.setdefault(resource_type, []).append(
                    OwnedPath(manifest.extension_id, target, revision)
                )
        _atomic_replace_directory(staging, destination)
        resolved_paths = {
            resource_type: [
                OwnedPath(
                    owned.owner,
                    destination / owned.path.relative_to(staging),
                    owned.revision,
                )
                for owned in owned_paths
            ]
            for resource_type, owned_paths in prepared.items()
        }
        return PreparedPluginResources(paths=resolved_paths, plan=plan)
    except Exception:
        resolver.unregister(manifest.extension_id)
        shutil.rmtree(staging, ignore_errors=True)
        raise
