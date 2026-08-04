"""Verify desktop runtime and optional NSIS output before artifact upload."""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sys
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from desktop.scripts.stage_defaults import SENSITIVE_KEYS


LOGGER = logging.getLogger("desktop.verify_bundle")


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


def write_checksum(installer: Path) -> Path:
    digest = hashlib.sha256(installer.read_bytes()).hexdigest()
    checksum_path = installer.with_suffix(installer.suffix + ".sha256")
    checksum_path.write_bytes(f"{digest}  {installer.name}\n".encode("ascii"))
    return checksum_path


def main() -> int:
    repo_root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser()
    parser.add_argument("--installer", type=Path)
    options = parser.parse_args()

    verify_defaults(repo_root / "desktop" / "generated" / "default-config")
    executable_name = "determinflow-backend.exe" if sys.platform == "win32" else "determinflow-backend"
    backend = repo_root / "desktop" / "runtime" / "backend" / executable_name
    if not backend.is_file():
        raise RuntimeError(f"桌面后端不存在: {backend}")

    if options.installer:
        installer = options.installer.resolve()
        if not installer.is_file() or installer.suffix.lower() != ".exe":
            raise RuntimeError(f"NSIS 安装包不存在: {installer}")
        checksum = write_checksum(installer)
        LOGGER.info("NSIS 安装包验证通过: %s", installer)
        LOGGER.info("SHA-256 文件: %s", checksum)
    else:
        LOGGER.info("桌面运行时边界验证通过")
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    raise SystemExit(main())
