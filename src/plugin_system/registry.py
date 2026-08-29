"""Official Plugin static HTTPS Registry v1 transport."""

from __future__ import annotations

import base64
import binascii
import hashlib
import io
import json
import os
import re
import zipfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from .models import (
    validate_plugin_id,
    validate_plugin_ref,
    validate_plugin_subdirectory,
)
from .source_selection import canonicalize_plugin_source


REGISTRY_SCHEMA_VERSION = 1
MANIFEST_NAME = "manifest.json"
SIGNATURE_NAME = "manifest.json.sig"
MAX_MANIFEST_BYTES = 1_048_576
MAX_SIGNATURE_BYTES = 4_096
MAX_PACKAGE_BYTES = 100 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 512 * 1024 * 1024
MAX_PACKAGE_FILES = 10_000
MANIFEST_TIMEOUT_SECONDS = 15.0
PACKAGE_TIMEOUT_SECONDS = 120.0
_COMMIT_RE = re.compile(r"^[0-9a-fA-F]{40,64}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_UNIX_IFMT = 0o170000
_UNIX_REGULAR = 0o100000
_UNIX_DIRECTORY = 0o040000
_UNIX_SYMLINK = 0o120000

HttpGet = Callable[..., bytes]


class PluginRegistryError(RuntimeError):
    """Raised when official Registry transport cannot be used."""


@dataclass(frozen=True)
class PluginRegistryConfig:
    url: str
    public_key: bytes
    public_key_text: str


@dataclass(frozen=True)
class RegistryPlugin:
    plugin_id: str
    name: str
    version: str
    description: str
    subdirectory: str
    ref: str
    commit: str
    package_url: str
    package_sha256: str
    content_sha256: str


@dataclass(frozen=True)
class RegistryManifest:
    source: str
    ref: str
    resolved_commit: str
    plugins: tuple[RegistryPlugin, ...]


def parse_registry_config(raw: Any, *, label: str) -> PluginRegistryConfig:
    if not isinstance(raw, dict):
        raise ValueError(f"{label}.registry 必须是 object")
    url = raw.get("url")
    public_key = raw.get("public_key")
    if not isinstance(url, str) or not url.strip():
        raise ValueError(f"{label}.registry.url 必须是非空字符串")
    if not isinstance(public_key, str) or not public_key.strip():
        raise ValueError(f"{label}.registry.public_key 必须是非空字符串")
    try:
        canonical_url = canonicalize_registry_url(url)
        key_bytes = decode_ed25519_public_key(public_key)
    except PluginRegistryError as exc:
        raise ValueError(f"{label}.registry 配置无效: {exc}") from exc
    return PluginRegistryConfig(
        url=canonical_url,
        public_key=key_bytes,
        public_key_text=public_key.strip(),
    )


def canonicalize_registry_url(url: str) -> str:
    raw = str(url).strip()
    split = urlsplit(raw)
    if split.scheme.lower() != "https":
        raise PluginRegistryError("Registry URL 必须是 HTTPS")
    hostname = (split.hostname or "").lower()
    if not hostname:
        raise PluginRegistryError("Registry URL 无效")
    if split.username is not None or split.password is not None:
        raise PluginRegistryError("Registry URL 不得包含凭据")
    if split.query or split.fragment:
        raise PluginRegistryError("Registry URL 不得包含 query 或 fragment")
    port = f":{split.port}" if split.port not in {None, 443} else ""
    path = split.path.rstrip("/") or "/"
    return urlunsplit(("https", f"{hostname}{port}", path, "", ""))


def decode_ed25519_public_key(value: str) -> bytes:
    decoded = _decode_binary(value.strip())
    if len(decoded) != 32:
        raise PluginRegistryError("Registry 公钥必须是 32 字节 Ed25519 公钥")
    return decoded


def decode_ed25519_signature(value: bytes) -> bytes:
    if len(value) == 64:
        return value
    decoded = _decode_binary(value.decode("ascii", errors="strict").strip())
    if len(decoded) != 64:
        raise PluginRegistryError("Registry 签名必须是 64 字节 Ed25519 签名")
    return decoded


def _decode_binary(value: str) -> bytes:
    compact = "".join(value.split())
    if re.fullmatch(r"[0-9a-fA-F]+", compact) and len(compact) % 2 == 0:
        try:
            return binascii.unhexlify(compact)
        except binascii.Error as exc:
            raise PluginRegistryError("Registry 密钥或签名编码无效") from exc
    try:
        return base64.b64decode(compact, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise PluginRegistryError("Registry 密钥或签名编码无效") from exc


def registry_object_url(registry_url: str, name: str) -> str:
    base = registry_url if registry_url.endswith("/") else f"{registry_url}/"
    return urljoin(base, name)


def resolve_package_url(registry_url: str, package_url: str) -> str:
    raw = str(package_url).strip()
    if not raw:
        raise PluginRegistryError("Registry 包地址不能为空")
    if urlsplit(raw).scheme:
        canonical = canonicalize_registry_url(raw)
        return canonical
    return canonicalize_registry_url(registry_object_url(registry_url, raw))


def https_get(
    url: str,
    *,
    max_bytes: int,
    timeout: float,
) -> bytes:
    canonical = canonicalize_registry_url(url)
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            with client.stream("GET", canonical) as response:
                final_url = str(response.url)
                canonicalize_registry_url(final_url)
                response.raise_for_status()
                chunks: list[bytes] = []
                total = 0
                for chunk in response.iter_bytes():
                    total += len(chunk)
                    if total > max_bytes:
                        raise PluginRegistryError("Registry 对象超过大小限制")
                    chunks.append(chunk)
                return b"".join(chunks)
    except PluginRegistryError:
        raise
    except httpx.HTTPError as exc:
        raise PluginRegistryError(f"Registry 传输失败: {exc}") from exc


def verify_manifest_signature(
    manifest_bytes: bytes,
    signature: bytes,
    public_key: bytes,
) -> None:
    try:
        Ed25519PublicKey.from_public_bytes(public_key).verify(
            decode_ed25519_signature(signature),
            manifest_bytes,
        )
    except (InvalidSignature, ValueError) as exc:
        raise PluginRegistryError("Registry 清单签名无效") from exc


def parse_registry_manifest(
    document: Any,
    *,
    registry_url: str,
) -> RegistryManifest:
    if not isinstance(document, dict):
        raise PluginRegistryError("Registry 清单必须是 object")
    if document.get("schema_version") != REGISTRY_SCHEMA_VERSION:
        raise PluginRegistryError("Registry 清单版本不受支持")
    source = document.get("source")
    ref = document.get("ref")
    resolved_commit = document.get("resolved_commit")
    raw_plugins = document.get("plugins")
    if not isinstance(source, str) or not source.strip():
        raise PluginRegistryError("Registry 清单缺少 source")
    try:
        normalized_ref = validate_plugin_ref(str(ref))
    except (TypeError, ValueError) as exc:
        raise PluginRegistryError("Registry 清单 ref 无效") from exc
    if not isinstance(resolved_commit, str) or not _COMMIT_RE.fullmatch(
        resolved_commit
    ):
        raise PluginRegistryError("Registry 清单 resolved_commit 无效")
    if not isinstance(raw_plugins, list) or not raw_plugins:
        raise PluginRegistryError("Registry 清单 plugins 必须是非空数组")
    plugins: list[RegistryPlugin] = []
    seen: set[str] = set()
    for item in raw_plugins:
        plugin = _parse_registry_plugin(item, registry_url=registry_url)
        if plugin.plugin_id in seen:
            raise PluginRegistryError(
                f"Registry 清单包含重复 Plugin: {plugin.plugin_id}"
            )
        seen.add(plugin.plugin_id)
        plugins.append(plugin)
    return RegistryManifest(
        source=source.strip(),
        ref=normalized_ref,
        resolved_commit=resolved_commit.lower(),
        plugins=tuple(plugins),
    )


def _parse_registry_plugin(
    item: Any,
    *,
    registry_url: str,
) -> RegistryPlugin:
    if not isinstance(item, dict):
        raise PluginRegistryError("Registry 清单 Plugin 必须是 object")
    try:
        plugin_id = validate_plugin_id(str(item["id"]))
        subdirectory = validate_plugin_subdirectory(item.get("subdirectory", ""))
        raw_ref = item.get("ref")
        if not isinstance(raw_ref, str):
            raise ValueError("ref")
        plugin_ref = validate_plugin_ref(raw_ref)
    except (KeyError, TypeError, ValueError) as exc:
        raise PluginRegistryError("Registry 清单 Plugin 身份无效") from exc
    name = item.get("name")
    version = item.get("version")
    description = item.get("description", "")
    commit = item.get("commit")
    content_digest = item.get("content_sha256")
    if not isinstance(name, str) or not name.strip():
        raise PluginRegistryError(f"Registry 清单缺少名称: {plugin_id}")
    if not isinstance(version, str) or not version.strip():
        raise PluginRegistryError(f"Registry 清单缺少版本: {plugin_id}")
    if not isinstance(description, str):
        raise PluginRegistryError(f"Registry 清单描述无效: {plugin_id}")
    if not isinstance(commit, str) or not _COMMIT_RE.fullmatch(commit):
        raise PluginRegistryError(f"Registry 清单 commit 无效: {plugin_id}")
    if not isinstance(content_digest, str) or not _SHA256_RE.fullmatch(
        content_digest
    ):
        raise PluginRegistryError(f"Registry 清单 content_sha256 无效: {plugin_id}")
    package = item.get("package")
    if not isinstance(package, Mapping):
        raise PluginRegistryError(f"Registry 清单 package 无效: {plugin_id}")
    package_url = package.get("url")
    package_sha256 = package.get("sha256")
    if not isinstance(package_url, str) or not package_url.strip():
        raise PluginRegistryError(f"Registry 清单 package.url 无效: {plugin_id}")
    if not isinstance(package_sha256, str) or not _SHA256_RE.fullmatch(
        package_sha256
    ):
        raise PluginRegistryError(f"Registry 清单 package.sha256 无效: {plugin_id}")
    try:
        resolved_package_url = resolve_package_url(registry_url, package_url)
    except PluginRegistryError as exc:
        raise PluginRegistryError(
            f"Registry 清单 package.url 无效: {plugin_id}"
        ) from exc
    return RegistryPlugin(
        plugin_id=plugin_id,
        name=name.strip(),
        version=version.strip(),
        description=description.strip(),
        subdirectory=subdirectory,
        ref=plugin_ref,
        commit=commit.lower(),
        package_url=resolved_package_url,
        package_sha256=package_sha256,
        content_sha256=content_digest,
    )


def fetch_signed_manifest(
    registry: PluginRegistryConfig,
    *,
    http_get: HttpGet | None = None,
) -> tuple[bytes, RegistryManifest]:
    getter = http_get or https_get
    manifest_url = registry_object_url(registry.url, MANIFEST_NAME)
    signature_url = registry_object_url(registry.url, SIGNATURE_NAME)
    manifest_bytes = getter(
        manifest_url,
        max_bytes=MAX_MANIFEST_BYTES,
        timeout=MANIFEST_TIMEOUT_SECONDS,
    )
    signature_bytes = getter(
        signature_url,
        max_bytes=MAX_SIGNATURE_BYTES,
        timeout=MANIFEST_TIMEOUT_SECONDS,
    )
    verify_manifest_signature(
        manifest_bytes,
        signature_bytes,
        registry.public_key,
    )
    try:
        document = json.loads(manifest_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PluginRegistryError("Registry 清单不是有效 JSON") from exc
    manifest = parse_registry_manifest(document, registry_url=registry.url)
    _canonical_git_source(manifest.source)
    return manifest_bytes, manifest


def load_verified_manifest(
    registry: PluginRegistryConfig,
    canonical_source: str,
    *,
    http_get: HttpGet | None = None,
) -> RegistryManifest:
    _, manifest = fetch_signed_manifest(registry, http_get=http_get)
    require_manifest_source(manifest, canonical_source)
    return manifest


def require_manifest_source(manifest: RegistryManifest, canonical_source: str) -> None:
    if _canonical_git_source(manifest.source) != canonical_source:
        raise PluginRegistryError("Registry 清单来源与官方 Git 身份不一致")


def _canonical_git_source(source: str) -> str:
    try:
        canonical, kind, _clone = canonicalize_plugin_source(source)
    except ValueError as exc:
        raise PluginRegistryError("Registry 清单 source 不是合法 Git 身份") from exc
    if kind not in {"git", "local"}:
        raise PluginRegistryError("Registry 清单 source 不是合法 Git 身份")
    return canonical


def select_registry_plugin(
    manifest: RegistryManifest,
    plugin_id: str,
    ref: str,
) -> RegistryPlugin:
    try:
        normalized_id = validate_plugin_id(plugin_id)
        requested_ref = validate_plugin_ref(ref)
    except ValueError as exc:
        raise PluginRegistryError(str(exc)) from exc
    matches = [
        item for item in manifest.plugins if item.plugin_id == normalized_id
    ]
    if len(matches) != 1:
        raise PluginRegistryError(f"Registry 未提供 Plugin: {normalized_id}")
    plugin = matches[0]
    if _COMMIT_RE.fullmatch(requested_ref):
        if plugin.commit != requested_ref.lower():
            raise PluginRegistryError(
                f"Registry 未提供该 commit 的包: {normalized_id}"
            )
        return plugin
    if requested_ref in {"HEAD", manifest.ref, plugin.ref}:
        return plugin
    raise PluginRegistryError(f"Registry 未覆盖该 ref: {requested_ref}")


def download_registry_package(
    plugin: RegistryPlugin,
    *,
    http_get: HttpGet | None = None,
) -> bytes:
    getter = http_get or https_get
    payload = getter(
        plugin.package_url,
        max_bytes=MAX_PACKAGE_BYTES,
        timeout=PACKAGE_TIMEOUT_SECONDS,
    )
    digest = hashlib.sha256(payload).hexdigest()
    if digest != plugin.package_sha256:
        raise PluginRegistryError(
            f"Plugin 包 SHA256 与签名清单不一致: {plugin.plugin_id}"
        )
    return payload


def extract_plugin_zip(payload: bytes, destination: Path) -> None:
    if destination.exists():
        raise PluginRegistryError("Registry 解压目录已存在")
    destination.mkdir(parents=True)
    try:
        _extract_plugin_zip(payload, destination)
    except PluginRegistryError:
        raise
    except (OSError, zipfile.BadZipFile, zipfile.LargeZipFile) as exc:
        raise PluginRegistryError(f"Plugin ZIP 无效: {exc}") from exc


def _extract_plugin_zip(payload: bytes, destination: Path) -> None:
    root = destination.resolve()
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        infos = archive.infolist()
        if not infos:
            raise PluginRegistryError("Plugin ZIP 为空")
        if len(infos) > MAX_PACKAGE_FILES:
            raise PluginRegistryError("Plugin ZIP 文件数量超过限制")
        declared_total = 0
        for info in infos:
            _validate_zip_entry(info)
            declared_total += max(info.file_size, 0)
            if declared_total > MAX_UNCOMPRESSED_BYTES:
                raise PluginRegistryError("Plugin ZIP 解压体积超过限制")
        written_total = 0
        for info in infos:
            relative = _safe_zip_path(info.filename)
            target = (root / relative).resolve()
            try:
                target.relative_to(root)
            except ValueError as exc:
                raise PluginRegistryError(
                    f"Plugin ZIP 路径穿越: {info.filename}"
                ) from exc
            if info.is_dir() or info.filename.endswith("/"):
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            written = 0
            with archive.open(info, "r") as source, target.open("wb") as output:
                while True:
                    chunk = source.read(1024 * 1024)
                    if not chunk:
                        break
                    written += len(chunk)
                    written_total += len(chunk)
                    if (
                        written > info.file_size
                        or written_total > MAX_UNCOMPRESSED_BYTES
                    ):
                        raise PluginRegistryError("Plugin ZIP 解压体积超过限制")
                    output.write(chunk)
            if written != info.file_size:
                raise PluginRegistryError(
                    f"Plugin ZIP 文件大小与声明不一致: {info.filename}"
                )
            _apply_zip_permissions(target, info)


def _validate_zip_entry(info: zipfile.ZipInfo) -> None:
    if info.flag_bits & 0x1:
        raise PluginRegistryError("Plugin ZIP 不得加密")
    mode = _unix_file_type(info)
    if mode == _UNIX_SYMLINK:
        raise PluginRegistryError(f"Plugin ZIP 不得包含链接: {info.filename}")
    if mode and mode not in {_UNIX_REGULAR, _UNIX_DIRECTORY}:
        raise PluginRegistryError(f"Plugin ZIP 包含不支持的文件类型: {info.filename}")
    if info.file_size < 0 or info.compress_size < 0:
        raise PluginRegistryError("Plugin ZIP 体积无效")
    if info.file_size > MAX_UNCOMPRESSED_BYTES:
        raise PluginRegistryError("Plugin ZIP 解压体积超过限制")


def _unix_file_type(info: zipfile.ZipInfo) -> int:
    if info.create_system != 3:
        return 0
    return (info.external_attr >> 16) & _UNIX_IFMT


def _safe_zip_path(name: str) -> PurePosixPath:
    raw = str(name).replace("\\", "/").strip()
    if not raw or raw.endswith(":"):
        raise PluginRegistryError("Plugin ZIP 包含空路径")
    if raw.startswith("/") or re.match(r"^[A-Za-z]:", raw):
        raise PluginRegistryError(f"Plugin ZIP 不得使用绝对路径: {name}")
    path = PurePosixPath(raw)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise PluginRegistryError(f"Plugin ZIP 路径穿越: {name}")
    if any(part == ".git" for part in path.parts):
        raise PluginRegistryError("Plugin ZIP 不得包含 .git")
    return path


def _apply_zip_permissions(path: Path, info: zipfile.ZipInfo) -> None:
    unix_mode = (info.external_attr >> 16) & 0o777 if info.create_system == 3 else 0
    if unix_mode & 0o111:
        os.chmod(path, 0o755)
    else:
        os.chmod(path, 0o644)


def catalog_plugins_from_manifest(
    manifest: RegistryManifest,
    *,
    canonical_source: str,
    source_id: str,
    source_name: str,
    source_kind: str,
) -> list[dict[str, Any]]:
    require_manifest_source(manifest, canonical_source)
    plugins: list[dict[str, Any]] = []
    for item in manifest.plugins:
        plugins.append(
            {
                "id": item.plugin_id,
                "name": item.name,
                "version": item.version,
                "description": item.description,
                "source_id": source_id,
                "source_name": source_name,
                "source": canonical_source,
                "source_kind": source_kind,
                "ref": item.ref,
                "resolved_commit": item.commit,
                "subdirectory": item.subdirectory,
            }
        )
    return plugins
