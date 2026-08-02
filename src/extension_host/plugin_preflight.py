"""Side-effect-free validation before a Plugin revision becomes desired state."""

from __future__ import annotations

import json
import tempfile
from dataclasses import replace
from pathlib import Path
from typing import Any

from src.extension_api.models import EXTENSION_API_VERSION, ExtensionManifest
from src.extension_api.registrar import (
    ExtensionContributions,
    ExtensionRegistrar,
    OwnedPath,
)
from src.plugin_system import (
    InvalidPluginPackageError,
    validate_resource_prefix,
)
from src.rules.loader import RuleLoader
from src.skills.loader import SkillLoader
from src.workflow.definition import WorkflowDef
from src.workflow.script_library import ScriptLibraryCatalog

from .manifest import parse_extension_manifest
from .plugin_config import load_settings_schema
from .resource_ids import ResourceIdResolver
from .resource_preparation import prepare_plugin_resources
from .resources import LayeredJsonConfig


_JSON_RESOURCE_SCHEMAS = {
    "agents": (("agents",), ()),
    "prompts": (("agents",), ()),
    "skills": (("skills", "skill_configs"), ("groups",)),
    "rules": (("rules", "rule_configs"), ("groups",)),
    "preset_phrases": ((), ("phrases",)),
}


def _require_file(root: Path, relative_path: str, label: str) -> Path:
    path = (root / relative_path).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise InvalidPluginPackageError(
            f"{label} 必须位于 Plugin 目录内"
        ) from exc
    if not path.is_file():
        raise InvalidPluginPackageError(f"{label} 不存在: {relative_path}")
    return path


def _resource_source_path(
    root: Path,
    configured_path: str,
    resource_type: str,
) -> Path:
    """Resolve one declared resource while rejecting every symlink boundary."""
    relative = Path(configured_path)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError(
            f"Plugin 资源路径必须位于 Plugin 目录内: "
            f"{resource_type}: {configured_path}"
        )

    lexical = root / relative
    cursor = root
    for part in relative.parts:
        if part in {"", "."}:
            continue
        cursor /= part
        if cursor.is_symlink():
            raise ValueError(
                f"Plugin 资源不允许使用 symlink: "
                f"{resource_type}: {configured_path}"
            )

    resolved = lexical.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError(
            f"Plugin 资源路径必须位于 Plugin 目录内: "
            f"{resource_type}: {configured_path}"
        ) from exc
    if not resolved.exists():
        raise FileNotFoundError(
            f"Plugin 资源不存在: {resource_type}: {configured_path}"
        )

    if resolved.is_dir():
        for descendant in resolved.rglob("*"):
            if descendant.is_symlink():
                raise ValueError(
                    f"Plugin 资源不允许使用 symlink: "
                    f"{resource_type}: "
                    f"{descendant.relative_to(root)}"
                )
    return resolved


def _load_json_document(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        document = json.load(handle)
    if not isinstance(document, dict):
        raise ValueError(f"Plugin JSON 资源顶层必须是对象: {path}")
    return document


def _validate_json_item_shapes(
    resource_type: str,
    paths: list[OwnedPath],
) -> None:
    """Reject values that would only fail later in a config manager."""
    dict_sections, list_sections = _JSON_RESOURCE_SCHEMAS[resource_type]
    for owned_path in paths:
        document = _load_json_document(owned_path.path)
        for section in dict_sections:
            values = document.get(section, {})
            if not isinstance(values, dict):
                raise ValueError(
                    f"{owned_path.path}: {section} 必须是对象"
                )
            for item_id, value in values.items():
                if not str(item_id).strip():
                    raise ValueError(
                        f"{owned_path.path}: {section} 包含空 ID"
                    )
                if not isinstance(value, dict):
                    raise ValueError(
                        f"{owned_path.path}: {section}.{item_id} 必须是对象"
                    )
        for section in list_sections:
            values = document.get(section, [])
            if values is None:
                values = []
            if not isinstance(values, list):
                raise ValueError(
                    f"{owned_path.path}: {section} 必须是数组"
                )
            seen: set[str] = set()
            for value in values:
                if not isinstance(value, dict):
                    raise ValueError(
                        f"{owned_path.path}: {section} 项必须是对象"
                    )
                item_id = str(value.get("id", "")).strip()
                if not item_id:
                    raise ValueError(
                        f"{owned_path.path}: {section} 项必须包含非空 id"
                    )
                if item_id in seen:
                    raise ValueError(
                        f"{owned_path.path}: {section} 包含重复 ID: "
                        f"{item_id}"
                    )
                seen.add(item_id)


def _validate_json_resources(
    contributions: ExtensionContributions,
    scratch_root: Path,
) -> None:
    for resource_type, (dict_sections, list_sections) in (
        _JSON_RESOURCE_SCHEMAS.items()
    ):
        paths = contributions.resource_paths.get(resource_type, [])
        if not paths:
            continue
        _validate_json_item_shapes(resource_type, paths)
        store = LayeredJsonConfig(
            scratch_root / f"{resource_type}.json",
            paths,
            dict_sections=dict_sections,
            list_sections=list_sections,
        )
        store.validate_sources()


def _validate_workflow_resources(
    contributions: ExtensionContributions,
) -> None:
    claimed: dict[str, Path] = {}
    for owned_root in contributions.resource_paths.get("workflows", []):
        if not owned_root.path.is_dir():
            raise ValueError(
                f"Plugin Workflow 资源必须是目录: {owned_root.path}"
            )
        for workflow_dir in sorted(owned_root.path.iterdir()):
            definition_path = workflow_dir / "definition.json"
            if not workflow_dir.is_dir() or not definition_path.is_file():
                continue
            document = _load_json_document(definition_path)
            workflow = WorkflowDef.from_dict(document)
            if workflow.workflow_id != workflow_dir.name:
                raise ValueError(
                    "Workflow definition ID 必须与目录名一致: "
                    f"{workflow.workflow_id} != {workflow_dir.name}"
                )
            previous = claimed.get(workflow.workflow_id)
            if previous is not None:
                raise ValueError(
                    f"Plugin Workflow ID 重复: {workflow.workflow_id} "
                    f"({previous} vs {definition_path})"
                )
            errors = workflow.auto_pair_gateways() + workflow.validate()
            if errors:
                raise ValueError(
                    f"Workflow definition 校验失败: {definition_path}: "
                    + "；".join(errors)
                )
            claimed[workflow.workflow_id] = definition_path


def _validate_bundle_resources(
    contributions: ExtensionContributions,
    scratch_root: Path,
) -> None:
    skill_roots = contributions.resource_paths.get("skill_bundles", [])
    if skill_roots:
        skill_loader = SkillLoader(
            scratch_root / "user-skills",
            resource_roots=skill_roots,
        )
        skill_loader.validate_sources()
        for skill in skill_loader.load_all(include_inactive=True):
            skill_dir = Path(str(skill.metadata.get("skill_dir", "")))
            if skill_dir.name != skill.id:
                raise ValueError(
                    f"Plugin Skill name 必须与目录名一致: "
                    f"{skill.id} != {skill_dir.name}"
                )

    rule_roots = contributions.resource_paths.get("rule_bundles", [])
    if rule_roots:
        RuleLoader(
            scratch_root / "user-rules",
            resource_roots=rule_roots,
        ).validate_sources()


def _validate_script_resources(
    contributions: ExtensionContributions,
    scratch_root: Path,
) -> None:
    roots = contributions.resource_paths.get("script_libraries", [])
    if not roots:
        return
    catalog = ScriptLibraryCatalog(
        scratch_root / "user-scripts",
        extension_roots=roots,
    )
    catalog.validate_sources()
    for script in catalog.list_scripts():
        if catalog.resolve(script["group"], script["name"]) is None:
            raise ValueError(
                f"Plugin Script Library 无法解析: "
                f"{script['group']}/{script['name']}"
            )


def _validate_declared_resources(
    manifest: ExtensionManifest,
    contributions: ExtensionContributions,
) -> None:
    with tempfile.TemporaryDirectory(prefix="determinflow-plugin-preflight-") as temp:
        scratch_root = Path(temp)
        source_scratch = scratch_root / "source-validation"
        _validate_json_resources(contributions, source_scratch)
        _validate_workflow_resources(contributions)
        _validate_bundle_resources(contributions, source_scratch)
        _validate_script_resources(contributions, source_scratch)
        prepared = prepare_plugin_resources(
            manifest,
            contributions.resource_paths,
            runtime_root=scratch_root / "runtime",
            resolver=ResourceIdResolver(),
        )
        effective = ExtensionContributions(
            resource_paths=prepared.paths,
        )
        effective_scratch = scratch_root / "effective-validation"
        _validate_json_resources(effective, effective_scratch)
        _validate_workflow_resources(effective)
        _validate_bundle_resources(effective, effective_scratch)
        _validate_script_resources(effective, effective_scratch)


def validate_plugin_checkout(
    plugin_id: str,
    checkout: Path,
    *,
    resource_prefix: str | None = None,
) -> ExtensionManifest:
    """Validate all declarative package surfaces without importing Plugin code."""
    root = Path(checkout).resolve()
    try:
        manifest = parse_extension_manifest(root / "extension.toml")
        if resource_prefix is not None:
            manifest = replace(
                manifest,
                resource_prefix=validate_resource_prefix(
                    resource_prefix,
                    allow_empty=True,
                ),
            )
        if manifest.extension_id != plugin_id:
            raise ValueError(
                f"manifest plugin id does not match requested id: {plugin_id}"
            )
        if manifest.api_version != EXTENSION_API_VERSION:
            raise ValueError(
                f"Plugin API 版本不兼容: "
                f"{manifest.api_version} != {EXTENSION_API_VERSION}"
            )
        if manifest.requirements:
            _require_file(root, manifest.requirements, "Plugin requirements")
        if manifest.settings_schema:
            load_settings_schema(root, manifest.settings_schema)
        if manifest.page is not None:
            static_root = (root / manifest.page.static_dir).resolve()
            static_root.relative_to(root)
            if not static_root.is_dir():
                raise ValueError(
                    f"Plugin 静态页面目录不存在: {manifest.page.static_dir}"
                )
            entrypoint = (static_root / manifest.page.entrypoint).resolve()
            entrypoint.relative_to(static_root)
            if not entrypoint.is_file():
                raise ValueError(
                    f"Plugin 静态页面入口不存在: {manifest.page.entrypoint}"
                )
        contributions = ExtensionContributions()
        registrar = ExtensionRegistrar(manifest, contributions)
        for resource_type, configured in manifest.resources.items():
            paths = configured if isinstance(configured, list) else [configured]
            for path in paths:
                _resource_source_path(root, path, resource_type)
                registrar.add_resource_path(resource_type, path)
        _validate_declared_resources(manifest, contributions)
        for process in manifest.processes:
            working_directory = (root / process.working_directory).resolve()
            working_directory.relative_to(root)
            if not working_directory.is_dir():
                raise ValueError(
                    f"Plugin 进程工作目录不存在: {process.working_directory}"
                )
    except InvalidPluginPackageError:
        raise
    except Exception as exc:
        raise InvalidPluginPackageError(
            f"Plugin 清单预检失败: {exc}"
        ) from exc
    return manifest
