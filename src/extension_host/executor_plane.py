"""Execution-only Extension plane used by Workflow Executor processes."""

from __future__ import annotations

import inspect
import logging
from typing import Any

from .runtime_failure import reload_runtime_resource_managers


logger = logging.getLogger(__name__)


class ExtensionExecutorPlaneMixin:
    @staticmethod
    def _explicit_hook(extension: Any, name: str) -> Any | None:
        defined_on_type = any(
            name in cls.__dict__
            for cls in type(extension).__mro__
            if cls is not object
        )
        defined_on_instance = name in getattr(extension, "__dict__", {})
        if not defined_on_type and not defined_on_instance:
            return None
        hook = getattr(extension, name, None)
        return hook if callable(hook) else None

    def _runtime_hooks_enabled(self, owner: str) -> bool:
        if self._states.get(owner, {}).get("status") != "running":
            return False
        if owner in self._executor_active_owners:
            return owner in self._executor_runtime_owners
        return True

    async def start_executor(self, runtime) -> None:
        """Enable loaded file resources without Controller lifecycle or HTTP."""
        self._runtime = runtime
        self._stopping = False
        for extension_id in self._load_order:
            if self._states[extension_id]["status"] != "loaded":
                continue
            dependency_error = self._dependency_error(
                extension_id,
                required_status="running",
            )
            if dependency_error:
                self._set_state(extension_id, "blocked", dependency_error)
                continue

            extension = self._extensions[extension_id]
            self._set_state(extension_id, "starting")
            try:
                start_hook = self._explicit_hook(extension, "start_executor")
                if start_hook is not None:
                    self._executor_started_extensions.add(extension_id)
                    result = start_hook(self._owner_runtime(extension_id, runtime))
                    if inspect.isawaitable(result):
                        await result
                    self._executor_registered_tool_owners.add(extension_id)
                    await self._register_owner_tools(extension_id, runtime)
                    self._executor_runtime_owners.add(extension_id)
                self._executor_active_owners.add(extension_id)
                self._set_state(extension_id, "running")
            except Exception as exc:
                await self._degrade_executor_extension(extension_id, exc, runtime)
                if self._strict_startup:
                    raise

    async def _deactivate_executor_owner(self, owner: str, runtime) -> str:
        if runtime is not None and owner in self._executor_registered_tool_owners:
            self._unregister_owner_tools(owner, runtime)
            self._registered_tool_owners.discard(owner)
            self._executor_registered_tool_owners.discard(owner)

        cleanup_errors: list[str] = []
        if owner in self._executor_started_extensions:
            self._executor_started_extensions.discard(owner)
            stop_hook = self._explicit_hook(
                self._extensions[owner], "stop_executor"
            )
            if stop_hook is not None:
                try:
                    result = stop_hook()
                    if inspect.isawaitable(result):
                        await result
                except Exception as exc:
                    logger.warning(
                        "扩展 Executor 失败清理异常: %s: %s",
                        owner,
                        exc,
                        exc_info=True,
                    )
                    cleanup_errors.append(f"Extension Executor 清理失败: {exc}")
        self._executor_runtime_owners.discard(owner)
        self._executor_active_owners.discard(owner)
        return "".join(f"；{message}" for message in cleanup_errors)

    async def _degrade_executor_extension(self, owner: str, exc: Exception, runtime) -> None:
        cleanup_error = await self._deactivate_executor_owner(owner, runtime)
        self._set_state(owner, "degraded", f"{exc}{cleanup_error}")
        reload_runtime_resource_managers(runtime)
        logger.warning("扩展 Executor 降级: %s: %s", owner, exc, exc_info=True)

    async def stop_executor(self) -> None:
        """Revoke Executor-plane tools and hooks without stopping processes."""
        self._stopping = True
        runtime = self._runtime
        for extension_id in reversed(self._load_order):
            was_executor_owner = (
                extension_id in self._executor_active_owners
                or extension_id in self._executor_started_extensions
            )
            if not was_executor_owner:
                if (
                    extension_id in self._extensions
                    and self._states[extension_id]["status"] == "blocked"
                ):
                    self._set_state(extension_id, "loaded")
                continue
            try:
                cleanup_error = await self._deactivate_executor_owner(
                    extension_id, runtime
                )
                if cleanup_error:
                    self._set_state(
                        extension_id,
                        "degraded",
                        f"扩展 Executor 关闭失败{cleanup_error}",
                    )
                    continue
                self._set_state(extension_id, "loaded")
            except Exception as exc:
                self._set_state(
                    extension_id,
                    "degraded",
                    f"扩展 Executor 关闭失败: {exc}",
                )
                logger.warning(
                    "扩展 Executor 关闭失败: %s: %s",
                    extension_id,
                    exc,
                    exc_info=True,
                )
        self._runtime = None
