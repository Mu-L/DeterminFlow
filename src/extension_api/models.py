"""Public data contracts for project-level extensions."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from .workflow import WorkflowRuntime


EXTENSION_API_VERSION = "1"


@dataclass(frozen=True)
class ExtensionProcess:
    """A child process declared by an extension manifest."""

    process_id: str
    command: tuple[str, ...]
    working_directory: str = "."
    environment: dict[str, str] = field(default_factory=dict)
    healthcheck_url: str = ""
    start_timeout_seconds: float = 30.0
    stop_timeout_seconds: float = 10.0


@dataclass(frozen=True)
class ExtensionPage:
    """A lightweight static management page bundled with an extension."""

    label: str
    static_dir: str
    entrypoint: str = "index.html"


@dataclass(frozen=True)
class ExtensionManifest:
    """Declarative identity and compatibility metadata for an extension."""

    extension_id: str
    name: str
    version: str
    api_version: str = EXTENSION_API_VERSION
    description: str = ""
    resource_prefix: str = ""
    dependencies: tuple[str, ...] = ()
    backend: str = ""
    frontend: str = ""
    capabilities: tuple[str, ...] = ()
    base_path: Path | None = field(default=None, compare=False)
    resources: dict[str, Any] = field(default_factory=dict, compare=False)
    requirements: str = ""
    settings_schema: str = ""
    page: ExtensionPage | None = None
    processes: tuple[ExtensionProcess, ...] = ()


@dataclass
class CoreRuntime:
    """Stable facade passed to extensions after the core has initialized."""

    app: Any
    session_manager: Any
    workflow_runtime: WorkflowRuntime
    tool_registry: Any
    event_publisher: Any
    services: dict[str, Any] = field(default_factory=dict)
    resource_owner: str = ""
    resource_dependencies: tuple[str, ...] = ()
    resource_resolver: Any = None

    def get_service(self, name: str, default: Any = None) -> Any:
        return self.services.get(name, default)

    def resolve_resource(
        self,
        resource_type: str,
        local_id: str,
        *,
        plugin_id: str | None = None,
    ) -> str:
        """Resolve one exact Plugin-local resource ID to its effective ID."""
        owner = str(plugin_id or self.resource_owner).strip()
        if not owner:
            raise RuntimeError("resolve_resource 缺少 Plugin owner")
        requester = str(self.resource_owner).strip()
        dependencies = {
            str(dependency).strip()
            for dependency in self.resource_dependencies
        }
        if requester and owner != requester and owner not in dependencies:
            raise RuntimeError(
                f"Plugin {requester!r} 未声明资源依赖 {owner!r}"
            )
        if self.resource_resolver is None:
            raise RuntimeError("CoreRuntime 未配置 Plugin resource resolver")
        return self.resource_resolver.resolve(owner, resource_type, local_id)


@dataclass(frozen=True)
class PromptContextRequest:
    """Context available before a main or workflow-main prompt is built."""

    agent_type: str
    agent_definition: Any = None
    session_type: str = "main"
    workflow_id: str = ""


@dataclass(frozen=True)
class PromptContribution:
    """A rendered prompt fragment supplied by an enabled extension."""

    content: str
    order: int = 100


@dataclass(frozen=True)
class HealthCheckResult:
    """Result returned by an extension health check."""

    healthy: bool
    message: str = ""


@runtime_checkable
class Extension(Protocol):
    manifest: ExtensionManifest

    def register(self, registrar: Any) -> None: ...

    async def start(self, runtime: CoreRuntime) -> None: ...

    async def stop(self) -> None: ...


@runtime_checkable
class PromptContextProvider(Protocol):
    async def provide(self, request: PromptContextRequest) -> PromptContribution | None: ...


@runtime_checkable
class SessionLifecycleHook(Protocol):
    async def on_session_end(self, session: Any) -> None: ...
