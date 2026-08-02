"""Runtime cleanup helpers for failed trusted Plugins."""

from __future__ import annotations

import logging
from typing import Any
from typing import Protocol

from src.extension_api.models import CoreRuntime, ExtensionManifest

logger = logging.getLogger(__name__)

_RESOURCE_MANAGER_SERVICES = (
    "agent_config_manager",
    "prompt_manager",
    "skill_manager",
    "rule_manager",
)


class RuntimeFailureHost(Protocol):
    _load_order: list[str]
    _manifests: dict[str, ExtensionManifest]
    _states: dict[str, dict[str, Any]]
    _runtime: CoreRuntime | None
    _starting_extensions: dict[str, Any]
    _stopping: bool
    _degrading_extensions: set[str]

    def _dependency_error(self, owner: str, *, required_status: str) -> str: ...
    def _set_state(self, owner: str, status: str, error: str = "") -> None: ...
    async def _deactivate_owner(
        self,
        owner: str,
        runtime: CoreRuntime,
    ) -> str: ...
    async def _degrade_runtime_extension(
        self,
        owner: str,
        exc: Exception,
        runtime: CoreRuntime,
    ) -> None: ...


class RuntimeStartBlocked(RuntimeError):
    """Raised when a dependency fails while an Extension is starting."""


def reload_runtime_resource_managers(runtime: CoreRuntime) -> None:
    """Refresh available Core resource caches after owner state changes."""
    for service_name in _RESOURCE_MANAGER_SERVICES:
        service = runtime.services.get(service_name)
        if service is None:
            continue
        try:
            if service.reload() is False:
                raise RuntimeError("reload returned false")
        except Exception as exc:
            logger.warning(
                "扩展资源缓存刷新失败: %s: %s",
                service_name,
                exc,
                exc_info=True,
            )


async def block_runtime_dependents(
    host: RuntimeFailureHost,
    owner: str,
    runtime: CoreRuntime,
    running_status: str,
    starting_status: str,
    blocked_status: str,
) -> None:
    """Block starting dependents and stop running ones in dependency order."""
    ordered = tuple(host._load_order)
    affected = {owner}
    for candidate in ordered:
        if affected.intersection(host._manifests[candidate].dependencies):
            affected.add(candidate)
    for dependent in ordered:
        if (
            dependent != owner
            and dependent in affected
            and host._states[dependent]["status"] == starting_status
        ):
            host._set_state(
                dependent,
                blocked_status,
                f"依赖扩展 {owner} 运行时失败",
            )
    for dependent in reversed(ordered):
        if (
            dependent == owner
            or dependent not in affected
            or host._states[dependent]["status"] != running_status
        ):
            continue
        cleanup_error = await host._deactivate_owner(dependent, runtime)
        host._set_state(
            dependent,
            blocked_status,
            f"依赖扩展 {owner} 运行时失败{cleanup_error}",
        )


def ensure_runtime_start_allowed(
    host: RuntimeFailureHost,
    owner: str,
    running_status: str,
    blocked_status: str,
) -> None:
    """Fail one startup checkpoint without overwriting an existing block."""
    state = host._states[owner]
    if state["status"] == blocked_status:
        raise RuntimeStartBlocked(state["error"])
    error = host._dependency_error(owner, required_status=running_status)
    if error:
        host._set_state(owner, blocked_status, error)
        raise RuntimeStartBlocked(error)


async def cleanup_blocked_start(
    host: RuntimeFailureHost,
    owner: str,
    runtime: CoreRuntime,
    blocked_status: str,
) -> None:
    """Clean resources after startup returns without changing blocked state."""
    cleanup_error = await host._deactivate_owner(owner, runtime)
    if cleanup_error:
        error = host._states[owner]["error"]
        host._set_state(owner, blocked_status, f"{error}{cleanup_error}")


async def handle_managed_process_exit(
    host: RuntimeFailureHost,
    owner: str,
    process_status: dict[str, Any],
    running_status: str,
    starting_status: str,
    blocked_status: str,
) -> None:
    """Serialize one process crash with startup and owner cleanup."""
    starting = host._starting_extensions.get(owner)
    if starting is not None:
        await starting.wait()
    runtime = host._runtime
    if (
        runtime is None
        or host._stopping
        or owner in host._degrading_extensions
        or host._states.get(owner, {}).get("status") != running_status
    ):
        return
    error = process_status.get("error") or (
        f"process {process_status.get('process_id', 'unknown')} exited"
    )
    host._degrading_extensions.add(owner)
    try:
        await block_runtime_dependents(
            host, owner, runtime, running_status, starting_status, blocked_status
        )
        await host._degrade_runtime_extension(
            owner,
            RuntimeError(f"Plugin 托管进程异常退出: {error}"),
            runtime,
        )
    finally:
        host._degrading_extensions.discard(owner)
