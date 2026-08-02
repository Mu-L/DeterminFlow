"""Registration surface used by extension implementations."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .models import ExtensionManifest


@dataclass(frozen=True)
class OwnedPath:
    owner: str
    path: Path
    revision: str = ""


@dataclass
class ExtensionContributions:
    routers: list[tuple[str, Any]] = field(default_factory=list)
    middleware: list[tuple[str, type, dict[str, Any]]] = field(default_factory=list)
    tool_contributors: list[tuple[str, Callable]] = field(default_factory=list)
    prompt_context_providers: list[tuple[str, Any]] = field(default_factory=list)
    session_hooks: list[tuple[str, Any]] = field(default_factory=list)
    health_checks: list[tuple[str, Callable]] = field(default_factory=list)
    resource_paths: dict[str, list[OwnedPath]] = field(default_factory=dict)


class ExtensionRegistrar:
    """Collects declarations without performing runtime I/O."""

    def __init__(
        self,
        manifest: ExtensionManifest,
        contributions: ExtensionContributions,
    ):
        self.manifest = manifest
        self._contributions = contributions

    @property
    def owner(self) -> str:
        return self.manifest.extension_id

    def add_router(self, router: Any) -> None:
        self._contributions.routers.append((self.owner, router))

    def add_middleware(self, middleware: type, **options: Any) -> None:
        self._contributions.middleware.append((self.owner, middleware, options))

    def add_tool_contributor(self, contributor: Callable) -> None:
        self._contributions.tool_contributors.append((self.owner, contributor))

    def add_prompt_context_provider(self, provider: Any) -> None:
        self._contributions.prompt_context_providers.append((self.owner, provider))

    def add_session_hook(self, hook: Any) -> None:
        self._contributions.session_hooks.append((self.owner, hook))

    def add_health_check(self, check: Callable) -> None:
        self._contributions.health_checks.append((self.owner, check))

    def add_resource_path(self, resource_type: str, path: str | Path) -> None:
        base_path = self.manifest.base_path
        resolved = Path(path)
        if not resolved.is_absolute():
            if base_path is None:
                raise ValueError(f"扩展 {self.owner} 缺少 base_path，无法解析资源: {path}")
            resolved = base_path / resolved
        resolved = resolved.resolve()
        if base_path is not None:
            try:
                resolved.relative_to(base_path.resolve())
            except ValueError as exc:
                raise ValueError(
                    f"扩展 {self.owner} 资源必须位于扩展目录内: {resolved}"
                ) from exc
        if not resolved.exists():
            raise FileNotFoundError(f"扩展 {self.owner} 资源不存在: {resolved}")
        self._contributions.resource_paths.setdefault(resource_type, []).append(
            OwnedPath(self.owner, resolved)
        )
