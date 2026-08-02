"""Schema-limited persistent settings for installed plugins."""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from collections.abc import Callable, Mapping
from copy import deepcopy
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from src.extension_api.models import ExtensionManifest


_PLUGIN_ID = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_ENV_NAME = re.compile(r"^[A-Z_][A-Z0-9_]*$")
_SUPPORTED_TYPES = frozenset({
    "object",
    "string",
    "number",
    "integer",
    "boolean",
    "array",
})
_SUPPORTED_FORMATS = frozenset({"password", "uri", "multiline"})
_SCHEMA_KEYS = frozenset({
    "$schema",
    "title",
    "type",
    "description",
    "properties",
    "required",
    "default",
    "enum",
    "minimum",
    "maximum",
    "format",
    "items",
})


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _validate_schema(schema: Any, path: str = "$") -> None:
    if not isinstance(schema, dict):
        raise ValueError(f"{path} Schema 必须是 object")
    unsupported = sorted(set(schema) - _SCHEMA_KEYS)
    if unsupported:
        raise ValueError(
            f"{path} 包含不支持的 Schema 关键字: {', '.join(unsupported)}"
        )
    schema_type = schema.get("type")
    if schema_type not in _SUPPORTED_TYPES:
        raise ValueError(f"{path}.type 不受支持: {schema_type!r}")
    if "enum" in schema:
        enum = schema["enum"]
        if not isinstance(enum, list) or not enum:
            raise ValueError(f"{path}.enum 必须是非空数组")
    schema_format = schema.get("format")
    if schema_format is not None and schema_format not in _SUPPORTED_FORMATS:
        raise ValueError(f"{path}.format 不受支持: {schema_format!r}")

    if schema_type == "object":
        properties = schema.get("properties", {})
        if not isinstance(properties, dict):
            raise ValueError(f"{path}.properties 必须是 object")
        required = schema.get("required", [])
        if (
            not isinstance(required, list)
            or any(not isinstance(item, str) for item in required)
            or not set(required).issubset(properties)
        ):
            raise ValueError(f"{path}.required 必须引用已声明的配置项")
        for key, child in properties.items():
            _validate_schema(child, f"{path}.{key}")
    elif schema_type == "array":
        items = schema.get("items")
        if not isinstance(items, dict) or items.get("type") != "string":
            raise ValueError(f"{path}.items 只支持 string")
        _validate_schema(items, f"{path}[]")
    elif "properties" in schema or "required" in schema or "items" in schema:
        raise ValueError(f"{path} 的结构关键字与 type 不匹配")

    bounds = [schema.get("minimum"), schema.get("maximum")]
    declared_bounds = [value for value in bounds if value is not None]
    if declared_bounds:
        if schema_type not in {"number", "integer"}:
            raise ValueError(f"{path} 只有 number/integer 支持数值边界")
        if any(
            not isinstance(value, (int, float)) or isinstance(value, bool)
            for value in declared_bounds
        ):
            raise ValueError(f"{path} 的 minimum/maximum 必须是数字")
        if (
            schema.get("minimum") is not None
            and schema.get("maximum") is not None
            and schema["minimum"] > schema["maximum"]
        ):
            raise ValueError(f"{path}.minimum 不能大于 maximum")

    value_schema = deepcopy(schema)
    value_schema.pop("default", None)
    value_schema.pop("enum", None)
    for index, enum_value in enumerate(schema.get("enum", [])):
        try:
            _normalize(value_schema, enum_value, f"{path}.enum[{index}]")
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{path}.enum[{index}] 类型或边界无效") from exc
    if "default" in schema:
        try:
            _normalize(schema, deepcopy(schema["default"]), f"{path}.default")
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{path}.default 不符合 Schema") from exc


def load_settings_schema(plugin_dir: Path, relative_path: str) -> dict[str, Any]:
    plugin_dir = Path(plugin_dir).resolve()
    schema_path = (plugin_dir / relative_path).resolve()
    if not _is_relative_to(schema_path, plugin_dir):
        raise ValueError("Settings Schema 必须位于 Plugin 目录内")
    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"Settings Schema 不存在: {relative_path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"Settings Schema JSON 无效: {exc}") from exc
    _validate_schema(schema)
    return schema


def _preserve_passwords(
    schema: dict[str, Any],
    value: Any,
    existing: Any,
) -> Any:
    if schema.get("type") != "object" or not isinstance(value, dict):
        return value
    existing_map = existing if isinstance(existing, dict) else {}
    result = dict(value)
    for key, child in schema.get("properties", {}).items():
        if key not in result:
            continue
        if (
            child.get("type") == "string"
            and child.get("format") == "password"
            and result[key] == ""
            and key in existing_map
        ):
            result[key] = existing_map[key]
        elif child.get("type") == "object":
            result[key] = _preserve_passwords(
                child,
                result[key],
                existing_map.get(key),
            )
    return result


def _normalize(
    schema: dict[str, Any],
    value: Any,
    path: str = "$",
    *,
    missing: bool = False,
) -> Any:
    if missing:
        if "default" in schema:
            value = deepcopy(schema["default"])
        else:
            return None

    schema_type = schema["type"]
    if schema_type == "object":
        if not isinstance(value, dict):
            raise ValueError(f"{path} 必须是 object")
        properties = schema.get("properties", {})
        unknown = sorted(set(value) - set(properties))
        if unknown:
            raise ValueError(f"{path} 包含未知配置项: {', '.join(unknown)}")
        result: dict[str, Any] = {}
        for key, child in properties.items():
            child_missing = key not in value
            child_value = _normalize(
                child,
                value.get(key),
                f"{path}.{key}",
                missing=child_missing,
            )
            if child_missing and child_value is None:
                if key in schema.get("required", []):
                    raise ValueError(f"{path}.{key} 是必填配置")
                continue
            result[key] = child_value
        return result

    if schema_type == "string":
        if not isinstance(value, str):
            raise ValueError(f"{path} 必须是 string")
        if schema.get("format") == "uri":
            parsed = urlparse(value)
            if not parsed.scheme:
                raise ValueError(f"{path} 必须是 URI")
    elif schema_type == "boolean":
        if not isinstance(value, bool):
            raise ValueError(f"{path} 必须是 boolean")
    elif schema_type == "integer":
        if not isinstance(value, int) or isinstance(value, bool):
            raise ValueError(f"{path} 必须是 integer")
    elif schema_type == "number":
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise ValueError(f"{path} 必须是 number")
    elif schema_type == "array":
        if not isinstance(value, list) or any(
            not isinstance(item, str) for item in value
        ):
            raise ValueError(f"{path} 必须是 string array")

    if "enum" in schema and value not in schema["enum"]:
        raise ValueError(f"{path} 不在允许的 enum 中")
    if "minimum" in schema and value < schema["minimum"]:
        raise ValueError(f"{path} 小于 minimum")
    if "maximum" in schema and value > schema["maximum"]:
        raise ValueError(f"{path} 大于 maximum")
    return value


def validate_plugin_settings(
    schema: dict[str, Any],
    values: dict[str, Any],
) -> dict[str, Any]:
    _validate_schema(schema)
    if not isinstance(values, dict):
        raise ValueError("Plugin 配置必须是 object")
    return _normalize(schema, values)


def _sparse_schema(schema: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(schema)
    result.pop("default", None)
    if result.get("type") == "object":
        result["required"] = []
        result["properties"] = {
            key: _sparse_schema(child)
            for key, child in result.get("properties", {}).items()
        }
    elif result.get("type") == "array":
        result["items"] = _sparse_schema(result["items"])
    return result


def validate_sparse_plugin_settings(
    schema: dict[str, Any],
    values: dict[str, Any],
) -> dict[str, Any]:
    """Validate only explicitly supplied values without persisting defaults."""
    _validate_schema(schema)
    return _normalize(_sparse_schema(schema), values)


def resolve_applied_plugin_settings(
    schema: dict[str, Any],
    values: dict[str, Any],
) -> dict[str, Any]:
    """Resolve saved values against process env, then Schema defaults."""
    runtime_schema = deepcopy(schema)
    required = list(runtime_schema.get("required", []))
    for key, child in runtime_schema.get("properties", {}).items():
        provided_by_environment = (
            key in os.environ or f"{key}_FILE" in os.environ
        )
        if key in values or not provided_by_environment:
            continue
        child.pop("default", None)
        required = [item for item in required if item != key]
    runtime_schema["required"] = required
    return validate_plugin_settings(runtime_schema, values)


def redact_plugin_settings(
    schema: dict[str, Any],
    values: dict[str, Any],
    sensitive_paths: set[tuple[str, ...]] | None = None,
    *,
    _path: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Project saved values onto the current Schema and blank passwords."""
    if schema.get("type") != "object" or not isinstance(values, dict):
        return {}
    sensitive_paths = sensitive_paths or set()
    result: dict[str, Any] = {}
    for key, child in schema.get("properties", {}).items():
        if key not in values:
            continue
        child_path = (*_path, key)
        if (
            child_path in sensitive_paths
            or (
                child.get("type") == "string"
                and child.get("format") == "password"
            )
        ):
            result[key] = ""
        elif child.get("type") == "object":
            result[key] = redact_plugin_settings(
                child,
                values[key],
                sensitive_paths,
                _path=child_path,
            )
        else:
            result[key] = deepcopy(values[key])
    return result


def _schema_password_paths(
    schema: dict[str, Any],
    path: tuple[str, ...] = (),
) -> set[tuple[str, ...]]:
    result: set[tuple[str, ...]] = set()
    if schema.get("type") != "object":
        return result
    for key, child in schema.get("properties", {}).items():
        child_path = (*path, key)
        if (
            child.get("type") == "string"
            and child.get("format") == "password"
        ):
            result.add(child_path)
        elif child.get("type") == "object":
            result.update(_schema_password_paths(child, child_path))
    return result


def _legacy_string_paths(
    values: Any,
    path: tuple[str, ...] = (),
) -> set[tuple[str, ...]]:
    if isinstance(values, str):
        return {path}
    if not isinstance(values, dict):
        return set()
    result: set[tuple[str, ...]] = set()
    for key, value in values.items():
        if isinstance(key, str):
            result.update(_legacy_string_paths(value, (*path, key)))
    return result


def settings_environment(
    values: dict[str, Any],
    *,
    schema: dict[str, Any] | None = None,
    environ: Mapping[str, str] | None = None,
) -> dict[str, str]:
    result: dict[str, str] = {}
    for key, value in values.items():
        if not _ENV_NAME.fullmatch(key) or value is None:
            continue
        if isinstance(value, bool):
            result[key] = "true" if value else "false"
        elif isinstance(value, (dict, list)):
            result[key] = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        else:
            result[key] = str(value)

    if not schema or schema.get("type") != "object":
        return result

    runtime_environment = os.environ if environ is None else environ
    for key, child in schema.get("properties", {}).items():
        if (
            key in values
            or not isinstance(key, str)
            or not _ENV_NAME.fullmatch(key)
            or not isinstance(child, dict)
        ):
            continue
        file_key = f"{key}_FILE"
        if (
            child.get("type") == "string"
            and child.get("format") == "password"
            and file_key in runtime_environment
        ):
            result[file_key] = runtime_environment[file_key]
        elif key in runtime_environment:
            result[key] = runtime_environment[key]
        elif file_key in runtime_environment:
            result[file_key] = runtime_environment[file_key]
    return result


class PluginConfigStore:
    """Persist validated settings while preserving them across uninstall."""

    def __init__(self, root: Path):
        self.root = Path(root).resolve()

    def path_for(self, plugin_id: str) -> Path:
        if not _PLUGIN_ID.fullmatch(plugin_id):
            raise ValueError(f"无效 Plugin ID: {plugin_id}")
        return self.root / f"{plugin_id}.json"

    def sensitive_path_for(self, plugin_id: str) -> Path:
        self.path_for(plugin_id)
        return self.root / f".{plugin_id}.sensitive.json"

    def load(self, plugin_id: str) -> dict[str, Any]:
        path = self.path_for(plugin_id)
        if not path.exists():
            return {}
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"Plugin 配置 JSON 无效: {plugin_id}: {exc}") from exc
        if not isinstance(value, dict):
            raise ValueError(f"Plugin 配置必须是 object: {plugin_id}")
        return value

    def save(
        self,
        plugin_id: str,
        schema: dict[str, Any],
        values: dict[str, Any],
    ) -> dict[str, Any]:
        existing = self.load(plugin_id)
        values = _preserve_passwords(schema, values, existing)
        normalized = validate_sparse_plugin_settings(schema, values)
        resolve_applied_plugin_settings(schema, normalized)
        historical = self._load_sensitive_paths(plugin_id)
        sensitive_paths = (historical or set()) | _schema_password_paths(schema)
        self._write_sensitive_paths(plugin_id, sensitive_paths)
        self.write(plugin_id, normalized)
        return normalized

    def sensitive_paths(
        self,
        plugin_id: str,
        schema: dict[str, Any],
        values: dict[str, Any],
    ) -> set[tuple[str, ...]]:
        historical = self._load_sensitive_paths(plugin_id)
        current = _schema_password_paths(schema)
        if historical is not None:
            return historical | current
        if self.path_for(plugin_id).is_file():
            return current | _legacy_string_paths(values)
        return current

    def write(self, plugin_id: str, values: dict[str, Any]) -> None:
        if not isinstance(values, dict):
            raise ValueError("Plugin 配置必须是 object")
        self.root.mkdir(parents=True, exist_ok=True)
        target = self.path_for(plugin_id)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{plugin_id}.",
            suffix=".tmp",
            dir=self.root,
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(values, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            temporary.replace(target)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise

    def delete(self, plugin_id: str) -> None:
        self.path_for(plugin_id).unlink(missing_ok=True)
        self.sensitive_path_for(plugin_id).unlink(missing_ok=True)

    def _load_sensitive_paths(
        self,
        plugin_id: str,
    ) -> set[tuple[str, ...]] | None:
        path = self.sensitive_path_for(plugin_id)
        if not path.exists():
            return None
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
            raw_paths = document["sensitive_paths"]
            if (
                document.get("schema_version") != 1
                or not isinstance(raw_paths, list)
            ):
                raise ValueError("metadata schema")
            result: set[tuple[str, ...]] = set()
            for item in raw_paths:
                if (
                    not isinstance(item, list)
                    or not item
                    or any(
                        not isinstance(segment, str)
                        for segment in item
                    )
                ):
                    raise ValueError("metadata path")
                result.add(tuple(item))
            return result
        except (
            KeyError,
            OSError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
        ) as exc:
            raise ValueError(
                f"Plugin 敏感配置元数据无效: {plugin_id}"
            ) from exc

    def _write_sensitive_paths(
        self,
        plugin_id: str,
        paths: set[tuple[str, ...]],
    ) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        target = self.sensitive_path_for(plugin_id)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{plugin_id}.sensitive.",
            suffix=".tmp",
            dir=self.root,
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(
                    {
                        "schema_version": 1,
                        "sensitive_paths": [
                            list(path) for path in sorted(paths)
                        ],
                    },
                    handle,
                    ensure_ascii=False,
                    indent=2,
                )
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            temporary.replace(target)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise


def load_applied_plugin_configs(
    config_store: PluginConfigStore,
    manifests: dict[str, ExtensionManifest],
    owners: list[str],
    *,
    on_error: Callable[[str, Exception], None] | None = None,
    strict: bool = False,
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for owner in owners:
        try:
            manifest = manifests[owner]
            values = config_store.load(owner)
            settings_schema = getattr(manifest, "settings_schema", "")
            base_path = getattr(manifest, "base_path", None)
            if settings_schema and base_path is not None:
                schema = load_settings_schema(
                    base_path,
                    settings_schema,
                )
                values = resolve_applied_plugin_settings(schema, values)
            result[owner] = values
        except (OSError, TypeError, ValueError) as exc:
            if on_error is not None:
                on_error(owner, exc)
            if strict:
                raise
    return result


def prepare_applied_plugin_configs(
    config_store: PluginConfigStore,
    manifests: dict[str, ExtensionManifest],
    owners: list[str],
    *,
    snapshot_root: Path,
    on_error: Callable[[str, Exception], None] | None = None,
    strict: bool = False,
) -> tuple[dict[str, dict[str, Any]], PluginConfigStore]:
    """Create an immutable-per-start snapshot for processes and runtime services."""
    snapshot_root = Path(snapshot_root).resolve()
    snapshot_root.mkdir(parents=True, exist_ok=True)
    snapshot_store = PluginConfigStore(
        Path(tempfile.mkdtemp(prefix="startup-", dir=snapshot_root))
    )
    try:
        configs = load_applied_plugin_configs(
            config_store,
            manifests,
            owners,
            on_error=on_error,
            strict=strict,
        )
        for owner, values in configs.items():
            snapshot_store.write(owner, values)
    except Exception:
        shutil.rmtree(snapshot_store.root, ignore_errors=True)
        raise
    return configs, snapshot_store
