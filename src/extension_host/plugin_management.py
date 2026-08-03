"""Desired-state management facade for installable Plugin Packages."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

from src.extension_api.models import ExtensionManifest
from src.environment import determinflow_env_is_set, get_determinflow_env
from src.plugin_system import (
    PluginLockRecord,
    PluginStoreError,
)

from .manifest import parse_extension_manifest
from .plugin_config import (
    load_settings_schema,
    redact_plugin_settings,
    validate_sparse_plugin_settings,
)
from .plugin_preflight import validate_plugin_checkout
from .source_config import (
    PluginCatalogService,
    PluginSourceStore,
    source_config_response,
)

if TYPE_CHECKING:
    from .manager import ExtensionManager


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _read_boolean_environment(suffix: str) -> bool:
    name = f"DETERMINFLOW_{suffix}"
    value = (get_determinflow_env(suffix, "") or "").strip().lower()
    if value in {"", "0", "false", "no", "off"}:
        return False
    if value in {"1", "true", "yes", "on"}:
        return True
    raise ValueError(f"{name} 必须是 boolean")


class PluginManagement:
    """Mutate desired Plugin state without changing the running process."""

    def __init__(self, manager: ExtensionManager):
        self.manager = manager
        self.store = manager.plugin_store
        self.config_store = manager.plugin_config_store
        self._applied_records = dict(manager._applied_plugin_records)
        self._applied_store_snapshot = self.store.snapshot()
        self._applied_enabled = frozenset(manager._load_order)
        self._applied_settings = self._settings_snapshot()
        self.package_management_read_only = _read_boolean_environment(
            "PLUGIN_PACKAGES_READ_ONLY"
        )
        self._source_store = PluginSourceStore(manager.plugin_source_file)
        self._catalog = PluginCatalogService(self._source_store.list())

    def list_response(self) -> dict[str, Any]:
        records = self.store.read_lock()
        statuses = {
            item["id"]: item
            for item in self.manager.get_statuses()
        }
        desired_enabled = self._desired_enabled()
        plugin_ids = sorted({
            *statuses,
            *self._applied_records,
            *records,
            *desired_enabled,
        })
        plugins = [
            self._build_record(
                plugin_id,
                statuses=statuses,
                desired_records=records,
                desired_enabled=desired_enabled,
            )
            for plugin_id in plugin_ids
        ]
        return {
            "plugins": plugins,
            "restart_required": self.restart_required(),
            "package_management_read_only": (
                self.package_management_read_only
            ),
        }

    def get_record(self, plugin_id: str) -> dict[str, Any]:
        response = self.list_response()
        for plugin in response["plugins"]:
            if plugin["id"] == plugin_id:
                return plugin
        raise PluginStoreError(f"plugin is not installed or discovered: {plugin_id}")

    def sources_response(self) -> dict[str, Any]:
        return {
            "sources": [
                source_config_response(source)
                for source in self._source_store.list()
            ],
            "package_management_read_only": self.package_management_read_only,
        }

    def catalog_response(self, *, refresh: bool = False) -> dict[str, Any]:
        if self.package_management_read_only:
            return {
                "sources": [
                    {
                        **source,
                        "resolved_commit": "",
                        "plugin_count": 0,
                        "error": "",
                    }
                    for source in self.sources_response()["sources"]
                ],
                "plugins": [],
                "package_management_read_only": True,
            }
        return self._catalog.get(refresh=refresh)

    def create_source(self, *, name: str, url: str, ref: str) -> dict[str, Any]:
        self._ensure_package_mutable()
        source = self._source_store.create(name=name, url=url, ref=ref)
        self._reload_catalog_sources()
        return {
            "source": source_config_response(source),
            "catalog": self.catalog_response(refresh=True),
        }

    def update_source(
        self,
        source_id: str,
        *,
        name: str,
        url: str,
        ref: str,
    ) -> dict[str, Any]:
        self._ensure_package_mutable()
        source = self._source_store.update(
            source_id,
            name=name,
            url=url,
            ref=ref,
        )
        self._reload_catalog_sources()
        return {
            "source": source_config_response(source),
            "catalog": self.catalog_response(refresh=True),
        }

    def delete_source(self, source_id: str) -> dict[str, Any]:
        self._ensure_package_mutable()
        source = self._source_store.delete(source_id)
        self._reload_catalog_sources()
        return {"source": source_config_response(source)}

    def _reload_catalog_sources(self) -> None:
        self._catalog.replace_sources(self._source_store.list())

    def install(
        self,
        plugin_id: str,
        source: str,
        *,
        ref: str = "HEAD",
        subdirectory: str = "",
        resource_prefix: str | None = None,
        acknowledge_risk: bool = False,
    ) -> dict[str, Any]:
        self._ensure_package_mutable()
        self.store.install(
            plugin_id,
            source,
            ref=ref,
            subdirectory=subdirectory,
            resource_prefix=resource_prefix,
            acknowledge_risk=acknowledge_risk,
            preflight=lambda checkout: validate_plugin_checkout(
                plugin_id,
                checkout,
                resource_prefix=resource_prefix,
            ),
        )
        return self.get_record(plugin_id)

    def update(
        self,
        plugin_id: str,
        *,
        ref: str | None = None,
    ) -> dict[str, Any]:
        self._ensure_package_mutable()
        current = self.store.get(plugin_id)
        if current is None:
            raise PluginStoreError(f"plugin is not package-managed: {plugin_id}")
        self.store.update(
            plugin_id,
            ref=ref or current.active_revision.requested_ref,
            acknowledge_risk=current.trust == "third_party",
            preflight=lambda checkout: validate_plugin_checkout(
                plugin_id,
                checkout,
                resource_prefix=current.resource_prefix_override,
            ),
        )
        return self.get_record(plugin_id)

    def rollback(self, plugin_id: str) -> dict[str, Any]:
        self._ensure_package_mutable()
        current = self.store.get(plugin_id)
        if current is None:
            raise PluginStoreError(f"plugin is not package-managed: {plugin_id}")
        self.store.rollback(
            plugin_id,
            preflight=lambda checkout: validate_plugin_checkout(
                plugin_id,
                checkout,
                resource_prefix=current.resource_prefix_override,
            ),
        )
        return self.get_record(plugin_id)

    def uninstall(self, plugin_id: str) -> dict[str, Any]:
        self._ensure_package_mutable()
        self._ensure_file_managed_enabled_state()
        if self.store.get(plugin_id) is None:
            raise PluginStoreError(f"plugin is not package-managed: {plugin_id}")
        self._ensure_not_required(plugin_id)
        config_existed = self.manager.config_file.exists()
        original_config = self._read_extension_config()
        disabled_config = {
            **original_config,
            "enabled": [
                item
                for item in original_config["enabled"]
                if item != plugin_id
            ],
        }
        self._write_json_atomic(self.manager.config_file, disabled_config)
        try:
            self.store.mark_uninstall(plugin_id)
        except Exception:
            try:
                if config_existed:
                    self._write_json_atomic(
                        self.manager.config_file,
                        original_config,
                    )
                else:
                    self.manager.config_file.unlink(missing_ok=True)
            except Exception as rollback_error:
                raise PluginStoreError(
                    "Plugin 卸载失败，且启用配置补偿失败"
                ) from rollback_error
            raise
        return self.get_record(plugin_id)

    def set_enabled(self, plugin_id: str, enabled: bool) -> dict[str, Any]:
        self._ensure_file_managed_enabled_state()
        records = self.store.read_lock()
        status_ids = {item["id"] for item in self.manager.get_statuses()}
        record = records.get(plugin_id)
        if record is not None and record.pending_remove and enabled:
            self.store.cancel_uninstall(plugin_id)
        elif plugin_id not in records and plugin_id not in status_ids:
            raise PluginStoreError(f"plugin is not installed: {plugin_id}")
        if not enabled:
            self._ensure_not_required(plugin_id)
        self._write_enabled(plugin_id, enabled)
        return self.get_record(plugin_id)

    def save_config(
        self,
        plugin_id: str,
        settings: dict[str, Any],
    ) -> dict[str, Any]:
        manifest = self._desired_manifest(plugin_id)
        if not manifest.settings_schema:
            raise ValueError(f"Plugin 未声明 Settings Schema: {plugin_id}")
        schema = load_settings_schema(
            manifest.base_path,
            manifest.settings_schema,
        )
        self.config_store.save(plugin_id, schema, settings)
        return self.get_record(plugin_id)

    def reset_config(self, plugin_id: str) -> dict[str, Any]:
        self._desired_manifest(plugin_id)
        self.config_store.delete(plugin_id)
        return self.get_record(plugin_id)

    def restart_required(self) -> bool:
        desired_enabled = self._desired_enabled()
        return (
            desired_enabled != self._applied_enabled
            or self.store.snapshot() != self._applied_store_snapshot
            or self._settings_snapshot() != self._applied_settings
        )

    def static_file(self, plugin_id: str, asset_path: str = "") -> Path:
        if not self.manager.is_running(plugin_id):
            raise PluginStoreError(f"plugin is not running: {plugin_id}")
        manifest = self.manager._manifests.get(plugin_id)
        if manifest is None or manifest.page is None or manifest.base_path is None:
            raise PluginStoreError(f"plugin has no static page: {plugin_id}")
        plugin_root = manifest.base_path.resolve()
        static_root = (plugin_root / manifest.page.static_dir).resolve()
        if not _is_relative_to(static_root, plugin_root) or not static_root.is_dir():
            raise PluginStoreError(f"plugin static page directory is invalid: {plugin_id}")
        relative = asset_path.strip("/") or manifest.page.entrypoint
        requested = (static_root / relative).resolve()
        if not _is_relative_to(requested, static_root) or not requested.is_file():
            raise PluginStoreError(f"plugin static asset not found: {plugin_id}/{relative}")
        return requested

    def _ensure_package_mutable(self) -> None:
        if self.package_management_read_only:
            raise PluginStoreError(
                "当前部署由不可变 Release 管理 Plugin 包；"
                "请构建并激活新 Release 以安装、更新、回退或卸载"
            )

    def _build_record(
        self,
        plugin_id: str,
        *,
        statuses: dict[str, dict[str, Any]],
        desired_records: dict[str, PluginLockRecord],
        desired_enabled: frozenset[str],
    ) -> dict[str, Any]:
        status = statuses.get(plugin_id, {})
        applied_record = self._applied_records.get(plugin_id)
        desired_record = desired_records.get(plugin_id)
        diagnostic = str(status.get("error", ""))
        try:
            desired_manifest = self._try_desired_manifest(plugin_id)
        except (OSError, ValueError, PluginStoreError) as exc:
            desired_manifest = None
            diagnostic = self._append_error(
                diagnostic,
                f"Plugin 清单无效: {exc}",
            )
        active_manifest = self.manager._manifests.get(plugin_id)
        display_manifest = desired_manifest or active_manifest
        schema: dict[str, Any] | None = None
        settings: dict[str, Any] = {}
        config_present = self.config_store.path_for(plugin_id).is_file()
        if desired_manifest is not None and desired_manifest.settings_schema:
            try:
                schema = load_settings_schema(
                    desired_manifest.base_path,
                    desired_manifest.settings_schema,
                )
                raw_settings = self.config_store.load(plugin_id)
                sensitive_paths = self.config_store.sensitive_paths(
                    plugin_id,
                    schema,
                    raw_settings,
                )
                settings = redact_plugin_settings(
                    schema,
                    validate_sparse_plugin_settings(schema, raw_settings),
                    sensitive_paths,
                )
            except ValueError as exc:
                diagnostic = self._append_error(diagnostic, str(exc))

        desired_revision = (
            None
            if desired_record is None or desired_record.pending_remove
            else desired_record.active_revision
        )
        applied_revision = (
            applied_record.active_revision
            if applied_record is not None
            else None
        )
        item_restart_required = (
            (plugin_id in self._applied_enabled)
            != (plugin_id in desired_enabled)
            or self._revision_identity(applied_revision)
            != self._revision_identity(desired_revision)
            or self._settings_digest(plugin_id, self._applied_settings)
            != self._settings_digest(plugin_id, self._settings_snapshot())
        )
        source_record = desired_record or applied_record
        source = self._source_payload(source_record)
        resource_prefix = (
            source_record.resource_prefix
            if source_record is not None
            else (
                display_manifest.resource_prefix
                if display_manifest is not None
                else ""
            )
        )
        active_version = (
            active_manifest.version
            if active_manifest is not None
            and status.get("status") != "missing"
            else None
        )
        desired_version = (
            desired_manifest.version
            if desired_manifest is not None
            else (
                active_manifest.version
                if source_record is None
                and active_manifest is not None
                and status.get("status") != "missing"
                else None
            )
        )
        page_url = None
        if (
            active_manifest is not None
            and active_manifest.page is not None
            and status.get("status") == "running"
        ):
            page_url = (
                f"/api/plugins/{quote(plugin_id, safe='')}/ui/"
                f"{quote(active_manifest.page.entrypoint, safe='/')}"
            )
        return {
            "id": plugin_id,
            "name": display_manifest.name if display_manifest else plugin_id,
            "description": (
                display_manifest.description if display_manifest else ""
            ),
            "resource_prefix": resource_prefix,
            "runtime_status": status.get("status", "disabled"),
            "error": diagnostic,
            "active_enabled": bool(status.get("enabled", False)),
            "desired_enabled": plugin_id in desired_enabled,
            "active_version": active_version,
            "desired_version": desired_version,
            "restart_required": item_restart_required,
            "pending_action": (
                desired_record.pending_action if desired_record else None
            ),
            "dependencies": (
                list(display_manifest.dependencies) if display_manifest else []
            ),
            "capabilities": (
                list(display_manifest.capabilities) if display_manifest else []
            ),
            "source": source,
            "settings_schema": schema,
            "settings": settings,
            "config_present": config_present,
            "page_url": page_url,
            "processes": list(status.get("processes", [])),
        }

    def _desired_manifest(self, plugin_id: str) -> ExtensionManifest:
        manifest = self._try_desired_manifest(plugin_id)
        if manifest is None:
            raise PluginStoreError(f"plugin is not installed: {plugin_id}")
        return manifest

    def _try_desired_manifest(
        self,
        plugin_id: str,
    ) -> ExtensionManifest | None:
        record = self.store.get(plugin_id)
        if record is not None and not record.pending_remove:
            manifest = parse_extension_manifest(
                Path(record.active_revision.checkout_path) / "extension.toml"
            )
            if manifest.extension_id != plugin_id:
                raise PluginStoreError(
                    f"manifest plugin id does not match lock: {plugin_id}"
                )
            return manifest
        if record is None:
            manifest = self.manager._manifests.get(plugin_id)
            state = self.manager._states.get(plugin_id, {})
            if manifest is not None and state.get("status") != "missing":
                return manifest
        return None

    def _read_extension_config(self) -> dict[str, Any]:
        if not self.manager.config_file.exists():
            return {"enabled": [], "strict_startup": False}
        try:
            document = json.loads(
                self.manager.config_file.read_text(encoding="utf-8")
            )
        except json.JSONDecodeError as exc:
            raise ValueError(f"extensions.json 无效: {exc}") from exc
        if not isinstance(document, dict):
            raise ValueError("extensions.json 必须是 object")
        enabled = document.get("enabled", [])
        if (
            not isinstance(enabled, list)
            or any(not isinstance(item, str) or not item.strip() for item in enabled)
        ):
            raise ValueError("extensions.enabled 必须是非空字符串数组")
        return {
            **document,
            "enabled": list(dict.fromkeys(item.strip() for item in enabled)),
            "strict_startup": bool(document.get("strict_startup", False)),
        }

    def _explicit_desired_enabled(self) -> list[str]:
        if determinflow_env_is_set("EXTENSIONS"):
            return list(self.manager._configured_enabled)
        return self._read_extension_config()["enabled"]

    def _desired_enabled(self) -> frozenset[str]:
        enabled: set[str] = set()
        visiting: set[str] = set()

        def visit(plugin_id: str) -> None:
            if plugin_id in enabled or plugin_id in visiting:
                return
            visiting.add(plugin_id)
            enabled.add(plugin_id)
            try:
                manifest = self._try_desired_manifest(plugin_id)
            except (OSError, ValueError, PluginStoreError):
                manifest = None
            if manifest is not None:
                for dependency in manifest.dependencies:
                    visit(dependency)
            visiting.remove(plugin_id)

        for plugin_id in self._explicit_desired_enabled():
            visit(plugin_id)
        return frozenset(enabled)

    def _ensure_not_required(self, plugin_id: str) -> None:
        required_by: list[str] = []
        for enabled_id in self._explicit_desired_enabled():
            if enabled_id == plugin_id:
                continue
            seen: set[str] = set()
            pending = [enabled_id]
            while pending:
                current = pending.pop()
                if current in seen:
                    continue
                seen.add(current)
                try:
                    manifest = self._try_desired_manifest(current)
                except (OSError, ValueError, PluginStoreError):
                    manifest = None
                if manifest is None:
                    continue
                if plugin_id in manifest.dependencies:
                    required_by.append(enabled_id)
                    break
                pending.extend(manifest.dependencies)
        if required_by:
            raise ValueError(
                f"Plugin {plugin_id} 被已启用 Plugin 依赖: "
                f"{', '.join(sorted(set(required_by)))}；请先停用依赖方"
            )

    def _write_enabled(self, plugin_id: str, enabled: bool) -> None:
        document = self._read_extension_config()
        values = list(document["enabled"])
        if enabled and plugin_id not in values:
            values.append(plugin_id)
        elif not enabled:
            values = [item for item in values if item != plugin_id]
        document["enabled"] = values
        self._write_json_atomic(self.manager.config_file, document)

    def _ensure_file_managed_enabled_state(self) -> None:
        if determinflow_env_is_set("EXTENSIONS"):
            raise ValueError(
                "DETERMINFLOW_EXTENSIONS 正在覆盖文件配置，不能通过管理 API 修改启用状态"
            )

    def _settings_snapshot(self) -> dict[str, str]:
        root = self.config_store.root
        if not root.exists():
            return {}
        result: dict[str, str] = {}
        for path in sorted(root.glob("*.json")):
            if path.name.startswith("."):
                continue
            result[path.stem] = hashlib.sha256(path.read_bytes()).hexdigest()
        return result

    @staticmethod
    def _settings_digest(plugin_id: str, snapshot: dict[str, str]) -> str:
        return snapshot.get(plugin_id, "")

    @staticmethod
    def _revision_identity(revision: Any) -> tuple[str, str] | None:
        if revision is None:
            return None
        return revision.commit, revision.content_sha256

    @staticmethod
    def _source_payload(record: PluginLockRecord | None) -> dict[str, str]:
        if record is None:
            return {
                "url": "bundled",
                "ref": "",
                "subdirectory": "",
                "trust": "official",
                "resolved_commit": "",
                "content_sha256": "",
            }
        revision = record.active_revision
        return {
            "url": record.source,
            "ref": revision.requested_ref,
            "subdirectory": record.subdirectory,
            "trust": record.trust,
            "resolved_commit": revision.commit,
            "content_sha256": revision.content_sha256,
        }

    @staticmethod
    def _append_error(current: str, additional: str) -> str:
        return f"{current}；{additional}" if current else additional

    @staticmethod
    def _write_json_atomic(path: Path, document: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=path.parent,
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(document, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            temporary.replace(path)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
