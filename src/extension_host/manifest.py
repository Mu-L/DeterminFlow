"""Parsing and validation for Extension and Plugin Package manifests."""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

from src.extension_api.models import (
    EXTENSION_API_VERSION,
    ExtensionManifest,
    ExtensionPage,
    ExtensionProcess,
)
from src.plugin_system.models import validate_plugin_id, validate_resource_prefix

from .lifecycle import parse_extension_lifecycle


SUPPORTED_RESOURCE_TYPES = frozenset({
    "agents",
    "prompts",
    "skills",
    "skill_bundles",
    "rules",
    "rule_bundles",
    "preset_phrases",
    "workflows",
    "script_libraries",
})


def _string(
    table: dict[str, Any],
    field_name: str,
    *,
    default: str = "",
    required: bool = False,
    context: str,
) -> str:
    value = table.get(field_name, default)
    if not isinstance(value, str):
        raise ValueError(f"{context}.{field_name} 必须是字符串")
    value = value.strip()
    if required and not value:
        raise ValueError(f"缺少非空 {context}.{field_name}")
    return value


def _positive_number(
    table: dict[str, Any],
    field_name: str,
    default: float,
    *,
    context: str,
) -> float:
    value = table.get(field_name, default)
    if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
        raise ValueError(f"{context}.{field_name} 必须是正数")
    return float(value)


def _validate_relative_path(value: str, *, field_name: str) -> str:
    path = Path(value)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"{field_name} 必须是 Plugin 根目录内的相对路径")
    return path.as_posix()


def _parse_processes(data: Any) -> tuple[ExtensionProcess, ...]:
    if data is None:
        return ()
    if not isinstance(data, list):
        raise ValueError("[[processes]] 必须是 TOML table 数组")

    result: list[ExtensionProcess] = []
    seen: set[str] = set()
    for index, raw in enumerate(data):
        context = f"processes[{index}]"
        if not isinstance(raw, dict):
            raise ValueError(f"{context} 必须是 TOML table")
        process_id = _string(
            raw,
            "id",
            required=True,
            context=context,
        )
        if process_id in seen:
            raise ValueError(f"重复 process ID: {process_id}")
        seen.add(process_id)

        command = raw.get("command")
        if (
            not isinstance(command, list)
            or not command
            or any(not isinstance(item, str) or not item for item in command)
        ):
            raise ValueError(f"{context}.command 必须是非空字符串数组")

        environment = raw.get("environment", {})
        if not isinstance(environment, dict) or any(
            not isinstance(key, str) or not isinstance(value, str)
            for key, value in environment.items()
        ):
            raise ValueError(f"{context}.environment 必须是字符串映射")

        working_directory = _validate_relative_path(
            _string(raw, "working_directory", default=".", context=context),
            field_name=f"{context}.working_directory",
        )
        result.append(
            ExtensionProcess(
                process_id=process_id,
                command=tuple(command),
                working_directory=working_directory,
                environment=dict(environment),
                healthcheck_url=_string(
                    raw,
                    "healthcheck_url",
                    context=context,
                ),
                start_timeout_seconds=_positive_number(
                    raw,
                    "start_timeout_seconds",
                    30,
                    context=context,
                ),
                stop_timeout_seconds=_positive_number(
                    raw,
                    "stop_timeout_seconds",
                    10,
                    context=context,
                ),
            )
        )
    return tuple(result)


def parse_extension_manifest(manifest_path: Path) -> ExtensionManifest:
    """Parse one extension.toml into the stable runtime contract."""
    manifest_path = Path(manifest_path).resolve()
    with manifest_path.open("rb") as handle:
        data = tomllib.load(handle)

    extension_data = data.get("extension", {})
    if not isinstance(extension_data, dict):
        raise ValueError("[extension] 必须是 TOML table")

    try:
        extension_id = validate_plugin_id(
            _string(
                extension_data,
                "id",
                required=True,
                context="extension",
            )
        )
    except ValueError as exc:
        raise ValueError(
            "extension.id 必须是小写 kebab-case，且不超过 128 个字符"
        ) from exc
    string_values = {
        "name": _string(
            extension_data,
            "name",
            default=extension_id,
            context="extension",
        ),
        "version": _string(
            extension_data,
            "version",
            default="0.0.0",
            context="extension",
        ),
        "api_version": _string(
            extension_data,
            "api_version",
            default=EXTENSION_API_VERSION,
            context="extension",
        ),
        "description": _string(
            extension_data,
            "description",
            context="extension",
        ),
        "backend": _string(extension_data, "backend", context="extension"),
        "frontend": _string(extension_data, "frontend", context="extension"),
    }
    resource_namespace = data.get("resource_namespace", {})
    if not isinstance(resource_namespace, dict):
        raise ValueError("[resource_namespace] 必须是 TOML table")
    unknown_namespace_fields = sorted(set(resource_namespace) - {"prefix"})
    if unknown_namespace_fields:
        raise ValueError(
            "resource_namespace 包含不支持的字段: "
            + ", ".join(unknown_namespace_fields)
        )
    try:
        resource_prefix = validate_resource_prefix(
            _string(
                resource_namespace,
                "prefix",
                context="resource_namespace",
            )
        )
    except ValueError as exc:
        raise ValueError(
            "resource_namespace.prefix 必须是小写 kebab-case，"
            "且不超过 128 个字符"
        ) from exc

    sequence_values: dict[str, tuple[str, ...]] = {}
    for field_name in ("dependencies", "capabilities"):
        value = extension_data.get(field_name, [])
        if not isinstance(value, list) or any(
            not isinstance(item, str) or not item.strip() for item in value
        ):
            raise ValueError(f"extension.{field_name} 必须是非空字符串数组")
        normalized = tuple(item.strip() for item in value)
        if field_name == "dependencies":
            try:
                normalized = tuple(
                    validate_plugin_id(item) for item in normalized
                )
            except ValueError as exc:
                raise ValueError(
                    "extension.dependencies 必须是小写 kebab-case Plugin ID"
                ) from exc
            if len(set(normalized)) != len(normalized):
                raise ValueError("extension.dependencies 不能重复")
        sequence_values[field_name] = normalized

    resources = data.get("resources", {})
    if not isinstance(resources, dict):
        raise ValueError("[resources] 必须是 TOML table")
    unknown_resources = sorted(set(resources) - SUPPORTED_RESOURCE_TYPES)
    if unknown_resources:
        raise ValueError("未知资源类型: " + ", ".join(unknown_resources))
    for resource_type, configured in resources.items():
        paths = configured if isinstance(configured, list) else [configured]
        if not paths or any(
            not isinstance(path, str) or not path.strip() for path in paths
        ):
            raise ValueError(
                f"resources.{resource_type} 必须是非空路径或路径数组"
            )

    installation = data.get("installation", {})
    if not isinstance(installation, dict):
        raise ValueError("[installation] 必须是 TOML table")
    requirements = _string(
        installation,
        "requirements",
        context="installation",
    )
    if requirements:
        requirements = _validate_relative_path(
            requirements,
            field_name="installation.requirements",
        )

    settings = data.get("settings", {})
    if not isinstance(settings, dict):
        raise ValueError("[settings] 必须是 TOML table")
    settings_schema = _string(settings, "schema", context="settings")
    if settings_schema:
        settings_schema = _validate_relative_path(
            settings_schema,
            field_name="settings.schema",
        )

    parse_extension_lifecycle(data.get("lifecycle"))

    page_data = data.get("page")
    page = None
    if page_data is not None:
        if not isinstance(page_data, dict):
            raise ValueError("[page] 必须是 TOML table")
        static_dir = _validate_relative_path(
            _string(page_data, "static_dir", required=True, context="page"),
            field_name="page.static_dir",
        )
        entrypoint = _validate_relative_path(
            _string(
                page_data,
                "entrypoint",
                default="index.html",
                context="page",
            ),
            field_name="page.entrypoint",
        )
        page = ExtensionPage(
            label=_string(
                page_data,
                "label",
                default=string_values["name"],
                context="page",
            ),
            static_dir=static_dir,
            entrypoint=entrypoint,
        )

    return ExtensionManifest(
        extension_id=extension_id,
        name=string_values["name"],
        version=string_values["version"],
        api_version=string_values["api_version"],
        description=string_values["description"],
        resource_prefix=resource_prefix,
        dependencies=sequence_values["dependencies"],
        backend=string_values["backend"],
        frontend=string_values["frontend"],
        capabilities=sequence_values["capabilities"],
        base_path=manifest_path.parent,
        resources=dict(resources),
        requirements=requirements,
        settings_schema=settings_schema,
        page=page,
        processes=_parse_processes(data.get("processes")),
    )
