"""Bridge manifest process declarations to the Plugin ProcessManager."""

from __future__ import annotations

import sys
from pathlib import Path

from src.extension_api.models import ExtensionManifest
from src.plugin_system import ProcessHealthCheck, ProcessManager, ProcessSpec


async def start_manifest_processes(
    process_manager: ProcessManager,
    manifest: ExtensionManifest,
    *,
    owner: str,
    base_dir: Path,
    config_file: Path,
    data_dir: Path,
) -> None:
    if not manifest.processes:
        return
    if manifest.base_path is None:
        raise RuntimeError(f"Plugin {owner} 子进程缺少 base_path")
    data_dir.mkdir(parents=True, exist_ok=True)
    specs = [
        ProcessSpec(
            process_id=process.process_id,
            argv=process.command,
            cwd=process.working_directory,
            env=process.environment,
            health=ProcessHealthCheck(
                kind="http" if process.healthcheck_url else "alive",
                url=process.healthcheck_url,
            ),
            startup_timeout_seconds=process.start_timeout_seconds,
            shutdown_timeout_seconds=process.stop_timeout_seconds,
        )
        for process in manifest.processes
    ]
    await process_manager.start(
        owner,
        specs,
        placeholders={
            "PYTHON": sys.executable,
            "PLUGIN_DIR": str(manifest.base_path),
            "CONFIG_FILE": str(config_file),
            "DATA_DIR": str(data_dir),
            "BASE_DIR": str(base_dir),
        },
    )
