from __future__ import annotations

import base64
import hashlib
import io
import json
import subprocess
import zipfile
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from src.extension_host.source_config import (
    PluginSourceStore,
    fetch_plugin_catalog,
    load_plugin_sources,
)
from src.plugin_system import (
    PluginRegistryConfig,
    PluginRegistryError,
    PluginStore,
    SourceTrustError,
)
from src.plugin_system.integrity import plugin_content_sha256
from src.plugin_system.registry import (
    _validate_zip_entry,
    extract_plugin_zip,
    parse_registry_config,
)
from src.plugin_system import store as store_module
from src.plugin_system.source_selection import GitSourceSelection


REGISTRY_URL = "https://plugins.example.invalid/v1"
CANONICAL_GIT = "https://github.example.invalid/DeterminFlow-Plugins.git"


def _git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _commit(repo: Path, message: str) -> str:
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


def _write_plugin(
    package: Path,
    *,
    plugin_id: str = "demo-plugin",
    payload: str = "one\n",
    version: str = "1.0.0",
) -> None:
    package.mkdir(parents=True, exist_ok=True)
    (package / "extension.toml").write_text(
        "\n".join(
            [
                "[extension]",
                f'id = "{plugin_id}"',
                'name = "Demo Plugin"',
                f'version = "{version}"',
                'api_version = "1"',
                "",
                "[resource_namespace]",
                'prefix = "demo"',
            ]
        ),
        encoding="utf-8",
    )
    (package / "payload.txt").write_text(payload, encoding="utf-8")


def _create_repo(tmp_path: Path, *, subdirectory: str = "") -> tuple[Path, Path, str]:
    repo = tmp_path / "source-demo-plugin"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.name", "Plugin Test")
    _git(repo, "config", "user.email", "plugin-test@example.invalid")
    package = repo / subdirectory if subdirectory else repo
    _write_plugin(package)
    if subdirectory:
        (repo / "plugin-repository.toml").write_text(
            "\n".join(
                [
                    'schema_version = "1"',
                    'name = "Official"',
                    "",
                    "[[plugins]]",
                    'id = "demo-plugin"',
                    f'subdirectory = "{subdirectory}"',
                ]
            ),
            encoding="utf-8",
        )
    return repo, package, _commit(repo, "initial")


def _zip_tree(root: Path) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for path in sorted(root.rglob("*")):
            if not path.is_file() or path.is_symlink():
                continue
            relative = path.relative_to(root).as_posix()
            info = zipfile.ZipInfo(relative)
            info.create_system = 3
            mode = 0o100755 if path.stat().st_mode & 0o111 else 0o100644
            info.external_attr = mode << 16
            archive.writestr(info, path.read_bytes())
    return buffer.getvalue()


def _zip_named(entries: dict[str, bytes], *, symlink: str | None = None) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content in entries.items():
            info = zipfile.ZipInfo(name)
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            archive.writestr(info, content)
        if symlink is not None:
            info = zipfile.ZipInfo(symlink)
            info.create_system = 3
            info.external_attr = 0o120777 << 16
            archive.writestr(info, b"target")
    return buffer.getvalue()


class _FakeHttp:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.offline: set[str] = set()
        self.calls: list[str] = []

    def __call__(self, url: str, *, max_bytes: int, timeout: float = 30.0) -> bytes:
        self.calls.append(url)
        if url in self.offline:
            raise PluginRegistryError("Registry 传输失败: offline")
        if url not in self.objects:
            raise PluginRegistryError(f"Registry 传输失败: missing {url}")
        payload = self.objects[url]
        if len(payload) > max_bytes:
            raise PluginRegistryError("Registry 对象超过大小限制")
        return payload


def _registry_config(public_key: bytes) -> PluginRegistryConfig:
    return parse_registry_config(
        {
            "url": REGISTRY_URL,
            "public_key": base64.b64encode(public_key).decode("ascii"),
        },
        label="official_sources",
    )


def _publish(
    http: _FakeHttp,
    *,
    private_key: Ed25519PrivateKey,
    package: Path,
    commit: str,
    source: str = CANONICAL_GIT,
    subdirectory: str = "",
    plugin_id: str = "demo-plugin",
    version: str = "1.0.0",
    package_bytes: bytes | None = None,
    package_sha256: str | None = None,
    content_sha256: str | None = None,
    signature: bytes | None = None,
) -> tuple[bytes, str]:
    zip_payload = package_bytes if package_bytes is not None else _zip_tree(package)
    digest = (
        content_sha256
        if content_sha256 is not None
        else plugin_content_sha256(package)
    )
    package_url = f"{REGISTRY_URL}/packages/{plugin_id}/{commit}.zip"
    document = {
        "schema_version": 1,
        "source": source,
        "ref": "main",
        "resolved_commit": commit,
        "plugins": [
            {
                "id": plugin_id,
                "name": "Demo Plugin",
                "version": version,
                "description": "Catalog metadata",
                "subdirectory": subdirectory,
                "ref": "main",
                "commit": commit,
                "content_sha256": digest,
                "package": {
                    "url": package_url,
                    "sha256": package_sha256
                    or hashlib.sha256(zip_payload).hexdigest(),
                },
            }
        ],
    }
    manifest = json.dumps(document, ensure_ascii=False, separators=(",", ":")).encode(
        "utf-8"
    )
    http.objects[f"{REGISTRY_URL}/manifest.json"] = manifest
    http.objects[f"{REGISTRY_URL}/manifest.json.sig"] = (
        signature if signature is not None else private_key.sign(manifest)
    )
    http.objects[package_url] = zip_payload
    return manifest, digest


def _store(
    tmp_path: Path,
    *,
    official: str,
    registry: PluginRegistryConfig | None = None,
    http: _FakeHttp | None = None,
    mirrors: dict[str, list[str]] | None = None,
) -> PluginStore:
    return PluginStore(
        tmp_path / "store",
        official_sources=[official],
        official_source_mirrors=mirrors,
        official_registries={official: registry} if registry is not None else None,
        registry_http_get=http,
    )


def test_registry_install_locks_canonical_git_identity_and_exact_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    package = tmp_path / "package"
    _write_plugin(package)
    commit = "a" * 40
    private = Ed25519PrivateKey.generate()
    http = _FakeHttp()
    _publish(
        http,
        private_key=private,
        package=package,
        commit=commit,
        source=CANONICAL_GIT,
        subdirectory="plugins/demo-plugin",
    )
    monkeypatch.setattr(
        store_module,
        "select_git_source",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("official Registry 命中后不得回退 Git")
        ),
    )
    store = _store(
        tmp_path,
        official=CANONICAL_GIT,
        registry=_registry_config(private.public_key().public_bytes_raw()),
        http=http,
    )

    record = store.install(
        "demo-plugin",
        CANONICAL_GIT,
        ref="HEAD",
        subdirectory="plugins/demo-plugin",
    )

    assert record.source == CANONICAL_GIT
    assert record.source_kind == "git"
    assert record.trust == "official"
    assert record.active_revision.commit == commit
    assert record.active_revision.requested_ref == "HEAD"
    assert record.subdirectory == "plugins/demo-plugin"
    assert record.pending_action == "install"
    assert record.active_revision.content_sha256 == plugin_content_sha256(package)
    assert (Path(record.active_revision.checkout_path) / "payload.txt").read_text(
        encoding="utf-8"
    ) == "one\n"


def test_registry_update_and_rollback_keep_restart_and_history_semantics(
    tmp_path: Path,
) -> None:
    repo, package, first_commit = _create_repo(tmp_path)
    private = Ed25519PrivateKey.generate()
    http = _FakeHttp()
    _publish(
        http,
        private_key=private,
        package=package,
        commit=first_commit,
        source=str(repo),
    )
    store = _store(
        tmp_path,
        official=str(repo),
        registry=_registry_config(private.public_key().public_bytes_raw()),
        http=http,
    )
    installed = store.install("demo-plugin", str(repo), ref="main")
    (package / "payload.txt").write_text("two\n", encoding="utf-8")
    second_commit = _commit(repo, "update")
    _publish(
        http,
        private_key=private,
        package=package,
        commit=second_commit,
        source=str(repo),
        version="1.0.1",
    )

    updated = store.update("demo-plugin", ref="main")
    rolled_back = store.rollback("demo-plugin")

    assert updated.active_revision.commit == second_commit
    assert updated.pending_action == "update"
    assert updated.source == installed.source
    assert rolled_back.active_revision.commit == first_commit
    assert rolled_back.pending_action == "rollback"
    assert Path(installed.active_revision.checkout_path).exists()
    assert Path(updated.active_revision.checkout_path).exists()
    assert (Path(rolled_back.active_revision.checkout_path) / "payload.txt").read_text(
        encoding="utf-8"
    ) == "one\n"


def test_invalid_signature_falls_back_to_configured_git(tmp_path: Path) -> None:
    repo, package, commit = _create_repo(tmp_path)
    private = Ed25519PrivateKey.generate()
    other = Ed25519PrivateKey.generate()
    http = _FakeHttp()
    _publish(
        http,
        private_key=private,
        package=package,
        commit=commit,
        source=str(repo),
        signature=other.sign(b"tampered"),
    )
    store = _store(
        tmp_path,
        official=str(repo),
        registry=_registry_config(private.public_key().public_bytes_raw()),
        http=http,
    )

    record = store.install("demo-plugin", str(repo), ref="main")

    assert record.active_revision.commit == commit
    assert record.source == repo.resolve().as_uri()
    assert f"{REGISTRY_URL}/manifest.json" in http.calls


def test_unavailable_registry_falls_back_to_git_mirror(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mirror, _, commit = _create_repo(tmp_path)
    primary = tmp_path / "primary-unavailable"
    private = Ed25519PrivateKey.generate()
    http = _FakeHttp()
    http.offline.add(f"{REGISTRY_URL}/manifest.json")
    expected_primary = primary.resolve().as_uri()
    expected_mirror = mirror.resolve().as_uri()

    def select(urls, ref, **kwargs):
        assert tuple(urls) == (expected_primary, expected_mirror)
        return GitSourceSelection(expected_mirror, commit, 0.1)

    monkeypatch.setattr(store_module, "select_git_source", select)
    store = PluginStore(
        tmp_path / "store",
        official_sources=[str(primary)],
        official_source_mirrors={str(primary): [str(mirror)]},
        official_registries={
            str(primary): _registry_config(private.public_key().public_bytes_raw())
        },
        registry_http_get=http,
    )

    record = store.install("demo-plugin", str(primary), ref="main")

    assert record.source == expected_primary
    assert record.trust == "official"
    assert record.active_revision.commit == commit


def test_package_and_content_hash_mismatch_do_not_install_registry_payload(
    tmp_path: Path,
) -> None:
    repo, package, commit = _create_repo(tmp_path)
    private = Ed25519PrivateKey.generate()
    http = _FakeHttp()
    _publish(
        http,
        private_key=private,
        package=package,
        commit=commit,
        source=str(repo),
        package_sha256="b" * 64,
    )
    store = _store(
        tmp_path,
        official=str(repo),
        registry=_registry_config(private.public_key().public_bytes_raw()),
        http=http,
    )
    git_record = store.install("demo-plugin", str(repo), ref="main")
    assert git_record.active_revision.commit == commit

    store.mark_uninstall("demo-plugin")
    store.apply_pending()
    _publish(
        http,
        private_key=private,
        package=package,
        commit=commit,
        source=str(repo),
        content_sha256="a" * 64,
    )
    record = store.install("demo-plugin", str(repo), ref="main")
    assert record.active_revision.content_sha256 == plugin_content_sha256(package)


def test_third_party_git_sources_ignore_official_registry(tmp_path: Path) -> None:
    repo, package, commit = _create_repo(tmp_path)
    private = Ed25519PrivateKey.generate()
    http = _FakeHttp()
    _publish(
        http,
        private_key=private,
        package=package,
        commit=commit,
        source=CANONICAL_GIT,
    )
    store = PluginStore(
        tmp_path / "store",
        official_registries={
            CANONICAL_GIT: _registry_config(private.public_key().public_bytes_raw())
        },
        registry_http_get=http,
    )

    with pytest.raises(SourceTrustError):
        store.install("demo-plugin", str(repo))
    record = store.install("demo-plugin", str(repo), acknowledge_risk=True)

    assert record.trust == "third_party"
    assert record.active_revision.commit == commit
    assert http.calls == []


def test_catalog_prefers_signed_registry_and_keeps_git_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, package, commit = _create_repo(tmp_path, subdirectory="plugins/demo-plugin")
    private = Ed25519PrivateKey.generate()
    http = _FakeHttp()
    _publish(
        http,
        private_key=private,
        package=package,
        commit=commit,
        source=str(repo),
        subdirectory="plugins/demo-plugin",
        version="1.2.3",
    )
    source_file = tmp_path / "config" / "plugin-sources.json"
    source_file.parent.mkdir(parents=True)
    source_file.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "official_sources": [
                    {
                        "name": "Local Official",
                        "url": str(repo),
                        "ref": "main",
                        "registry": {
                            "url": REGISTRY_URL,
                            "public_key": base64.b64encode(
                                private.public_key().public_bytes_raw()
                            ).decode("ascii"),
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "src.plugin_system.registry.https_get",
        http,
    )

    sources = load_plugin_sources(source_file)
    catalog = fetch_plugin_catalog(sources)

    assert catalog["sources"][0]["transport"] == "registry"
    assert catalog["sources"][0]["selected_url"] == REGISTRY_URL
    assert catalog["sources"][0]["resolved_commit"] == commit
    assert catalog["plugins"] == [
        {
            "id": "demo-plugin",
            "name": "Demo Plugin",
            "version": "1.2.3",
            "description": "Catalog metadata",
            "source_id": catalog["sources"][0]["id"],
            "source_name": "Local Official",
            "source": repo.resolve().as_uri(),
            "source_kind": "official",
            "ref": "main",
            "resolved_commit": commit,
            "subdirectory": "plugins/demo-plugin",
        }
    ]


def test_catalog_falls_back_to_git_when_registry_is_offline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, package, commit = _create_repo(tmp_path, subdirectory="plugins/demo-plugin")
    private = Ed25519PrivateKey.generate()
    http = _FakeHttp()
    http.offline.add(f"{REGISTRY_URL}/manifest.json")
    source_file = tmp_path / "plugin-sources.json"
    source_file.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "official_sources": [
                    {
                        "name": "Local Official",
                        "url": str(repo),
                        "ref": "main",
                        "registry": {
                            "url": REGISTRY_URL,
                            "public_key": base64.b64encode(
                                private.public_key().public_bytes_raw()
                            ).decode("ascii"),
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("src.plugin_system.registry.https_get", http)

    catalog = fetch_plugin_catalog(load_plugin_sources(source_file))

    assert catalog["sources"][0]["transport"] == "git"
    assert catalog["sources"][0]["error"] == ""
    assert catalog["plugins"][0]["id"] == "demo-plugin"
    assert catalog["plugins"][0]["source"] == repo.resolve().as_uri()
    assert catalog["plugins"][0]["resolved_commit"] == commit
    assert package.is_dir()


def test_custom_source_store_preserves_official_registry(tmp_path: Path) -> None:
    source_file = tmp_path / "plugin-sources.json"
    public_key = base64.b64encode(b"\x11" * 32).decode("ascii")
    source_file.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "official_sources": [
                    {
                        "name": "Official",
                        "url": CANONICAL_GIT,
                        "ref": "main",
                        "registry": {
                            "url": REGISTRY_URL,
                            "public_key": public_key,
                        },
                    }
                ],
                "custom_sources": [],
            }
        ),
        encoding="utf-8",
    )
    custom_repo = tmp_path / "custom"
    custom_repo.mkdir()
    store = PluginSourceStore(source_file)
    store.create(name="Team", url=str(custom_repo), ref="main")

    sources = load_plugin_sources(source_file)
    assert sources[0].kind == "official"
    assert sources[0].registry is not None
    assert sources[0].registry.url == REGISTRY_URL
    assert sources[1].kind == "custom"
    assert sources[1].registry is None


def test_custom_source_rejects_registry_config(tmp_path: Path) -> None:
    source_file = tmp_path / "plugin-sources.json"
    source_file.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "official_sources": [],
                "custom_sources": [
                    {
                        "name": "Team",
                        "url": str(tmp_path / "custom"),
                        "registry": {
                            "url": REGISTRY_URL,
                            "public_key": base64.b64encode(b"\x00" * 32).decode(
                                "ascii"
                            ),
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="自定义仓库不能配置官方 Registry"):
        load_plugin_sources(source_file)


def test_zip_rejects_traversal_symlink_and_abnormal_size(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    destination = tmp_path / "extracted"
    with pytest.raises(PluginRegistryError, match="路径穿越"):
        extract_plugin_zip(_zip_named({"../escape.txt": b"nope"}), destination)
    with pytest.raises(PluginRegistryError, match="链接"):
        extract_plugin_zip(
            _zip_named({"payload.txt": b"ok"}, symlink="link"),
            tmp_path / "symlink",
        )
    monkeypatch.setattr(
        "src.plugin_system.registry.MAX_UNCOMPRESSED_BYTES",
        8,
    )
    with pytest.raises(PluginRegistryError, match="体积"):
        extract_plugin_zip(
            _zip_named({"payload.txt": b"0123456789"}),
            tmp_path / "huge",
        )


def test_zip_rejects_encrypted_and_absolute_members(tmp_path: Path) -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        info = zipfile.ZipInfo("/tmp/escape.txt")
        archive.writestr(info, b"nope")
    with pytest.raises(PluginRegistryError, match="绝对路径"):
        extract_plugin_zip(buffer.getvalue(), tmp_path / "absolute")

    encrypted = zipfile.ZipInfo("payload.txt")
    encrypted.flag_bits |= 0x1
    with pytest.raises(PluginRegistryError, match="加密"):
        _validate_zip_entry(encrypted)


def test_manifest_source_mismatch_falls_back_to_git(tmp_path: Path) -> None:
    repo, package, commit = _create_repo(tmp_path)
    private = Ed25519PrivateKey.generate()
    http = _FakeHttp()
    _publish(
        http,
        private_key=private,
        package=package,
        commit=commit,
        source=CANONICAL_GIT,
    )
    store = _store(
        tmp_path,
        official=str(repo),
        registry=_registry_config(private.public_key().public_bytes_raw()),
        http=http,
    )

    record = store.install("demo-plugin", str(repo), ref="main")
    assert record.source == repo.resolve().as_uri()
    assert record.active_revision.commit == commit
