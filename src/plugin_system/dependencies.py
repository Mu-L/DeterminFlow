"""Install declared Plugin dependencies into the shared Core environment."""

from __future__ import annotations

import shutil
import subprocess
import sys
from collections.abc import Callable, Iterable
from pathlib import Path

from src.extension_api.models import ExtensionManifest


class PluginDependencyError(RuntimeError):
    """Raised when a declared shared-environment dependency install fails."""


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
