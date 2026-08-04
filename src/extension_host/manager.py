"""Project-level extension discovery and lifecycle management."""

from __future__ import annotations

import asyncio
import importlib
import inspect
import json
import logging
import os
import shutil
import sys
from dataclasses import replace
from importlib import metadata
from pathlib import Path
from typing import Any, Iterable

from src.extension_api.models import (
    EXTENSION_API_VERSION,
    CoreRuntime,
    ExtensionManifest,
    HealthCheckResult,
    PromptContextRequest,
    PromptContribution,
)
from src.extension_api.registrar import ExtensionContributions, ExtensionRegistrar, OwnedPath
from src.environment import get_determinflow_env
from src.plugin_system import (
    PluginStore,
    ProcessManager,
    install_applied_plugin_requirements,
)
from .manifest import SUPPORTED_RESOURCE_TYPES, parse_extension_manifest
from .lifecycle import load_extension_lifecycle, run_extension_lifecycle
from .plugin_config import (
    PluginConfigStore,
    load_settings_schema,
    prepare_applied_plugin_configs,
    settings_environment,
)
from .plugin_management import PluginManagement
from .process_adapter import start_manifest_processes
from .resource_ids import ResourceIdResolver
from .resource_preparation import (
    build_plugin_resource_plan,
    prepare_plugin_resources,
)
from .resource_validation import validate_file_resources
from .runtime_failure import (
    RuntimeStartBlocked,
    cleanup_blocked_start,
    ensure_runtime_start_allowed,
    handle_managed_process_exit,
    reload_runtime_resource_managers,
)
from .source_config import load_plugin_sources
from .tool_registry import ExtensionToolRegistry
from .workflow_provisioning import provision_plugin_workflows

logger = logging.getLogger(__name__)

_SUPPORTED_RESOURCE_TYPES = SUPPORTED_RESOURCE_TYPES
_ENTRY_POINT_GROUPS = ("determinflow.extensions", "ai_company.extensions")


EXTENSION_DISABLED = "disabled"
EXTENSION_DISCOVERED = "discovered"
EXTENSION_LOADED = "loaded"
EXTENSION_STARTING = "starting"
EXTENSION_RUNNING = "running"
EXTENSION_DEGRADED = "degraded"
EXTENSION_BLOCKED = "blocked"
EXTENSION_MISSING = "missing"

_JSON_RESOURCE_SCHEMAS = {
    "agents": ("agents_config.json", ("agents",), ()),
    "prompts": ("prompts_config.json", ("agents",), ()),
    "skills": (
        "skills_config.json",
        ("skills", "skill_configs"),
        ("groups",),
    ),
    "rules": (
        "rules_config.json",
        ("rules", "rule_configs"),
        ("groups",),
    ),
    "preset_phrases": ("preset_phrases.json", (), ("phrases",)),
}
class _ResourceOnlyExtension:
    def __init__(self, manifest: ExtensionManifest):
        self.manifest = manifest

    def register(self, registrar: ExtensionRegistrar) -> None:
        return None

    async def start(self, runtime: CoreRuntime) -> None:
        return None

    async def stop(self) -> None:
        return None


class ExtensionManager:
    """Owns discovery, dependency ordering, registration and lifecycle."""

    def __init__(
        self,
        base_dir: Path,
        *,
        config_file: Path | None = None,
        workflows_dir: Path | None = None,
        enabled: Iterable[str] | None = None,
        discover_entry_points: bool = True,
        plugins_dir: Path | None = None,
        plugin_store: PluginStore | None = None,
        process_manager: ProcessManager | None = None,
        plugin_logs_dir: Path | None = None,
    ):
        self.base_dir = Path(base_dir).resolve()
        self.extensions_dir = self.base_dir / "extensions"
        self.config_file = Path(
            config_file or self.base_dir / "config" / "extensions.json"
        ).resolve()
        self.config_dir = self.config_file.parent
        self.plugin_source_file = self.config_dir / "plugin-sources.json"
        self.plugin_sources = tuple(
            load_plugin_sources(self.plugin_source_file)
        )
        self.workflows_dir = Path(
            workflows_dir or self.base_dir / "data" / "workflows"
        ).resolve()
        if plugin_store is not None:
            self.plugin_store = plugin_store
            self.plugins_dir = plugin_store.root
        else:
            self.plugins_dir = Path(
                plugins_dir or self.base_dir / "data" / "plugins"
            ).resolve()
            self.plugin_store = PluginStore(
                self.plugins_dir,
                official_sources=(
                    source.url
                    for source in self.plugin_sources
                    if source.kind == "official"
                ),
                official_source_mirrors={
                    source.url: source.mirrors
                    for source in self.plugin_sources
                    if source.kind == "official"
                },
            )
        self._applied_plugin_records = self.plugin_store.apply_pending()
        self.plugin_config_store = PluginConfigStore(self.plugins_dir / "config")
        self.plugin_data_dir = self.plugins_dir / "data"
        self.plugin_runtime_resources_dir = self.plugins_dir / "runtime-resources"
        self.resource_resolver = ResourceIdResolver()
        self.process_manager = process_manager or ProcessManager(
            plugin_logs_dir or self.base_dir / "logs" / "plugins"
        )
        self.contributions = ExtensionContributions()
        self._manifests: dict[str, ExtensionManifest] = {}
        self._extensions: dict[str, Any] = {}
        self._entry_points: dict[str, Any] = {}
        self._states: dict[str, dict[str, Any]] = {}
        self._load_order: list[str] = []
        self._runtime: CoreRuntime | None = None
        self._registered_tool_owners: set[str] = set()
        self._started_extensions: set[str] = set()
        self._starting_extensions: dict[str, asyncio.Event] = {}
        self._degrading_extensions: set[str] = set()
        self._stopping = False
        self._strict_startup = False
        self._configured_enabled: list[str] = []
        self._missing_enabled: set[str] = set()
        self.process_manager.set_unexpected_exit_handler(self._handle_managed_process_exit)

        self._discover_local_manifests()
        self._discover_installed_manifests()
        if discover_entry_points:
            self._discover_entry_points()
        requested = self._resolve_enabled(enabled)
        self._load_order = self._resolve_dependencies(requested)
        (
            self._applied_plugin_configs,
            self.applied_plugin_config_store,
        ) = prepare_applied_plugin_configs(
            self.plugin_config_store,
            self._manifests,
            self._load_order,
            snapshot_root=self.plugins_dir / "runtime-config",
            on_error=lambda owner, exc: self._set_state(
                owner, EXTENSION_DEGRADED, f"Plugin 配置无效: {exc}"
            ),
            strict=self._strict_startup,
        )
        try:
            install_applied_plugin_requirements(
                self._manifests,
                (
                    owner
                    for owner in self._load_order
                    if owner in self._applied_plugin_records
                    and owner in self._applied_plugin_configs
                ),
                on_error=lambda owner, exc: self._set_state(
                    owner, EXTENSION_DEGRADED, str(exc)
                ),
                strict=self._strict_startup,
            )
            self._load_and_register()
            self.plugin_management = PluginManagement(self)
        except Exception:
            shutil.rmtree(
                self.applied_plugin_config_store.root,
                ignore_errors=True,
            )
            raise

    def _discover_local_manifests(self) -> None:
        if not self.extensions_dir.exists():
            return
        for manifest_path in sorted(self.extensions_dir.glob("*/extension.toml")):
            self._discover_manifest(manifest_path)

    def _discover_installed_manifests(self) -> None:
        for manifest_path in self.plugin_store.installed_manifest_paths():
            self._discover_manifest(
                manifest_path,
                replace_existing=True,
                use_applied_prefix=True,
            )

    def _discover_manifest(
        self,
        manifest_path: Path,
        *,
        replace_existing: bool = False,
        use_applied_prefix: bool = False,
    ) -> None:
        extension_id = manifest_path.parent.name.replace("_", "-")
        try:
            manifest = parse_extension_manifest(manifest_path)
            extension_id = manifest.extension_id
            if use_applied_prefix:
                record = self._applied_plugin_records.get(extension_id)
                if record is None:
                    raise ValueError(
                        f"Plugin lock 缺少已应用记录: {extension_id}"
                    )
                manifest = replace(
                    manifest,
                    resource_prefix=record.resource_prefix,
                )
            if extension_id in self._manifests and not replace_existing:
                raise ValueError(f"重复扩展 ID: {extension_id}")
        except Exception as exc:
            error = f"扩展清单无效: {manifest_path}: {exc}"
            if extension_id in self._manifests:
                self._set_state(extension_id, EXTENSION_DEGRADED, error)
            else:
                self._manifests[extension_id] = ExtensionManifest(
                    extension_id=extension_id,
                    name=extension_id,
                    version="0.0.0",
                    base_path=manifest_path.parent.resolve(),
                )
                self._states[extension_id] = {
                    "status": EXTENSION_DEGRADED,
                    "error": error,
                }
            logger.warning(error, exc_info=True)
            return

        self._manifests[extension_id] = manifest
        self._states[extension_id] = {
            "status": EXTENSION_DISABLED,
            "error": "",
        }

    def _discover_entry_points(self) -> None:
        for group in _ENTRY_POINT_GROUPS:
            try:
                entry_points = metadata.entry_points(group=group)
            except TypeError:
                entry_points = metadata.entry_points().get(group, [])
            for entry_point in entry_points:
                extension_id = str(entry_point.name).strip()
                if not extension_id:
                    raise ValueError("Extension entry point 缺少名称")
                if extension_id in self._manifests:
                    logger.debug(
                        "更高优先级的扩展定义覆盖 entry point: %s (%s)",
                        extension_id,
                        group,
                    )
                    continue
                distribution = getattr(entry_point, "dist", None)
                self._manifests[extension_id] = ExtensionManifest(
                    extension_id=extension_id,
                    name=extension_id,
                    version=str(getattr(distribution, "version", "0.0.0")),
                    description="Installed Python extension",
                )
                self._entry_points[extension_id] = entry_point
                self._states[extension_id] = {
                    "status": EXTENSION_DISABLED,
                    "error": "",
                }

    def _load_entry_point(self, extension_id: str) -> None:
        entry_point = self._entry_points.get(extension_id)
        if entry_point is None or extension_id in self._extensions:
            return
        loaded = entry_point.load()
        extension = loaded() if callable(loaded) else loaded
        manifest = extension.manifest
        if manifest.extension_id != extension_id:
            raise ValueError(
                f"Extension entry point 名称必须等于 manifest ID: "
                f"{extension_id} != {manifest.extension_id}"
            )
        self._manifests[extension_id] = manifest
        self._extensions[extension_id] = extension

    def _resolve_enabled(self, enabled: Iterable[str] | None) -> list[str]:
        config = {}
        if self.config_file.exists():
            with self.config_file.open("r", encoding="utf-8") as handle:
                config = json.load(handle)
        self._strict_startup = bool(config.get("strict_startup", False))

        env_value = get_determinflow_env("EXTENSIONS")
        if enabled is not None:
            requested = list(enabled)
        elif env_value is not None:
            normalized = env_value.strip().lower()
            requested = [] if normalized in {"", "none", "off"} else [
                item.strip() for item in env_value.split(",") if item.strip()
            ]
        else:
            requested = list(config.get("enabled", []))

        requested = list(dict.fromkeys(requested))
        if any(not isinstance(item, str) or not item.strip() for item in requested):
            raise ValueError("extensions.enabled 必须是非空字符串数组")
        requested = [item.strip() for item in requested]
        self._configured_enabled = requested
        unknown = sorted(set(requested) - set(self._manifests))
        if unknown:
            if self._strict_startup:
                raise ValueError(f"配置启用了未知扩展: {', '.join(unknown)}")
            self._missing_enabled.update(unknown)
            for extension_id in unknown:
                self._manifests[extension_id] = ExtensionManifest(
                    extension_id=extension_id,
                    name=extension_id,
                    version="0.0.0",
                )
                self._states[extension_id] = {
                    "status": EXTENSION_MISSING,
                    "error": "已配置启用，但 Plugin 尚未安装",
                }
            logger.warning("已配置但未安装的 Plugin: %s", ", ".join(unknown))
        return [item for item in requested if item not in self._missing_enabled]

    def _resolve_dependencies(self, requested: list[str]) -> list[str]:
        order: list[str] = []
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(extension_id: str) -> None:
            if extension_id in visited:
                return
            if extension_id in visiting:
                error = f"扩展依赖形成环: {extension_id}"
                if self._strict_startup:
                    raise ValueError(error)
                self._set_state(extension_id, EXTENSION_DEGRADED, error)
                return
            manifest = self._manifests.get(extension_id)
            if manifest is None:
                if self._strict_startup:
                    raise ValueError(f"缺少扩展依赖: {extension_id}")
                self._manifests[extension_id] = ExtensionManifest(
                    extension_id=extension_id,
                    name=extension_id,
                    version="0.0.0",
                )
                self._states[extension_id] = {
                    "status": EXTENSION_MISSING,
                    "error": "被已启用 Plugin 依赖，但尚未安装",
                }
                visited.add(extension_id)
                return
            if self._states[extension_id]["status"] == EXTENSION_DEGRADED:
                visited.add(extension_id)
                order.append(extension_id)
                if self._strict_startup:
                    raise ValueError(self._states[extension_id]["error"])
                return
            visiting.add(extension_id)
            try:
                self._load_entry_point(extension_id)
                manifest = self._manifests[extension_id]
                if manifest.api_version != EXTENSION_API_VERSION:
                    raise ValueError(
                        f"扩展 {extension_id} API 版本不兼容: "
                        f"{manifest.api_version} != {EXTENSION_API_VERSION}"
                    )
            except Exception as exc:
                visiting.remove(extension_id)
                visited.add(extension_id)
                order.append(extension_id)
                self._set_state(
                    extension_id,
                    EXTENSION_DEGRADED,
                    f"扩展加载失败: {exc}",
                )
                logger.warning(
                    "扩展加载降级: %s: %s",
                    extension_id,
                    exc,
                    exc_info=True,
                )
                if self._strict_startup:
                    raise
                return

            for dependency in manifest.dependencies:
                visit(dependency)
            visiting.remove(extension_id)
            visited.add(extension_id)
            order.append(extension_id)
            if self._states[extension_id]["status"] == EXTENSION_DISABLED:
                self._set_state(extension_id, EXTENSION_DISCOVERED)

        for extension_id in requested:
            visit(extension_id)
        return order

    @staticmethod
    def _load_backend(spec: str) -> Any:
        module_name, separator, attribute = spec.partition(":")
        if not separator or not module_name or not attribute:
            raise ValueError(f"无效 backend entrypoint: {spec}")
        loaded = getattr(importlib.import_module(module_name), attribute)
        return loaded() if callable(loaded) else loaded

    def _load_and_register(self) -> None:
        for extension_id in self._load_order:
            if self._states[extension_id]["status"] == EXTENSION_DEGRADED:
                continue
            dependency_error = self._dependency_error(
                extension_id,
                required_status=EXTENSION_LOADED,
            )
            if dependency_error:
                self._set_state(
                    extension_id,
                    EXTENSION_BLOCKED,
                    dependency_error,
                )
                continue

            manifest = self._manifests[extension_id]
            pending = ExtensionContributions()
            try:
                extension = self._extensions.get(extension_id)
                if extension is None:
                    if manifest.base_path is not None:
                        plugin_path = str(manifest.base_path)
                        if plugin_path not in sys.path:
                            sys.path.append(plugin_path)
                    extension = (
                        self._load_backend(manifest.backend)
                        if manifest.backend
                        else _ResourceOnlyExtension(manifest)
                    )
                    self._extensions[extension_id] = extension
                registrar = ExtensionRegistrar(manifest, pending)
                self._register_manifest_resources(manifest, registrar)
                extension.register(registrar)
                revision = self.resource_revision(extension_id)
                if (
                    manifest.resource_prefix
                    or extension_id in self._applied_plugin_records
                ):
                    prepared = prepare_plugin_resources(
                        manifest,
                        pending.resource_paths,
                        runtime_root=self.plugin_runtime_resources_dir,
                        resolver=self.resource_resolver,
                        revision=revision,
                    )
                    pending.resource_paths = prepared.paths
                else:
                    self.resource_resolver.register(
                        build_plugin_resource_plan(
                            manifest,
                            pending.resource_paths,
                        )
                    )
                    pending.resource_paths = {
                        resource_type: [
                            OwnedPath(path.owner, path.path, revision)
                            for path in paths
                        ]
                        for resource_type, paths
                        in pending.resource_paths.items()
                    }
                self._validate_pending_resources(pending)
            except Exception as exc:
                self.resource_resolver.unregister(extension_id)
                shutil.rmtree(
                    self.plugin_runtime_resources_dir / extension_id,
                    ignore_errors=True,
                )
                self._extensions.pop(extension_id, None)
                self._set_state(
                    extension_id,
                    EXTENSION_DEGRADED,
                    f"扩展注册失败: {exc}",
                )
                logger.warning(
                    "扩展注册降级: %s: %s",
                    extension_id,
                    exc,
                    exc_info=True,
                )
                if self._strict_startup:
                    raise
                continue

            self._merge_contributions(pending)
            self._set_state(extension_id, EXTENSION_LOADED)

    def _set_state(self, extension_id: str, status: str, error: str = "") -> None:
        self._states[extension_id] = {"status": status, "error": error}

    def _dependency_error(
        self,
        extension_id: str,
        *,
        required_status: str,
    ) -> str:
        manifest = self._manifests[extension_id]
        for dependency in manifest.dependencies:
            state = self._states.get(dependency, {})
            if state.get("status") == required_status:
                continue
            dependency_status = state.get("status", "missing")
            dependency_error = state.get("error", "")
            details = f": {dependency_error}" if dependency_error else ""
            return (
                f"依赖扩展 {dependency} 未处于 {required_status} 状态 "
                f"({dependency_status}){details}"
            )
        return ""

    def _merge_contributions(self, pending: ExtensionContributions) -> None:
        self.contributions.routers.extend(pending.routers)
        self.contributions.middleware.extend(pending.middleware)
        self.contributions.tool_contributors.extend(pending.tool_contributors)
        self.contributions.prompt_context_providers.extend(
            pending.prompt_context_providers
        )
        self.contributions.session_hooks.extend(pending.session_hooks)
        self.contributions.health_checks.extend(pending.health_checks)
        for resource_type, paths in pending.resource_paths.items():
            self.contributions.resource_paths.setdefault(resource_type, []).extend(paths)

    def _validate_pending_resources(
        self,
        pending: ExtensionContributions,
    ) -> None:
        from .resources import LayeredJsonConfig

        unknown_resources = sorted(
            set(pending.resource_paths) - _SUPPORTED_RESOURCE_TYPES
        )
        if unknown_resources:
            raise ValueError(
                "未知资源类型: " + ", ".join(unknown_resources)
            )

        for resource_type, schema in _JSON_RESOURCE_SCHEMAS.items():
            pending_paths = pending.resource_paths.get(resource_type, [])
            if not pending_paths:
                continue
            filename, dict_sections, list_sections = schema
            existing_paths = self.contributions.resource_paths.get(resource_type, [])
            store = LayeredJsonConfig(
                self.config_dir / filename,
                [*existing_paths, *pending_paths],
                dict_sections=dict_sections,
                list_sections=list_sections,
            )
            store.validate_sources()

        self._validate_workflow_resources(pending)
        validate_file_resources(self.base_dir, self.contributions, pending)

    def _validate_workflow_resources(
        self,
        pending: ExtensionContributions,
    ) -> None:
        from src.workflow.definition import WorkflowDef

        pending_roots = pending.resource_paths.get("workflows", [])
        if not pending_roots:
            return

        claimed: dict[str, str] = {}
        existing_roots = self.contributions.resource_paths.get("workflows", [])
        for owned_root in [*existing_roots, *pending_roots]:
            if not owned_root.path.is_dir():
                raise ValueError(
                    f"扩展 {owned_root.owner} Workflow 资源必须是目录: "
                    f"{owned_root.path}"
                )
            for source_dir in sorted(owned_root.path.iterdir()):
                definition = source_dir / "definition.json"
                if not source_dir.is_dir() or not definition.exists():
                    continue
                existing_owner = claimed.get(source_dir.name)
                if existing_owner is not None:
                    raise ValueError(
                        f"扩展 Workflow 冲突: {source_dir.name} "
                        f"({existing_owner} vs {owned_root.owner})"
                    )
                document = json.loads(definition.read_text(encoding="utf-8"))
                if not isinstance(document, dict):
                    raise ValueError(f"Workflow definition 顶层必须是对象: {definition}")
                workflow = WorkflowDef.from_dict(document)
                if workflow.workflow_id != source_dir.name:
                    raise ValueError(
                        "Workflow definition ID 必须与目录名一致: "
                        f"{workflow.workflow_id} != {source_dir.name}"
                    )
                validation_errors = workflow.auto_pair_gateways() + workflow.validate()
                if validation_errors:
                    raise ValueError(
                        f"Workflow definition 校验失败: {definition}: "
                        + "；".join(validation_errors)
                    )
                claimed[source_dir.name] = owned_root.owner

                target_dir = self.workflows_dir / source_dir.name
                marker = self._read_marker(target_dir / ".extension.json")
                marker_owner = marker.get("owner")
                if marker_owner and marker_owner != owned_root.owner:
                    raise ValueError(
                        f"扩展 Workflow 冲突: {source_dir.name} "
                        f"({marker_owner} vs {owned_root.owner})"
                    )
                if (target_dir / "definition.json").exists() and not marker_owner:
                    raise ValueError(
                        f"扩展 Workflow 与用户/Core Workflow 冲突: "
                        f"{source_dir.name}"
                    )

    @staticmethod
    def _register_manifest_resources(
        manifest: ExtensionManifest,
        registrar: ExtensionRegistrar,
    ) -> None:
        for resource_type, configured_paths in manifest.resources.items():
            paths = configured_paths if isinstance(configured_paths, list) else [configured_paths]
            for configured_path in paths:
                registrar.add_resource_path(resource_type, configured_path)

    def resource_paths(self, resource_type: str) -> list[OwnedPath]:
        return list(self.contributions.resource_paths.get(resource_type, []))

    def resource_revision(self, extension_id: str) -> str:
        """Return the immutable revision backing one Plugin's resources."""
        record = self._applied_plugin_records.get(extension_id)
        if record is not None:
            revision = record.active_revision
            return f"{revision.commit}:{revision.content_sha256}"
        manifest = self._manifests.get(extension_id)
        if manifest is None:
            return ""
        return f"local:{manifest.version}"

    @property
    def routers(self) -> list[tuple[str, Any]]:
        return list(self.contributions.routers)

    @property
    def middleware(self) -> list[tuple[str, type, dict[str, Any]]]:
        return list(self.contributions.middleware)

    def is_enabled(self, extension_id: str) -> bool:
        return extension_id in self._load_order

    def is_running(self, extension_id: str) -> bool:
        self._plugin_process_statuses(extension_id)
        return self._states.get(extension_id, {}).get("status") == EXTENSION_RUNNING

    def get_state(self, extension_id: str) -> dict[str, Any]:
        state = self._states.get(extension_id)
        if state is None:
            return {"status": "missing", "error": "扩展不存在"}
        return dict(state)

    async def _register_owner_tools(
        self,
        owner: str,
        runtime: CoreRuntime,
    ) -> None:
        if owner in self._registered_tool_owners:
            return
        owner_runtime = self._owner_runtime(owner, runtime)
        for contributor_owner, contributor in self.contributions.tool_contributors:
            if contributor_owner != owner:
                continue
            result = contributor(owner_runtime.tool_registry, owner_runtime)
            if inspect.isawaitable(result):
                await result
        self._registered_tool_owners.add(owner)
        logger.info("扩展工具已注册: %s", owner)

    @staticmethod
    def _unregister_owner_tools(owner: str, runtime: CoreRuntime) -> None:
        unregister = getattr(runtime.tool_registry, "unregister_owner", None)
        if unregister is None:
            logger.warning("ToolRegistry 不支持按 owner 回滚: %s", owner)
            return
        unregister(owner)

    async def _deactivate_owner(self, owner: str, runtime: CoreRuntime) -> str:
        self._unregister_owner_tools(owner, runtime)
        self._registered_tool_owners.discard(owner)
        self.process_manager.begin_stop(owner)

        cleanup_errors: list[str] = []
        if owner in self._started_extensions:
            self._started_extensions.discard(owner)
            try:
                await self._extensions[owner].stop()
            except Exception as exc:
                logger.warning("扩展失败清理异常: %s: %s", owner, exc, exc_info=True)
                cleanup_errors.append(f"Extension 清理失败: {exc}")
        try:
            await self.process_manager.stop(owner)
        except Exception as exc:
            logger.warning("Plugin 进程清理异常: %s: %s", owner, exc, exc_info=True)
            cleanup_errors.append(f"进程清理失败: {exc}")
        return "".join(f"；{message}" for message in cleanup_errors)

    async def _degrade_runtime_extension(
        self,
        owner: str,
        exc: Exception,
        runtime: CoreRuntime,
    ) -> None:
        cleanup_error = await self._deactivate_owner(owner, runtime)
        self._set_state(owner, EXTENSION_DEGRADED, f"{exc}{cleanup_error}")
        reload_runtime_resource_managers(runtime)
        logger.warning("扩展降级: %s: %s", owner, exc, exc_info=True)

    async def _handle_managed_process_exit(
        self,
        owner: str,
        process_status: dict[str, Any],
    ) -> None:
        await handle_managed_process_exit(
            self, owner, process_status, EXTENSION_RUNNING,
            EXTENSION_STARTING, EXTENSION_BLOCKED,
        )

    async def _run_health_checks(self, owner: str, runtime: CoreRuntime) -> None:
        owner_runtime = self._owner_runtime(owner, runtime)
        for check_owner, check in self.contributions.health_checks:
            if check_owner != owner:
                continue
            result = check(owner_runtime)
            if inspect.isawaitable(result):
                result = await result
            if result is None or result is True:
                continue
            if isinstance(result, HealthCheckResult):
                if result.healthy:
                    continue
                message = result.message or "健康检查未通过"
            elif result is False:
                message = "健康检查未通过"
            else:
                raise TypeError(f"扩展 {owner} 返回了无效健康检查结果: {result!r}")
            raise RuntimeError(message)

    async def start(self, runtime: CoreRuntime) -> None:
        self._runtime = runtime
        self._stopping = False
        for extension_id in self._load_order:
            if self._states[extension_id]["status"] != EXTENSION_LOADED:
                continue
            dependency_error = self._dependency_error(
                extension_id,
                required_status=EXTENSION_RUNNING,
            )
            if dependency_error:
                self._set_state(
                    extension_id,
                    EXTENSION_BLOCKED,
                    dependency_error,
                )
                continue

            extension = self._extensions[extension_id]
            self._set_state(extension_id, EXTENSION_STARTING)
            starting = asyncio.Event()
            self._starting_extensions[extension_id] = starting
            try:
                manifest = self._manifests[extension_id]
                lifecycle = (
                    load_extension_lifecycle(
                        manifest.base_path / "extension.toml"
                    )
                    if manifest.base_path is not None
                    else None
                )
                record = self._applied_plugin_records.get(extension_id)
                lifecycle_revision = (
                    record.active_revision.commit
                    if record is not None
                    else manifest.version
                )
                await run_extension_lifecycle(
                    lifecycle,
                    owner=extension_id,
                    plugin_dir=manifest.base_path or self.base_dir,
                    config_file=self.applied_plugin_config_store.path_for(
                        extension_id
                    ),
                    data_dir=self.plugin_data_dir / extension_id,
                    base_dir=self.base_dir,
                    plugin_revision=lifecycle_revision,
                    environment=self.plugin_environment(extension_id),
                )
                ensure_runtime_start_allowed(
                    self, extension_id, EXTENSION_RUNNING, EXTENSION_BLOCKED
                )
                await start_manifest_processes(
                    self.process_manager,
                    manifest,
                    owner=extension_id,
                    base_dir=self.base_dir,
                    config_file=self.applied_plugin_config_store.path_for(extension_id),
                    data_dir=self.plugin_data_dir / extension_id,
                )
                ensure_runtime_start_allowed(
                    self, extension_id, EXTENSION_RUNNING, EXTENSION_BLOCKED
                )
                self._started_extensions.add(extension_id)
                await extension.start(self._owner_runtime(extension_id, runtime))
                ensure_runtime_start_allowed(
                    self, extension_id, EXTENSION_RUNNING, EXTENSION_BLOCKED
                )
                await self._run_health_checks(extension_id, runtime)
                ensure_runtime_start_allowed(
                    self, extension_id, EXTENSION_RUNNING, EXTENSION_BLOCKED
                )
                await self._register_owner_tools(extension_id, runtime)
                ensure_runtime_start_allowed(
                    self, extension_id, EXTENSION_RUNNING, EXTENSION_BLOCKED
                )
                self._set_state(extension_id, EXTENSION_RUNNING)
            except RuntimeStartBlocked:
                await cleanup_blocked_start(
                    self, extension_id, runtime, EXTENSION_BLOCKED
                )
            except Exception as exc:
                await self._degrade_runtime_extension(extension_id, exc, runtime)
                if self._strict_startup:
                    raise
            finally:
                starting.set()
                self._starting_extensions.pop(extension_id, None)

    def plugin_environment(self, owner: str) -> dict[str, str]:
        manifest = self._manifests[owner]
        schema = None
        if manifest.settings_schema and manifest.base_path is not None:
            schema = load_settings_schema(
                manifest.base_path,
                manifest.settings_schema,
            )
        return settings_environment(
            self._applied_plugin_configs.get(owner, {}),
            schema=schema,
        )

    def _owner_runtime(self, owner: str, runtime: CoreRuntime) -> CoreRuntime:
        manifest = self._manifests[owner]
        services = dict(runtime.services)
        services.pop("resource_resolver", None)
        services.update({
            "plugin_config": dict(self._applied_plugin_configs.get(owner, {})),
            "plugin_config_file": self.applied_plugin_config_store.path_for(owner),
            "plugin_data_dir": self.plugin_data_dir / owner,
            "plugin_dir": manifest.base_path,
        })
        return replace(
            runtime,
            tool_registry=ExtensionToolRegistry(runtime.tool_registry, owner),
            services=services,
            resource_owner=owner, resource_dependencies=manifest.dependencies,
            resource_resolver=self.resource_resolver,
        )

    async def stop(self) -> None:
        self._stopping = True
        runtime = self._runtime
        for extension_id in reversed(self._load_order):
            if runtime is not None:
                self._unregister_owner_tools(extension_id, runtime)
                self._registered_tool_owners.discard(extension_id)
            if extension_id not in self._started_extensions:
                if (
                    extension_id in self._extensions
                    and self._states[extension_id]["status"] == EXTENSION_BLOCKED
                ):
                    self._set_state(extension_id, EXTENSION_LOADED)
                continue
            self._started_extensions.discard(extension_id)
            extension = self._extensions[extension_id]
            try:
                self.process_manager.begin_stop(extension_id)
                await extension.stop()
                await self.process_manager.stop(extension_id)
                self._set_state(extension_id, EXTENSION_LOADED)
            except Exception as exc:
                try:
                    await self.process_manager.stop(extension_id)
                except Exception:
                    logger.warning(
                        "Plugin 进程关闭失败: %s",
                        extension_id,
                        exc_info=True,
                    )
                self._set_state(extension_id, EXTENSION_DEGRADED, f"扩展关闭失败: {exc}")
                logger.warning("扩展关闭失败: %s: %s", extension_id, exc, exc_info=True)
        self._runtime = None
        shutil.rmtree(self.applied_plugin_config_store.root, ignore_errors=True)

    async def build_prompt_context(self, request: PromptContextRequest) -> str:
        contributions: list[PromptContribution] = []
        for owner, provider in self.contributions.prompt_context_providers:
            if self._states.get(owner, {}).get("status") != "running":
                continue
            try:
                value = await provider.provide(request)
                if isinstance(value, str):
                    value = PromptContribution(value)
                if value and value.content.strip():
                    contributions.append(value)
            except Exception:
                logger.warning("扩展 Prompt 上下文获取失败: %s", owner, exc_info=True)
        contributions.sort(key=lambda item: item.order)
        return "\n\n".join(item.content.strip() for item in contributions)

    async def notify_session_end(self, session: Any) -> None:
        calls = []
        for owner, hook in self.contributions.session_hooks:
            if self._states.get(owner, {}).get("status") != "running":
                continue
            async def invoke(current_owner: str = owner, current_hook: Any = hook) -> None:
                try:
                    await current_hook.on_session_end(session)
                except Exception:
                    logger.debug("扩展 Session Hook 失败: %s", current_owner, exc_info=True)
            calls.append(invoke())
        if calls:
            await asyncio.gather(*calls)

    def get_statuses(self) -> list[dict[str, Any]]:
        result = []
        for extension_id, manifest in sorted(self._manifests.items()):
            processes = self._plugin_process_statuses(extension_id)
            state = self._states[extension_id]
            result.append({
                "id": extension_id,
                "name": manifest.name,
                "version": manifest.version,
                "description": manifest.description,
                "enabled": extension_id in self._load_order,
                "status": state["status"],
                "error": state["error"],
                "dependencies": list(manifest.dependencies),
                "capabilities": list(manifest.capabilities),
                "resource_prefix": manifest.resource_prefix,
                "frontend": manifest.frontend,
                "processes": processes,
            })
        return result

    def _plugin_process_statuses(self, extension_id: str) -> list[dict[str, Any]]:
        return self.process_manager.statuses(extension_id)

    def provision_workflows(self, target_root: Path) -> list[str]:
        """Synchronize extension workflows and report preserved orphan files."""
        return provision_plugin_workflows(
            self.resource_paths("workflows"),
            target_root,
            active_owners=set(self._load_order),
        )

    @staticmethod
    def _read_marker(path: Path) -> dict:
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}

    def workflow_owner_enabled(self, workflow_dir: Path) -> bool:
        marker = self._read_marker(workflow_dir / ".extension.json")
        owner = marker.get("owner")
        return marker.get("active", True) is not False and (
            not owner or self.is_running(owner)
        )

    def workflow_environment(self, workflow_id: str) -> dict[str, str]:
        """Return the owning Plugin's applied environment for one Workflow."""
        workflow_dir = self.workflows_dir / workflow_id
        try:
            workflow_dir.resolve().relative_to(self.workflows_dir)
        except ValueError:
            return {}
        marker = self._read_marker(workflow_dir / ".extension.json")
        owner = str(marker.get("owner", ""))
        if not owner or not self.is_running(owner):
            return {}
        return self.plugin_environment(owner)
