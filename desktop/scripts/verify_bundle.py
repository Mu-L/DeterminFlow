"""Verify desktop runtime and optional NSIS output before artifact upload."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import logging
import sys
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from desktop.scripts.stage_defaults import SENSITIVE_KEYS
from src.plugin_system.release import load_release_plugin
from src.plugin_system.store import PluginStore


LOGGER = logging.getLogger("desktop.verify_bundle")
WINDOWS_GUI_SUBSYSTEM = 2


def _inspect_secrets(value: Any, location: str) -> list[str]:
    findings: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            child_location = f"{location}.{key}"
            if key.lower() in SENSITIVE_KEYS and isinstance(child, str):
                if child and not (child.startswith("${") and child.endswith("}")):
                    findings.append(child_location)
            findings.extend(_inspect_secrets(child, child_location))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            findings.extend(_inspect_secrets(child, f"{location}[{index}]"))
    return findings


def verify_defaults(config_dir: Path) -> None:
    required = {
        "extensions.json",
        "mcp_servers.json",
        "models_config.example.json",
        "models_config.json",
        "plugin-sources.json",
    }
    names = {path.name for path in config_dir.glob("*.json")}
    missing = required - names
    if missing:
        raise RuntimeError(f"桌面默认配置缺失: {', '.join(sorted(missing))}")

    findings: list[str] = []
    combined = ""
    for path in sorted(config_dir.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        findings.extend(_inspect_secrets(payload, path.name))
        combined += path.read_text(encoding="utf-8")
    if findings:
        raise RuntimeError(f"桌面默认配置包含明文凭据: {', '.join(findings)}")
    forbidden = ("ssh://git@localhost", "AI Company Core")
    leaked = [item for item in forbidden if item in combined]
    if leaked:
        raise RuntimeError(f"桌面默认配置包含私有边界内容: {', '.join(leaked)}")


def verify_bundled_plugins(snapshot_dir: Path) -> list[str]:
    if not snapshot_dir.is_dir():
        raise RuntimeError(f"桌面 Full Plugin 快照不存在: {snapshot_dir}")
    metadata_path = snapshot_dir / "release-plugins.json"
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        plugin_ids = sorted(metadata["plugins"])
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as error:
        raise RuntimeError("桌面 Full Plugin 快照元数据无效") from error
    if not plugin_ids:
        raise RuntimeError("桌面 Full Plugin 快照不能为空")
    store = PluginStore(snapshot_dir)
    if sorted(store.read_lock()) != plugin_ids:
        raise RuntimeError("桌面 Full Plugin 快照锁与元数据不一致")
    for plugin_id in plugin_ids:
        store.verify(plugin_id)
        load_release_plugin(snapshot_dir, plugin_id)
    LOGGER.info("桌面 Full Plugin 快照验证通过: %s", ", ".join(plugin_ids))
    return plugin_ids


def verify_windows_gui_executable(executable: Path) -> None:
    """Require a Windows PE GUI subsystem so release startup has no console."""
    image = executable.read_bytes()
    if len(image) < 64 or image[:2] != b"MZ":
        raise RuntimeError(f"桌面程序不是有效的 Windows PE 文件: {executable}")

    pe_offset = int.from_bytes(image[0x3C:0x40], "little")
    optional_header = pe_offset + 24
    subsystem_offset = optional_header + 68
    if (
        subsystem_offset + 2 > len(image)
        or image[pe_offset : pe_offset + 4] != b"PE\x00\x00"
        or int.from_bytes(image[optional_header : optional_header + 2], "little")
        not in {0x10B, 0x20B}
    ):
        raise RuntimeError(f"桌面程序的 Windows PE Header 无效: {executable}")

    subsystem = int.from_bytes(
        image[subsystem_offset : subsystem_offset + 2], "little"
    )
    if subsystem != WINDOWS_GUI_SUBSYSTEM:
        raise RuntimeError(
            f"桌面程序必须使用 Windows GUI Subsystem，实际值={subsystem}: {executable}"
        )
    LOGGER.info("Windows GUI Subsystem 验证通过: %s", executable)


def write_checksum(installer: Path) -> Path:
    digest = hashlib.sha256(installer.read_bytes()).hexdigest()
    checksum_path = installer.with_suffix(installer.suffix + ".sha256")
    checksum_path.write_bytes(f"{digest}  {installer.name}\n".encode("ascii"))
    return checksum_path


def verify_updater_signature(signature: Path) -> None:
    if not signature.is_file():
        raise RuntimeError(f"更新签名不存在: {signature}")
    encoded = signature.read_text(encoding="utf-8").strip()
    try:
        decoded = base64.b64decode(encoded, validate=True)
    except ValueError as error:
        raise RuntimeError(f"更新签名不是有效的 Base64: {signature}") from error
    if len(decoded) < 64:
        raise RuntimeError(f"更新签名内容过短: {signature}")
    LOGGER.info("Tauri 更新签名验证通过: %s", signature)


def main() -> int:
    repo_root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser()
    parser.add_argument("--installer", type=Path)
    parser.add_argument("--updater-signature", type=Path)
    parser.add_argument("--desktop-executable", type=Path)
    parser.add_argument("--expected-flavor", choices=("core", "full"))
    options = parser.parse_args()

    verify_defaults(repo_root / "desktop" / "generated" / "default-config")
    executable_name = "determinflow-backend.exe" if sys.platform == "win32" else "determinflow-backend"
    backend = repo_root / "desktop" / "runtime" / "backend" / executable_name
    if not backend.is_file():
        raise RuntimeError(f"桌面后端不存在: {backend}")

    if options.expected_flavor:
        runtime_snapshot = backend.parent / "_internal" / "bundled-plugins"
        if options.expected_flavor == "full":
            verify_bundled_plugins(runtime_snapshot)
        elif runtime_snapshot.exists():
            raise RuntimeError("桌面 Core 后端意外包含 Full Plugin 快照")

    if options.desktop_executable:
        verify_windows_gui_executable(options.desktop_executable.resolve())

    if options.installer:
        installer = options.installer.resolve()
        if not installer.is_file() or installer.suffix.lower() != ".exe":
            raise RuntimeError(f"NSIS 安装包不存在: {installer}")
        checksum = write_checksum(installer)
        LOGGER.info("NSIS 安装包验证通过: %s", installer)
        LOGGER.info("SHA-256 文件: %s", checksum)
    else:
        LOGGER.info("桌面运行时边界验证通过")

    if options.updater_signature:
        verify_updater_signature(options.updater_signature.resolve())
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    raise SystemExit(main())
