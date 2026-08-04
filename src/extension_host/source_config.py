"""Persistent Plugin repository sources and catalog discovery."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import tempfile
import threading
import time
import tomllib
from collections.abc import Iterable
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.plugin_system import (
    PluginStore,
    validate_plugin_id,
    validate_plugin_ref,
    validate_plugin_subdirectory,
)
from src.plugin_system.source_selection import select_git_source


@dataclass(frozen=True)
class PluginSourceConfig:
    name: str
    url: str
    ref: str = "HEAD"
    id: str = ""
    kind: str = "official"
    builtin: bool = True
    mirrors: tuple[str, ...] = ()

    @property
    def clone_urls(self) -> tuple[str, ...]:
        return (self.url, *self.mirrors)


def _source_id(kind: str, name: str, url: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    if not slug:
        slug = "repository"
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:8]
    return f"{kind}-{slug[:48]}-{digest}"


def _parse_source(item: Any, *, kind: str) -> PluginSourceConfig:
    label = "official_sources" if kind == "official" else "custom_sources"
    if not isinstance(item, dict) or not isinstance(item.get("url"), str):
        raise ValueError(f"{label} 项必须包含字符串 url")
    raw_url = item["url"].strip()
    name = item.get("name", raw_url)
    ref = item.get("ref", "HEAD")
    if not raw_url:
        raise ValueError(f"{label}.url 不能为空")
    if not isinstance(name, str) or not name.strip():
        raise ValueError(f"{label}.name 必须是非空字符串")
    if not isinstance(ref, str):
        raise ValueError(f"{label}.ref 必须是非空字符串")
    try:
        url = PluginStore.canonicalize_source(raw_url)[0]
        normalized_ref = validate_plugin_ref(ref)
    except (RuntimeError, ValueError) as exc:
        raise ValueError(f"{label} 配置无效: {exc}") from exc
    raw_mirrors = item.get("mirrors", [])
    if not isinstance(raw_mirrors, list) or not all(
        isinstance(value, str) for value in raw_mirrors
    ):
        raise ValueError(f"{label}.mirrors 必须是字符串数组")
    mirrors: list[str] = []
    for raw_mirror in raw_mirrors:
        try:
            mirror = PluginStore.canonicalize_source(raw_mirror.strip())[0]
        except (RuntimeError, ValueError) as exc:
            raise ValueError(f"{label}.mirrors 配置无效: {exc}") from exc
        if mirror == url or mirror in mirrors:
            raise ValueError(f"{label}.mirrors 包含重复地址")
        mirrors.append(mirror)
    raw_id = item.get("id")
    if raw_id is None:
        source_id = _source_id(kind, name.strip(), url)
    elif not isinstance(raw_id, str):
        raise ValueError(f"{label}.id 必须是字符串")
    else:
        try:
            source_id = validate_plugin_id(raw_id)
        except ValueError as exc:
            raise ValueError(f"{label}.id 无效") from exc
    return PluginSourceConfig(
        id=source_id,
        name=name.strip(),
        url=url,
        ref=normalized_ref,
        kind=kind,
        builtin=kind == "official",
        mirrors=tuple(mirrors),
    )


def _load_source_document(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {
            "schema_version": 1,
            "official_sources": [],
            "custom_sources": [],
        }
    with path.open("r", encoding="utf-8") as handle:
        document = json.load(handle)
    if not isinstance(document, dict):
        raise ValueError("plugin-sources.json 必须是 object")
    if document.get("schema_version") != 1:
        raise ValueError("plugin-sources.json 版本不受支持")
    for key in ("official_sources", "custom_sources"):
        if key in document and not isinstance(document[key], list):
            raise ValueError(f"{key} 必须是数组")
    return document


def load_plugin_sources(path: Path) -> list[PluginSourceConfig]:
    document = _load_source_document(path)
    sources = [
        *(
            _parse_source(item, kind="official")
            for item in document.get("official_sources", [])
        ),
        *(
            _parse_source(item, kind="custom")
            for item in document.get("custom_sources", [])
        ),
    ]
    seen_ids: set[str] = set()
    seen_urls: set[str] = set()
    for source in sources:
        if source.id in seen_ids:
            raise ValueError(f"Plugin 仓库 ID 重复: {source.id}")
        duplicate = next((url for url in source.clone_urls if url in seen_urls), None)
        if duplicate is not None:
            raise ValueError(f"Plugin 仓库地址重复: {duplicate}")
        seen_ids.add(source.id)
        seen_urls.update(source.clone_urls)
    return sources


def load_official_sources(path: Path) -> list[str]:
    return [
        source.url
        for source in load_plugin_sources(path)
        if source.kind == "official"
    ]


def source_config_response(source: PluginSourceConfig) -> dict[str, Any]:
    return {
        "id": source.id,
        "name": source.name,
        "url": source.url,
        "ref": source.ref,
        "kind": source.kind,
        "builtin": source.builtin,
        "mirrors": list(source.mirrors),
    }


class PluginSourceStore:
    """Persist user-added repositories while preserving built-in sources."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.RLock()

    def list(self) -> list[PluginSourceConfig]:
        with self._lock:
            return load_plugin_sources(self.path)

    def create(self, *, name: str, url: str, ref: str = "HEAD") -> PluginSourceConfig:
        with self._lock:
            current = self.list()
            candidate = _parse_source(
                {"name": name, "url": url, "ref": ref},
                kind="custom",
            )
            self._ensure_unique(candidate, current)
            self._write_custom([source for source in current if source.kind == "custom"] + [candidate])
            return candidate

    def update(
        self,
        source_id: str,
        *,
        name: str,
        url: str | None = None,
        ref: str,
    ) -> PluginSourceConfig:
        with self._lock:
            current = self.list()
            existing = self._get(source_id, current)
            self._ensure_mutable(existing)
            candidate = _parse_source(
                {
                    "id": existing.id,
                    "name": name,
                    "url": url or existing.url,
                    "ref": ref,
                },
                kind="custom",
            )
            self._ensure_unique(candidate, current, exclude_id=source_id)
            custom = [
                candidate if source.id == source_id else source
                for source in current
                if source.kind == "custom"
            ]
            self._write_custom(custom)
            return candidate

    def delete(self, source_id: str) -> PluginSourceConfig:
        with self._lock:
            current = self.list()
            existing = self._get(source_id, current)
            self._ensure_mutable(existing)
            self._write_custom([
                source
                for source in current
                if source.kind == "custom" and source.id != source_id
            ])
            return existing

    @staticmethod
    def _get(
        source_id: str,
        sources: Iterable[PluginSourceConfig],
    ) -> PluginSourceConfig:
        for source in sources:
            if source.id == source_id:
                return source
        raise ValueError(f"Plugin 仓库不存在: {source_id}")

    @staticmethod
    def _ensure_mutable(source: PluginSourceConfig) -> None:
        if source.builtin:
            raise ValueError("内置官方仓库不能编辑或删除")

    @staticmethod
    def _ensure_unique(
        candidate: PluginSourceConfig,
        sources: Iterable[PluginSourceConfig],
        *,
        exclude_id: str = "",
    ) -> None:
        for source in sources:
            if source.id == exclude_id:
                continue
            if source.url == candidate.url:
                raise ValueError("该 Plugin 仓库已存在")
            if source.id == candidate.id:
                raise ValueError("Plugin 仓库 ID 已存在")

    def _write_custom(self, custom_sources: list[PluginSourceConfig]) -> None:
        document = _load_source_document(self.path)
        document["custom_sources"] = [
            {
                "id": source.id,
                "name": source.name,
                "url": source.url,
                "ref": source.ref,
            }
            for source in custom_sources
        ]
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=self.path.parent,
            prefix=f".{self.path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            json.dump(document, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            temporary = Path(handle.name)
        temporary.replace(self.path)


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


def _git_file(repository: Path, commit: str, path: str) -> bytes:
    try:
        return subprocess.run(
            ["git", "show", f"{commit}:{path}"],
            cwd=repository,
            check=True,
            capture_output=True,
            timeout=60,
        ).stdout
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError(f"仓库未提供 {path}") from exc


def _parse_repository_index(
    raw: bytes,
    source: PluginSourceConfig,
) -> list[dict[str, Any]]:
    try:
        document = tomllib.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise ValueError("plugin-repository.toml 无效") from exc
    if document.get("schema_version") != "1":
        raise ValueError("Plugin 仓库目录版本不受支持")
    raw_plugins = document.get("plugins", [])
    if not isinstance(raw_plugins, list):
        raise ValueError("Plugin 仓库目录 plugins 必须是数组")
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in raw_plugins:
        if not isinstance(item, dict):
            raise ValueError("Plugin 仓库目录项必须是 table")
        try:
            plugin_id = validate_plugin_id(str(item["id"]))
        except (KeyError, ValueError) as exc:
            raise ValueError("Plugin 仓库目录项 id 无效") from exc
        try:
            subdirectory = validate_plugin_subdirectory(item.get("subdirectory", ""))
        except ValueError as exc:
            raise ValueError(f"Plugin 仓库目录子路径无效: {plugin_id}") from exc
        plugin_ref = item.get("ref", source.ref)
        if not isinstance(plugin_ref, str):
            raise ValueError(f"Plugin 仓库目录 ref 无效: {plugin_id}")
        try:
            normalized_ref = validate_plugin_ref(plugin_ref)
        except ValueError as exc:
            raise ValueError(f"Plugin 仓库目录 ref 无效: {plugin_id}") from exc
        if plugin_id in seen:
            raise ValueError(f"Plugin 仓库目录包含重复 ID: {plugin_id}")
        seen.add(plugin_id)
        result.append({
            "id": plugin_id,
            "source_id": source.id,
            "source_name": source.name,
            "source": source.url,
            "source_kind": source.kind,
            "ref": normalized_ref,
            "subdirectory": subdirectory,
        })
    return result


def _manifest_metadata(raw: bytes, expected_id: str) -> dict[str, str]:
    try:
        document = tomllib.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise ValueError(f"Plugin 清单无效: {expected_id}") from exc
    extension = document.get("extension")
    if not isinstance(extension, dict) or extension.get("id") != expected_id:
        raise ValueError(f"Plugin 清单 ID 不匹配: {expected_id}")
    name = extension.get("name")
    version = extension.get("version")
    description = extension.get("description", "")
    if not isinstance(name, str) or not name.strip():
        raise ValueError(f"Plugin 清单缺少名称: {expected_id}")
    if not isinstance(version, str) or not version.strip():
        raise ValueError(f"Plugin 清单缺少版本: {expected_id}")
    if not isinstance(description, str):
        raise ValueError(f"Plugin 清单描述无效: {expected_id}")
    return {
        "name": name.strip(),
        "version": version.strip(),
        "description": description.strip(),
    }


def fetch_plugin_catalog(
    configured_sources: Iterable[PluginSourceConfig],
) -> dict[str, Any]:
    """Fetch indexes and installable Plugin metadata from Git sources."""
    plugins: list[dict[str, Any]] = []
    sources: list[dict[str, Any]] = []
    for source in tuple(configured_sources):
        source_result: dict[str, Any] = {
            **source_config_response(source),
            "resolved_commit": "",
            "plugin_count": 0,
            "error": "",
            "selected_url": "",
        }
        source_plugins: list[dict[str, Any]] = []
        try:
            with tempfile.TemporaryDirectory(prefix="determinflow-plugin-catalog-") as raw:
                repository = Path(raw) / "repository"
                selected = select_git_source(source.clone_urls, source.ref)
                _run_git(
                    "clone",
                    "--quiet",
                    "--no-checkout",
                    "--",
                    selected.url,
                    str(repository),
                )
                source_result["selected_url"] = selected.url
                source_commit = _resolve_ref(repository, source.ref)
                if selected.commit and source_commit != selected.commit:
                    raise RuntimeError("Plugin 镜像在拉取期间发生版本漂移")
                entries = _parse_repository_index(
                    _git_file(repository, source_commit, "plugin-repository.toml"),
                    source,
                )
                for entry in entries:
                    commit = _resolve_ref(repository, entry["ref"])
                    manifest_path = "/".join(filter(None, (
                        entry["subdirectory"],
                        "extension.toml",
                    )))
                    entry.update(
                        _manifest_metadata(
                            _git_file(repository, commit, manifest_path),
                            entry["id"],
                        )
                    )
                    entry["resolved_commit"] = commit
                    source_plugins.append(entry)
                source_result["resolved_commit"] = source_commit
                source_result["plugin_count"] = len(source_plugins)
                plugins.extend(source_plugins)
        except ValueError as exc:
            source_result["error"] = str(exc)
        sources.append(source_result)
    plugins.sort(key=lambda item: (item["id"], item["source"]))
    return {"sources": sources, "plugins": plugins}


class PluginCatalogService:
    """TTL cache with source replacement and a single-flight refresh gate."""

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

    def replace_sources(
        self,
        configured_sources: Iterable[PluginSourceConfig],
    ) -> None:
        with self._lock:
            self.sources = tuple(configured_sources)
            self._cached_at = 0.0
            self._cache = None

    def get(self, *, refresh: bool = False) -> dict[str, Any]:
        now = time.monotonic()
        with self._lock:
            if (
                not refresh
                and self._cache is not None
                and now - self._cached_at < self.ttl_seconds
            ):
                return deepcopy(self._cache)
            if self._refreshing:
                if self._cache is not None:
                    return deepcopy(self._cache)
                return {"sources": [], "plugins": [], "refreshing": True}
            self._refreshing = True
            sources = self.sources
        try:
            result = fetch_plugin_catalog(sources)
        finally:
            with self._lock:
                self._refreshing = False
        with self._lock:
            self._cache = deepcopy(result)
            self._cached_at = time.monotonic()
            return deepcopy(result)
