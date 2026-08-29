"""Build and publish deterministic official Plugin Registry v1 snapshots."""

from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import json
import mimetypes
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import tempfile
import tomllib
from typing import Callable
from urllib.parse import quote
from urllib.request import Request, urlopen
import zipfile

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from .integrity import plugin_content_sha256
from .models import (
    validate_plugin_id,
    validate_plugin_ref,
    validate_plugin_subdirectory,
)
from .registry import (
    MAX_PACKAGE_FILES,
    MAX_UNCOMPRESSED_BYTES,
    canonicalize_registry_url,
    parse_registry_manifest,
    verify_manifest_signature,
)
from .source_selection import canonicalize_plugin_source


IMMUTABLE_CACHE_CONTROL = "public, max-age=31536000, immutable"
LATEST_CACHE_CONTROL = "no-cache, no-store, must-revalidate"
_COMMIT_RE = re.compile(r"^[0-9a-f]{40,64}$")
_GIT_TREE_RECORD_RE = re.compile(
    rb"(?P<mode>[0-7]{6}) (?P<kind>[a-z]+) (?P<object>[0-9a-f]{40,64})\t(?P<path>.*)"
)


class PluginRegistryReleaseError(RuntimeError):
    """Raised when a Registry snapshot cannot be built or published safely."""


def _run_git(repository: Path, *arguments: str) -> bytes:
    try:
        return subprocess.run(
            ["git", *arguments],
            cwd=repository,
            check=True,
            capture_output=True,
            timeout=120,
        ).stdout
    except (OSError, subprocess.SubprocessError) as exc:
        raise PluginRegistryReleaseError(f"Git command failed: {' '.join(arguments)}") from exc


def _resolve_commit(repository: Path, ref: str) -> str:
    normalized_ref = validate_plugin_ref(ref)
    commit = _run_git(
        repository,
        "rev-parse",
        "--verify",
        "--end-of-options",
        f"{normalized_ref}^{{commit}}",
    ).decode("ascii").strip().lower()
    if not _COMMIT_RE.fullmatch(commit):
        raise PluginRegistryReleaseError(f"Git ref does not resolve to a commit: {ref}")
    return commit


def _git_file(repository: Path, commit: str, path: str) -> bytes:
    return _run_git(repository, "show", f"{commit}:{path}")


def _repository_entries(
    repository: Path,
    commit: str,
    *,
    default_ref: str,
) -> list[dict[str, str]]:
    try:
        document = tomllib.loads(
            _git_file(repository, commit, "plugin-repository.toml").decode("utf-8")
        )
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise PluginRegistryReleaseError("plugin-repository.toml is invalid") from exc
    if document.get("schema_version") != "1":
        raise PluginRegistryReleaseError("plugin repository schema is unsupported")
    raw_plugins = document.get("plugins")
    if not isinstance(raw_plugins, list) or not raw_plugins:
        raise PluginRegistryReleaseError("plugin repository index is empty")
    entries: list[dict[str, str]] = []
    seen: set[str] = set()
    for raw in raw_plugins:
        if not isinstance(raw, dict):
            raise PluginRegistryReleaseError("plugin repository entry must be a table")
        try:
            plugin_id = validate_plugin_id(raw["id"])
            subdirectory = validate_plugin_subdirectory(raw.get("subdirectory", ""))
            ref = validate_plugin_ref(raw.get("ref", default_ref))
        except (KeyError, TypeError, ValueError) as exc:
            raise PluginRegistryReleaseError("plugin repository entry is invalid") from exc
        if plugin_id in seen:
            raise PluginRegistryReleaseError(f"duplicate Plugin id: {plugin_id}")
        seen.add(plugin_id)
        entries.append({"id": plugin_id, "subdirectory": subdirectory, "ref": ref})
    return entries


def _safe_relative_path(raw_path: str, subdirectory: str) -> PurePosixPath:
    prefix = f"{subdirectory}/" if subdirectory else ""
    if prefix and not raw_path.startswith(prefix):
        raise PluginRegistryReleaseError("Git tree entry escapes Plugin subdirectory")
    relative = raw_path[len(prefix) :] if prefix else raw_path
    path = PurePosixPath(relative)
    if not relative or path.is_absolute() or any(
        part in {"", ".", ".."} for part in path.parts
    ):
        raise PluginRegistryReleaseError("Git tree contains an unsafe path")
    if any(part in {".git", "__pycache__"} for part in path.parts):
        raise PluginRegistryReleaseError("Plugin package contains forbidden runtime files")
    if path.suffix in {".pyc", ".pyo"}:
        raise PluginRegistryReleaseError("Plugin package contains Python bytecode")
    return path


def _export_plugin(
    repository: Path,
    commit: str,
    subdirectory: str,
    destination: Path,
) -> None:
    arguments = ["ls-tree", "-rz", "--full-tree", commit]
    if subdirectory:
        arguments.extend(["--", subdirectory])
    records = [record for record in _run_git(repository, *arguments).split(b"\0") if record]
    if not records:
        raise PluginRegistryReleaseError("Plugin subdirectory is empty or missing")
    if len(records) > MAX_PACKAGE_FILES:
        raise PluginRegistryReleaseError("Plugin package has too many files")
    destination.mkdir(parents=True)
    total = 0
    for record in records:
        match = _GIT_TREE_RECORD_RE.fullmatch(record)
        if match is None:
            raise PluginRegistryReleaseError("Git tree record is invalid")
        mode = match.group("mode").decode("ascii")
        kind = match.group("kind").decode("ascii")
        if kind != "blob" or mode not in {"100644", "100755"}:
            raise PluginRegistryReleaseError("Plugin package may contain only regular files")
        raw_path = match.group("path").decode("utf-8")
        relative = _safe_relative_path(raw_path, subdirectory)
        payload = _run_git(
            repository,
            "cat-file",
            "blob",
            match.group("object").decode("ascii"),
        )
        total += len(payload)
        if total > MAX_UNCOMPRESSED_BYTES:
            raise PluginRegistryReleaseError("Plugin package exceeds size limit")
        target = destination.joinpath(*relative.parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
        target.chmod(0o755 if mode == "100755" else 0o644)


def _plugin_metadata(package: Path, expected_id: str) -> dict[str, str]:
    manifest_path = package / "extension.toml"
    try:
        with manifest_path.open("rb") as handle:
            document = tomllib.load(handle)
        extension = document["extension"]
    except (OSError, KeyError, TypeError, tomllib.TOMLDecodeError) as exc:
        raise PluginRegistryReleaseError(f"Plugin manifest is invalid: {expected_id}") from exc
    if extension.get("id") != expected_id:
        raise PluginRegistryReleaseError(f"Plugin manifest id mismatch: {expected_id}")
    metadata: dict[str, str] = {}
    for field in ("name", "version"):
        value = extension.get(field)
        if not isinstance(value, str) or not value.strip():
            raise PluginRegistryReleaseError(
                f"Plugin manifest field is invalid: {expected_id}.{field}"
            )
        metadata[field] = value.strip()
    description = extension.get("description", "")
    if not isinstance(description, str):
        raise PluginRegistryReleaseError(
            f"Plugin manifest field is invalid: {expected_id}.description"
        )
    metadata["description"] = description.strip()
    return metadata


def _write_deterministic_zip(package: Path, output: Path) -> None:
    files = sorted(path for path in package.rglob("*") if path.is_file())
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in files:
            relative = path.relative_to(package).as_posix()
            info = zipfile.ZipInfo(relative, date_time=(1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            executable = bool(path.stat().st_mode & stat.S_IXUSR)
            info.external_attr = (
                (stat.S_IFREG | (0o755 if executable else 0o644)) << 16
            )
            info.compress_type = zipfile.ZIP_DEFLATED
            info.flag_bits |= 0x800
            archive.writestr(info, path.read_bytes(), compresslevel=9)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def decode_ed25519_private_key(value: str) -> Ed25519PrivateKey:
    compact = "".join(value.split())
    try:
        raw = (
            bytes.fromhex(compact)
            if re.fullmatch(r"[0-9a-fA-F]{64}", compact)
            else base64.b64decode(compact, validate=True)
        )
    except (ValueError, binascii.Error) as exc:
        raise PluginRegistryReleaseError("Registry signing key encoding is invalid") from exc
    if len(raw) != 32:
        raise PluginRegistryReleaseError("Registry signing key must be a 32-byte seed")
    return Ed25519PrivateKey.from_private_bytes(raw)


def build_registry(
    *,
    repository: Path,
    source_url: str,
    ref: str,
    output: Path,
    public_base_url: str,
    private_key: Ed25519PrivateKey,
) -> dict[str, object]:
    repository = repository.resolve()
    if not (repository / ".git").exists():
        raise PluginRegistryReleaseError("Plugin repository is not a Git checkout")
    canonical_source, source_kind, _ = canonicalize_plugin_source(source_url)
    if source_kind != "git":
        raise PluginRegistryReleaseError("Registry source identity must be a Git URL")
    registry_root = canonicalize_registry_url(public_base_url)
    resolved_commit = _resolve_commit(repository, ref)
    entries = _repository_entries(
        repository,
        resolved_commit,
        default_ref=validate_plugin_ref(ref),
    )
    if output.exists():
        raise PluginRegistryReleaseError("Registry output directory already exists")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix="plugin-registry-", dir=output.parent))
    try:
        plugins: list[dict[str, object]] = []
        for entry in entries:
            plugin_commit = (
                resolved_commit
                if entry["ref"] in {"HEAD", ref}
                else _resolve_commit(repository, entry["ref"])
            )
            package = temporary / "work" / entry["id"]
            _export_plugin(repository, plugin_commit, entry["subdirectory"], package)
            metadata = _plugin_metadata(package, entry["id"])
            content_sha256 = plugin_content_sha256(package)
            relative_package = (
                Path("packages")
                / entry["id"]
                / f"{plugin_commit}-{content_sha256[:16]}.zip"
            )
            package_path = temporary / relative_package
            package_path.parent.mkdir(parents=True, exist_ok=True)
            _write_deterministic_zip(package, package_path)
            package_url = (
                f"{registry_root.rstrip('/')}/"
                + "/".join(quote(part, safe="") for part in relative_package.parts)
            )
            plugins.append(
                {
                    "id": entry["id"],
                    **metadata,
                    "subdirectory": entry["subdirectory"],
                    "ref": entry["ref"],
                    "commit": plugin_commit,
                    "content_sha256": content_sha256,
                    "package": {
                        "url": package_url,
                        "sha256": _sha256(package_path),
                    },
                }
            )
        manifest: dict[str, object] = {
            "schema_version": 1,
            "source": canonical_source,
            "ref": validate_plugin_ref(ref),
            "resolved_commit": resolved_commit,
            "plugins": plugins,
        }
        manifest_bytes = (
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        ).encode("utf-8")
        signature = private_key.sign(manifest_bytes)
        verify_manifest_signature(
            manifest_bytes,
            signature,
            private_key.public_key().public_bytes_raw(),
        )
        parse_registry_manifest(
            json.loads(manifest_bytes),
            registry_url=registry_root,
        )
        (temporary / "manifest.json").write_bytes(manifest_bytes)
        (temporary / "manifest.json.sig").write_text(
            base64.b64encode(signature).decode("ascii") + "\n",
            encoding="ascii",
        )
        shutil.rmtree(temporary / "work")
        os.replace(temporary, output)
        return manifest
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def _public_object_url(public_base_url: str, key: str) -> str:
    base = canonicalize_registry_url(public_base_url).rstrip("/")
    return f"{base}/" + "/".join(quote(part, safe="") for part in key.split("/"))


class R2RegistryPublisher:
    def __init__(
        self,
        *,
        bucket: str,
        endpoint_url: str,
        public_base_url: str,
        aws_binary: str = "aws",
        runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
        fetcher: Callable[..., object] = urlopen,
    ) -> None:
        self.bucket = bucket
        self.endpoint_url = canonicalize_registry_url(endpoint_url)
        self.public_base_url = canonicalize_registry_url(public_base_url)
        self.aws_binary = aws_binary
        self.runner = runner
        self.fetcher = fetcher

    def _run(self, *arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        return self.runner(
            [
                self.aws_binary,
                "--endpoint-url",
                self.endpoint_url,
                "s3api",
                *arguments,
            ],
            check=check,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )

    def _head(self, key: str) -> dict[str, object] | None:
        result = self._run(
            "head-object", "--bucket", self.bucket, "--key", key, check=False
        )
        if result.returncode == 0:
            return json.loads(result.stdout)
        if any(marker in result.stderr for marker in ("404", "Not Found", "NoSuchKey")):
            return None
        raise PluginRegistryReleaseError(f"R2 head-object failed: {key}")

    def _put(self, path: Path, key: str, cache_control: str) -> None:
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        self._run(
            "put-object",
            "--bucket",
            self.bucket,
            "--key",
            key,
            "--body",
            str(path),
            "--content-type",
            content_type,
            "--cache-control",
            cache_control,
            "--metadata",
            f"sha256={_sha256(path)}",
        )

    def _verify_public(self, path: Path, key: str) -> None:
        expected = _sha256(path)
        request = Request(
            f"{_public_object_url(self.public_base_url, key)}?sha256={expected}",
            headers={"Cache-Control": "no-cache"},
        )
        with self.fetcher(request, timeout=30) as response:
            payload = response.read()
        if hashlib.sha256(payload).hexdigest() != expected:
            raise PluginRegistryReleaseError(f"R2 public checksum mismatch: {key}")

    def publish_immutable(self, path: Path, key: str) -> None:
        digest = _sha256(path)
        existing = self._head(key)
        if existing is not None:
            metadata = existing.get("Metadata") or {}
            stored_digest = metadata.get("sha256") if isinstance(metadata, dict) else None
            if stored_digest != digest or existing.get("ContentLength") != path.stat().st_size:
                raise PluginRegistryReleaseError(
                    f"immutable R2 object has different content: {key}"
                )
        else:
            self._put(path, key, IMMUTABLE_CACHE_CONTROL)
        self._verify_public(path, key)

    def publish_latest(self, path: Path, key: str) -> None:
        self._put(path, key, LATEST_CACHE_CONTROL)
        self._verify_public(path, key)


def publish_registry(
    *,
    registry_dir: Path,
    prefix: str,
    publisher: R2RegistryPublisher,
) -> None:
    registry_dir = registry_dir.resolve()
    manifest_path = registry_dir / "manifest.json"
    signature_path = registry_dir / "manifest.json.sig"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        resolved_commit = manifest["resolved_commit"]
    except (OSError, KeyError, json.JSONDecodeError) as exc:
        raise PluginRegistryReleaseError("Registry output manifest is invalid") from exc
    if not isinstance(resolved_commit, str) or not _COMMIT_RE.fullmatch(resolved_commit):
        raise PluginRegistryReleaseError("Registry resolved commit is invalid")
    normalized_prefix = prefix.strip("/")
    expected_registry_root = _public_object_url(
        publisher.public_base_url, normalized_prefix
    ).rstrip("/")
    expected_packages: dict[str, str] = {}
    for plugin in manifest.get("plugins", []):
        package_url = plugin.get("package", {}).get("url") if isinstance(plugin, dict) else None
        if not isinstance(package_url, str) or not package_url.startswith(
            f"{expected_registry_root}/packages/"
        ):
            raise PluginRegistryReleaseError("Registry package URL does not match R2 prefix")
        package_relative = package_url.removeprefix(f"{expected_registry_root}/")
        expected_hash = plugin.get("package", {}).get("sha256")
        if not isinstance(expected_hash, str):
            raise PluginRegistryReleaseError("Registry package SHA256 is invalid")
        expected_packages[package_relative] = expected_hash
    packages = sorted((registry_dir / "packages").rglob("*.zip"))
    if {package.relative_to(registry_dir).as_posix() for package in packages} != set(
        expected_packages
    ):
        raise PluginRegistryReleaseError("Registry package set does not match manifest")
    for package in packages:
        relative = package.relative_to(registry_dir).as_posix()
        if _sha256(package) != expected_packages[relative]:
            raise PluginRegistryReleaseError(f"Registry package checksum mismatch: {relative}")
        publisher.publish_immutable(package, f"{normalized_prefix}/{relative}")
    snapshot_prefix = f"{normalized_prefix}/snapshots/{resolved_commit}"
    publisher.publish_immutable(manifest_path, f"{snapshot_prefix}/manifest.json")
    publisher.publish_immutable(signature_path, f"{snapshot_prefix}/manifest.json.sig")
    publisher.publish_latest(signature_path, f"{normalized_prefix}/manifest.json.sig")
    publisher.publish_latest(manifest_path, f"{normalized_prefix}/manifest.json")


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    build = subparsers.add_parser("build")
    build.add_argument("--repository", type=Path, required=True)
    build.add_argument("--source-url", required=True)
    build.add_argument("--ref", default="main")
    build.add_argument("--output", type=Path, required=True)
    build.add_argument("--public-base-url", required=True)
    build.add_argument("--private-key-env", default="PLUGIN_REGISTRY_SIGNING_KEY")

    publish = subparsers.add_parser("publish")
    publish.add_argument("--registry-dir", type=Path, required=True)
    publish.add_argument("--prefix", default="plugins/v1")
    publish.add_argument("--bucket", required=True)
    publish.add_argument("--endpoint-url", required=True)
    publish.add_argument("--public-base-url", required=True)
    publish.add_argument("--aws-binary", default="aws")
    options = parser.parse_args()

    if options.command == "build":
        raw_key = os.environ.get(options.private_key_env, "")
        if not raw_key:
            raise PluginRegistryReleaseError(
                f"Registry signing key environment variable is missing: {options.private_key_env}"
            )
        build_registry(
            repository=options.repository,
            source_url=options.source_url,
            ref=options.ref,
            output=options.output,
            public_base_url=options.public_base_url,
            private_key=decode_ed25519_private_key(raw_key),
        )
        return 0
    publish_registry(
        registry_dir=options.registry_dir,
        prefix=options.prefix,
        publisher=R2RegistryPublisher(
            bucket=options.bucket,
            endpoint_url=options.endpoint_url,
            public_base_url=options.public_base_url,
            aws_binary=options.aws_binary,
        ),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
