"""Install declared Plugin dependencies into the shared Core environment."""

from __future__ import annotations

import shutil
import subprocess
import sys
from collections.abc import Callable, Iterable
from importlib import metadata
from pathlib import Path

from packaging.requirements import InvalidRequirement, Requirement

from src.extension_api.models import ExtensionManifest


class PluginDependencyError(RuntimeError):
    """Raised when a declared shared-environment dependency install fails."""


def _requirements_are_satisfied(requirements: Path) -> bool:
    """Avoid invoking pip when every simple requirement is already installed."""
    for raw_line in requirements.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            requirement = Requirement(line)
        except InvalidRequirement:
            return False
        if requirement.url or requirement.extras:
            return False
        if requirement.marker is not None and not requirement.marker.evaluate():
            continue
        try:
            installed_version = metadata.version(requirement.name)
        except metadata.PackageNotFoundError:
            return False
        if requirement.specifier and installed_version not in requirement.specifier:
            return False
    return True


def install_plugin_requirements(
    manifest: ExtensionManifest,
    *,
    timeout_seconds: int = 600,
) -> None:
    if not manifest.requirements:
        return
    if manifest.base_path is None:
        raise PluginDependencyError("Plugin requirements 缺少 base_path")
    plugin_root = manifest.base_path.resolve()
    requirements = (plugin_root / manifest.requirements).resolve()
    try:
        requirements.relative_to(plugin_root)
    except ValueError as exc:
        raise PluginDependencyError(
            "Plugin requirements 必须位于 Plugin 目录内"
        ) from exc
    if not requirements.is_file():
        raise PluginDependencyError(
            f"Plugin requirements 不存在: {manifest.requirements}"
        )
    if _requirements_are_satisfied(requirements):
        return

    uv_binary = shutil.which("uv")
    command = (
        [
            uv_binary,
            "pip",
            "install",
            "--python",
            sys.executable,
            "-r",
            str(requirements),
        ]
        if uv_binary
        else [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "-r",
            str(requirements),
        ]
    )
    try:
        subprocess.run(
            command,
            cwd=plugin_root,
            check=True,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return_code = getattr(exc, "returncode", "unknown")
        raise PluginDependencyError(
            f"Plugin 共享依赖安装失败，exit_code={return_code}"
        ) from exc


def install_applied_plugin_requirements(
    manifests: dict[str, ExtensionManifest],
    owners: Iterable[str],
    *,
    on_error: Callable[[str, Exception], None] | None = None,
    strict: bool = False,
) -> None:
    """Install dependencies only while applying a cold-start snapshot."""
    for owner in owners:
        try:
            install_plugin_requirements(manifests[owner])
        except PluginDependencyError as exc:
            if on_error is not None:
                on_error(owner, exc)
            if strict:
                raise
