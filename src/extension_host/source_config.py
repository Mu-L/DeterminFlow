"""Trusted Plugin source configuration and repository-index discovery."""

from __future__ import annotations

import json
import subprocess
import tempfile
import threading
import time
import tomllib
from copy import deepcopy
from dataclasses import dataclass
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from src.plugin_system import (
    PluginStore,
    validate_plugin_id,
    validate_plugin_ref,
    validate_plugin_subdirectory,
)


@dataclass(frozen=True)
class PluginSourceConfig:
    name: str
    url: str
    ref: str = "HEAD"


def load_plugin_sources(path: Path) -> list[PluginSourceConfig]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8") as handle:
        document = json.load(handle)
    if not isinstance(document, dict):
        raise ValueError("plugin-sources.json 必须是 object")
    if document.get("schema_version") != 1:
        raise ValueError("plugin-sources.json 版本不受支持")
    raw_sources = document.get("official_sources", [])
    if not isinstance(raw_sources, list):
        raise ValueError("official_sources 必须是数组")
    result: list[PluginSourceConfig] = []
    for item in raw_sources:
        if not isinstance(item, dict) or not isinstance(item.get("url"), str):
            raise ValueError("official_sources 项必须包含字符串 url")
        raw_url = item["url"].strip()
        name = item.get("name", raw_url)
        ref = item.get("ref", "HEAD")
        if not raw_url:
            raise ValueError("official_sources.url 不能为空")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("official_sources.name 必须是非空字符串")
        if not isinstance(ref, str):
            raise ValueError("official_sources.ref 必须是非空字符串")
        try:
            url = PluginStore.canonicalize_source(raw_url)[0]
            normalized_ref = validate_plugin_ref(ref)
        except (RuntimeError, ValueError) as exc:
            raise ValueError(f"official_sources 配置无效: {exc}") from exc
        result.append(
            PluginSourceConfig(
                name=name.strip(),
                url=url,
                ref=normalized_ref,
            )
        )
    return result


def load_official_sources(path: Path) -> list[str]:
    return [source.url for source in load_plugin_sources(path)]


def _run_git(*args: str, cwd: Path | None = None) -> str:
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=cwd,
            check=True,
            capture_output=True,
            text=True,
            timeout=180,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError("Plugin 仓库目录暂不可用") from exc
    return completed.stdout


def _resolve_ref(repository: Path, ref: str) -> str:
    candidates = [ref]
    if not ref.startswith("refs/") and ref != "HEAD":
        candidates.append(f"refs/remotes/origin/{ref}")
    for candidate in candidates:
        try:
            commit = _run_git(
                "rev-parse",
                "--verify",
                "--end-of-options",
                f"{candidate}^{{commit}}",
                cwd=repository,
            ).strip()
        except ValueError:
            continue
        if len(commit) == 40:
            return commit
    raise ValueError(f"Plugin 仓库 ref 不存在: {ref}")


def _parse_repository_index(
    raw: bytes,
    source: PluginSourceConfig,
) -> list[dict[str, str]]:
    try:
        document = tomllib.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise ValueError("plugin-repository.toml 无效") from exc
    if document.get("schema_version") != "1":
        raise ValueError("Plugin 仓库目录版本不受支持")
    raw_plugins = document.get("plugins", [])
    if not isinstance(raw_plugins, list):
        raise ValueError("Plugin 仓库目录 plugins 必须是数组")
    result: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in raw_plugins:
        if not isinstance(item, dict):
            raise ValueError("Plugin 仓库目录项必须是 table")
        try:
            plugin_id = validate_plugin_id(str(item["id"]))
        except (KeyError, ValueError) as exc:
            raise ValueError("Plugin 仓库目录项 id 无效") from exc
        try:
            subdirectory = validate_plugin_subdirectory(
                item.get("subdirectory", "")
            )
        except ValueError as exc:
            raise ValueError(
                f"Plugin 仓库目录子路径无效: {plugin_id}"
            ) from exc
        plugin_ref = item.get("ref", source.ref)
        if not isinstance(plugin_ref, str):
            raise ValueError(f"Plugin 仓库目录 ref 无效: {plugin_id}")
        try:
            normalized_ref = validate_plugin_ref(plugin_ref)
        except ValueError as exc:
            raise ValueError(
                f"Plugin 仓库目录 ref 无效: {plugin_id}"
            ) from exc
        if plugin_id in seen:
            raise ValueError(f"Plugin 仓库目录包含重复 ID: {plugin_id}")
        seen.add(plugin_id)
        result.append({
            "id": plugin_id,
            "source_name": source.name,
            "source": source.url,
            "ref": normalized_ref,
            "subdirectory": subdirectory,
        })
    return result


def fetch_plugin_catalog(
    configured_sources: Iterable[PluginSourceConfig],
) -> dict[str, Any]:
    """Fetch optional indexes from configured official Git sources."""
    plugins: list[dict[str, str]] = []
    sources: list[dict[str, Any]] = []
    for source in tuple(configured_sources):
        source_result: dict[str, Any] = {
            "name": source.name,
            "url": source.url,
            "ref": source.ref,
            "error": "",
        }
        try:
            with tempfile.TemporaryDirectory(prefix="ai-company-plugin-catalog-") as raw:
                repository = Path(raw) / "repository"
                _run_git(
                    "clone",
                    "--quiet",
                    "--no-checkout",
                    "--",
                    source.url,
                    str(repository),
                )
                commit = _resolve_ref(repository, source.ref)
                try:
                    index = subprocess.run(
                        [
                            "git",
                            "show",
                            f"{commit}:plugin-repository.toml",
                        ],
                        cwd=repository,
                        check=True,
                        capture_output=True,
                        timeout=60,
                    ).stdout
                except (OSError, subprocess.SubprocessError) as exc:
                    raise ValueError(
                        "仓库未提供 plugin-repository.toml"
                    ) from exc
                entries = _parse_repository_index(index, source)
                source_result["resolved_commit"] = commit
                plugins.extend(entries)
        except ValueError as exc:
            source_result["error"] = str(exc)
        sources.append(source_result)
    plugins.sort(key=lambda item: (item["id"], item["source"]))
    return {"sources": sources, "plugins": plugins}


class PluginCatalogService:
    """TTL cache with a non-blocking single-flight refresh gate."""

    def __init__(
        self,
        configured_sources: Iterable[PluginSourceConfig],
        *,
        ttl_seconds: float = 300,
    ):
        self.sources = tuple(configured_sources)
        self.ttl_seconds = ttl_seconds
        self._lock = threading.Lock()
        self._refreshing = False
        self._cached_at = 0.0
        self._cache: dict[str, Any] | None = None

    def get(self) -> dict[str, Any]:
        now = time.monotonic()
        with self._lock:
            if (
                self._cache is not None
                and now - self._cached_at < self.ttl_seconds
            ):
                return deepcopy(self._cache)
            if self._refreshing:
                if self._cache is not None:
                    return deepcopy(self._cache)
                return {
                    "sources": [],
                    "plugins": [],
                    "refreshing": True,
                }
            self._refreshing = True
        try:
            result = fetch_plugin_catalog(self.sources)
        finally:
            with self._lock:
                self._refreshing = False
        with self._lock:
            self._cache = deepcopy(result)
            self._cached_at = time.monotonic()
            return deepcopy(result)
