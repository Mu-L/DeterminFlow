"""Create and validate the immutable official Plugin input for Full builds."""

from __future__ import annotations

import json
import os
import re
import tempfile
from collections.abc import Iterable
from dataclasses import replace
from pathlib import Path
from typing import Any

from src.extension_host.source_config import (
    PluginSourceConfig,
    fetch_plugin_catalog,
    load_plugin_sources,
)

LOCK_SCHEMA_VERSION = 1
LOCK_RELATIVE_PATH = Path("desktop/official-plugins.lock.json")
_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")


def _desktop_version(repo_root: Path) -> str:
    path = repo_root / "desktop" / "src-tauri" / "tauri.conf.json"
    try:
        version = json.loads(path.read_text(encoding="utf-8"))["version"]
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as error:
        raise RuntimeError("桌面版本配置无效") from error
    if not isinstance(version, str) or not version.strip():
        raise RuntimeError("桌面版本配置无效")
    return version.strip()


def _official_sources(source_file: Path) -> tuple[PluginSourceConfig, ...]:
    sources = tuple(
        source
        for source in load_plugin_sources(source_file)
        if source.kind == "official"
    )
    if len(sources) != 1:
        raise RuntimeError("桌面 Full 构建必须配置且只能配置一个官方 Plugin 仓库")
    return sources


def _catalog_or_raise(
    sources: Iterable[PluginSourceConfig],
) -> dict[str, Any]:
    catalog = fetch_plugin_catalog(sources)
    errors = [
        f"{source['name']}: {source['error']}"
        for source in catalog.get("sources", [])
        if source.get("error")
    ]
    if errors:
        raise RuntimeError("官方 Plugin Catalog 获取失败: " + "; ".join(errors))
    if not catalog.get("plugins"):
        raise RuntimeError("官方 Plugin Catalog 不包含可安装 Plugin")
    return catalog


def _normalized_lock(document: Any) -> dict[str, Any]:
    try:
        if not isinstance(document, dict) or document["schema_version"] != 1:
            raise ValueError
        desktop_version = document["desktop_version"]
        source = document["source"]
        plugins = document["plugins"]
        if not isinstance(desktop_version, str) or not desktop_version.strip():
            raise ValueError
        if not isinstance(source, dict):
            raise TypeError
        source_id = source["id"]
        source_url = source["url"]
        source_ref = source["ref"]
        source_commit = source["commit"]
        if not all(
            isinstance(value, str) and value.strip()
            for value in (source_id, source_url, source_ref, source_commit)
        ):
            raise ValueError
        source_commit = source_commit.lower()
        if not _COMMIT_RE.fullmatch(source_commit):
            raise ValueError
        if not isinstance(plugins, list) or not plugins:
            raise ValueError
        normalized_plugins: list[dict[str, str]] = []
        seen: set[str] = set()
        for item in plugins:
            if not isinstance(item, dict):
                raise TypeError
            plugin_id = item["id"]
            version = item["version"]
            subdirectory = item["subdirectory"]
            if not all(
                isinstance(value, str) and value.strip()
                for value in (plugin_id, version, subdirectory)
            ):
                raise ValueError
            if plugin_id in seen:
                raise ValueError
            seen.add(plugin_id)
            normalized_plugins.append(
                {
                    "id": plugin_id.strip(),
                    "version": version.strip(),
                    "subdirectory": subdirectory.strip(),
                }
            )
        normalized_plugins.sort(key=lambda item: item["id"])
    except (KeyError, TypeError, ValueError) as error:
        raise RuntimeError("桌面 Full 官方 Plugin 构建锁无效") from error
    return {
        "schema_version": LOCK_SCHEMA_VERSION,
        "desktop_version": desktop_version.strip(),
        "source": {
            "id": source_id.strip(),
            "url": source_url.strip(),
            "ref": source_ref.strip(),
            "commit": source_commit,
        },
        "plugins": normalized_plugins,
    }


def load_official_plugin_lock(repo_root: Path) -> dict[str, Any]:
    path = repo_root / LOCK_RELATIVE_PATH
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError("桌面 Full 官方 Plugin 构建锁不存在或无法读取") from error
    lock = _normalized_lock(document)
    desktop_version = _desktop_version(repo_root)
    if lock["desktop_version"] != desktop_version:
        raise RuntimeError(
            "桌面版本与 Full 官方 Plugin 构建锁不一致: "
            f"desktop={desktop_version}, lock={lock['desktop_version']}"
        )
    return lock


def resolve_latest_official_plugin_lock(
    repo_root: Path,
    source_file: Path,
) -> dict[str, Any]:
    sources = _official_sources(source_file)
    catalog = _catalog_or_raise(sources)
    source = sources[0]
    source_results = catalog.get("sources", [])
    if len(source_results) != 1:
        raise RuntimeError("官方 Plugin Catalog 来源数量与配置不一致")
    commit = str(source_results[0].get("resolved_commit", "")).lower()
    if not _COMMIT_RE.fullmatch(commit):
        raise RuntimeError("官方 Plugin Catalog 未解析到精确 Commit")
    plugins: list[dict[str, str]] = []
    for entry in catalog["plugins"]:
        if entry.get("source_id") != source.id:
            raise RuntimeError("官方 Plugin Catalog 包含非预期来源")
        if str(entry.get("resolved_commit", "")).lower() != commit:
            raise RuntimeError("官方 Plugin Catalog 条目未锁定到来源 Commit")
        plugins.append(
            {
                "id": str(entry["id"]),
                "version": str(entry["version"]),
                "subdirectory": str(entry["subdirectory"]),
            }
        )
    return _normalized_lock(
        {
            "schema_version": LOCK_SCHEMA_VERSION,
            "desktop_version": _desktop_version(repo_root),
            "source": {
                "id": source.id,
                "url": source.url,
                "ref": source.ref,
                "commit": commit,
            },
            "plugins": plugins,
        }
    )


def write_official_plugin_lock(repo_root: Path, lock: dict[str, Any]) -> Path:
    path = repo_root / LOCK_RELATIVE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (
        json.dumps(_normalized_lock(lock), ensure_ascii=False, indent=2, sort_keys=True)
        + "\n"
    )
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        handle.write(payload)
        temporary = Path(handle.name)
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return path


def pin_official_sources(
    sources: Iterable[PluginSourceConfig],
    lock: dict[str, Any],
) -> tuple[PluginSourceConfig, ...]:
    configured = tuple(sources)
    if len(configured) != 1:
        raise RuntimeError("桌面 Full 构建必须配置且只能配置一个官方 Plugin 仓库")
    source = configured[0]
    expected = lock["source"]
    if (
        source.id != expected["id"]
        or source.url != expected["url"]
        or source.ref != expected["ref"]
    ):
        raise RuntimeError("桌面官方 Plugin 来源与构建锁不一致")
    return (replace(source, ref=expected["commit"]),)


def validate_locked_catalog(
    catalog: dict[str, Any],
    lock: dict[str, Any],
) -> list[dict[str, Any]]:
    errors = [
        f"{source['name']}: {source['error']}"
        for source in catalog.get("sources", [])
        if source.get("error")
    ]
    if errors:
        raise RuntimeError("官方 Plugin Catalog 获取失败: " + "; ".join(errors))
    source_results = catalog.get("sources", [])
    expected_source = lock["source"]
    if len(source_results) != 1:
        raise RuntimeError("官方 Plugin Catalog 来源数量与构建锁不一致")
    source_result = source_results[0]
    if (
        source_result.get("id") != expected_source["id"]
        or source_result.get("ref") != expected_source["commit"]
        or str(source_result.get("resolved_commit", "")).lower()
        != expected_source["commit"]
    ):
        raise RuntimeError("官方 Plugin Catalog 未解析到构建锁 Commit")

    entries = list(catalog.get("plugins", []))
    actual = sorted(
        (
            {
                "id": str(entry.get("id", "")),
                "version": str(entry.get("version", "")),
                "subdirectory": str(entry.get("subdirectory", "")),
            }
            for entry in entries
        ),
        key=lambda item: item["id"],
    )
    if actual != lock["plugins"]:
        raise RuntimeError("官方 Plugin Catalog 条目与构建锁不一致")
    if any(
        entry.get("source_id") != expected_source["id"]
        or entry.get("ref") != expected_source["commit"]
        or str(entry.get("resolved_commit", "")).lower() != expected_source["commit"]
        for entry in entries
    ):
        raise RuntimeError("官方 Plugin Catalog 条目未锁定到构建锁 Commit")
    return entries
