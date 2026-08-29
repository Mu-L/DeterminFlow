from __future__ import annotations

import base64
import json
from pathlib import Path
import subprocess

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import pytest

from src.plugin_system.integrity import plugin_content_sha256
from src.plugin_system.registry import (
    extract_plugin_zip,
    parse_registry_manifest,
    verify_manifest_signature,
)
from src.plugin_system.registry_release import (
    PluginRegistryReleaseError,
    R2RegistryPublisher,
    build_registry,
    decode_ed25519_private_key,
    publish_registry,
)


SOURCE_URL = "https://github.com/alikon-art/DeterminFlow-Plugins.git"
REGISTRY_URL = "https://downloads.determinflow.com/plugins/v1"


def _git(repository: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", *arguments],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _repository(tmp_path: Path, *, symlink: bool = False) -> tuple[Path, str]:
    repository = tmp_path / "plugins"
    repository.mkdir()
    _git(repository, "init", "-b", "main")
    _git(repository, "config", "user.name", "Registry Test")
    _git(repository, "config", "user.email", "registry@example.invalid")
    (repository / "plugin-repository.toml").write_text(
        'schema_version = "1"\n\n'
        "[[plugins]]\n"
        'id = "demo-plugin"\n'
        'subdirectory = "plugins/demo-plugin"\n',
        encoding="utf-8",
    )
    package = repository / "plugins" / "demo-plugin"
    package.mkdir(parents=True)
    (package / "extension.toml").write_text(
        "[extension]\n"
        'id = "demo-plugin"\n'
        'name = "Demo Plugin"\n'
        'version = "1.2.3"\n'
        'api_version = "1"\n'
        'description = "Registry package"\n',
        encoding="utf-8",
    )
    executable = package / "run.sh"
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o755)
    if symlink:
        (package / "link").symlink_to("run.sh")
    _git(repository, "add", ".")
    _git(repository, "commit", "-m", "test: add Plugin")
    return repository, _git(repository, "rev-parse", "HEAD")


def test_registry_build_is_deterministic_and_client_verifiable(tmp_path: Path) -> None:
    repository, commit = _repository(tmp_path)
    private_key = Ed25519PrivateKey.generate()
    first = tmp_path / "first"
    second = tmp_path / "second"

    manifest = build_registry(
        repository=repository,
        source_url=SOURCE_URL,
        ref="main",
        output=first,
        public_base_url=REGISTRY_URL,
        private_key=private_key,
    )
    build_registry(
        repository=repository,
        source_url=SOURCE_URL,
        ref="main",
        output=second,
        public_base_url=REGISTRY_URL,
        private_key=private_key,
    )

    assert manifest["resolved_commit"] == commit
    manifest_bytes = (first / "manifest.json").read_bytes()
    signature = (first / "manifest.json.sig").read_bytes()
    verify_manifest_signature(
        manifest_bytes,
        signature,
        private_key.public_key().public_bytes_raw(),
    )
    parsed = parse_registry_manifest(
        json.loads(manifest_bytes),
        registry_url=REGISTRY_URL,
    )
    assert parsed.source == SOURCE_URL
    assert parsed.plugins[0].ref == "main"
    assert parsed.plugins[0].commit == commit
    package_relative = parsed.plugins[0].package_url.removeprefix(f"{REGISTRY_URL}/")
    assert (first / package_relative).read_bytes() == (second / package_relative).read_bytes()
    assert manifest_bytes == (second / "manifest.json").read_bytes()
    assert signature == (second / "manifest.json.sig").read_bytes()

    extracted = tmp_path / "extracted"
    extract_plugin_zip((first / package_relative).read_bytes(), extracted)
    assert plugin_content_sha256(extracted) == parsed.plugins[0].content_sha256
    assert (extracted / "run.sh").stat().st_mode & 0o111


def test_registry_build_rejects_git_symlinks(tmp_path: Path) -> None:
    repository, _ = _repository(tmp_path, symlink=True)

    with pytest.raises(PluginRegistryReleaseError, match="regular files"):
        build_registry(
            repository=repository,
            source_url=SOURCE_URL,
            ref="main",
            output=tmp_path / "registry",
            public_base_url=REGISTRY_URL,
            private_key=Ed25519PrivateKey.generate(),
        )


def test_private_key_decoder_accepts_base64_seed_and_rejects_wrong_size() -> None:
    private_key = Ed25519PrivateKey.generate()
    encoded = base64.b64encode(private_key.private_bytes_raw()).decode("ascii")

    decoded = decode_ed25519_private_key(encoded)

    assert decoded.public_key().public_bytes_raw() == private_key.public_key().public_bytes_raw()
    with pytest.raises(PluginRegistryReleaseError, match="32-byte"):
        decode_ed25519_private_key(base64.b64encode(b"short").decode("ascii"))


def test_registry_publish_updates_manifest_last(tmp_path: Path) -> None:
    repository, commit = _repository(tmp_path)
    registry = tmp_path / "registry"
    build_registry(
        repository=repository,
        source_url=SOURCE_URL,
        ref="main",
        output=registry,
        public_base_url=REGISTRY_URL,
        private_key=Ed25519PrivateKey.generate(),
    )
    calls: list[tuple[str, str]] = []

    class RecordingPublisher:
        public_base_url = "https://downloads.determinflow.com"

        def publish_immutable(self, _path: Path, key: str) -> None:
            calls.append(("immutable", key))

        def publish_latest(self, _path: Path, key: str) -> None:
            calls.append(("latest", key))

    publish_registry(
        registry_dir=registry,
        prefix="plugins/v1",
        publisher=RecordingPublisher(),  # type: ignore[arg-type]
    )

    assert calls[-2:] == [
        ("latest", "plugins/v1/manifest.json.sig"),
        ("latest", "plugins/v1/manifest.json"),
    ]
    assert ("immutable", f"plugins/v1/snapshots/{commit}/manifest.json") in calls
    assert all(kind == "immutable" for kind, _ in calls[:-2])


def test_registry_publish_rejects_changed_immutable_object(tmp_path: Path) -> None:
    asset = tmp_path / "package.zip"
    asset.write_bytes(b"package")

    def runner(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=json.dumps(
                {"ContentLength": 7, "Metadata": {"sha256": "different"}}
            ),
            stderr="",
        )

    publisher = R2RegistryPublisher(
        bucket="downloads",
        endpoint_url="https://example.r2.cloudflarestorage.com",
        public_base_url="https://downloads.determinflow.com",
        runner=runner,
    )

    with pytest.raises(PluginRegistryReleaseError, match="immutable R2 object"):
        publisher.publish_immutable(asset, "plugins/v1/packages/demo/package.zip")
