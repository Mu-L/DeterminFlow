"""Immutable Git-backed plugin package storage and lock management."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.parse import unquote, urlsplit, urlunsplit
from urllib.request import url2pathname

from .models import (
    PluginLockRecord,
    PluginRevision,
    validate_plugin_id,
    validate_plugin_ref,
    validate_plugin_subdirectory,
    validate_resource_prefix,
)
from .source_selection import select_git_source


LOCK_SCHEMA_VERSION = 1
_COMMIT_RE = re.compile(r"^[0-9a-fA-F]{40,64}$")
_SCP_SOURCE_RE = re.compile(r"^(?P<user>[^/@:\s]+)@(?P<host>[^/:\s]+):(?P<path>.+)$")


class PluginStoreError(RuntimeError):
    """Base error for package installation and lock operations."""


class SourceTrustError(PluginStoreError):
    """Raised when a third-party source lacks explicit risk acknowledgement."""


class InvalidPluginPackageError(PluginStoreError):
    """Raised when a checkout violates the package contract."""


class PluginStore:
    """Install immutable Git revisions and persist desired package state."""

    def __init__(
        self,
        root: Path,
        *,
        official_sources: Iterable[str] = (),
        official_source_mirrors: Mapping[str, Iterable[str]] | None = None,
        git_binary: str = "git",
    ):
        self.root = Path(root).expanduser().resolve()
        self.checkouts_dir = self.root / "checkouts"
        self.staging_dir = self.root / "staging"
        self.lock_path = self.root / "plugins.lock.json"
        self.git_binary = git_binary
        self.root.mkdir(parents=True, exist_ok=True)
        self.checkouts_dir.mkdir(parents=True, exist_ok=True)
        self.staging_dir.mkdir(parents=True, exist_ok=True)
        self._mutex = threading.RLock()
        self._official_sources = frozenset(
            self.canonicalize_source(source)[0] for source in official_sources
        )
        self._official_source_candidates: dict[str, tuple[str, ...]] = {}
        for source, mirrors in (official_source_mirrors or {}).items():
            canonical, _, clone_source = self.canonicalize_source(source)
            candidates = [clone_source]
            for mirror in mirrors:
                candidates.append(self.canonicalize_source(mirror)[2])
            self._official_source_candidates[canonical] = tuple(
                dict.fromkeys(candidates)
            )

    def install(
        self,
        plugin_id: str,
        source: str,
        *,
        ref: str = "HEAD",
        subdirectory: str = "",
        resource_prefix: str | None = None,
        acknowledge_risk: bool = False,
        preflight: Callable[[Path], None] | None = None,
    ) -> PluginLockRecord:
        plugin_id = self._normalize_plugin_id(plugin_id)
        prefix_override = self._normalize_resource_prefix_override(
            resource_prefix
        )
        with self._mutex:
            records = self.read_lock()
            if plugin_id in records:
                raise PluginStoreError(f"plugin already installed: {plugin_id}")
            canonical_source, source_kind, clone_source = self.canonicalize_source(
                source
            )
            trust = self._require_source_trust(canonical_source, acknowledge_risk)
            normalized_subdirectory = self._validate_subdirectory(subdirectory)
            revision = self._materialize(
                plugin_id,
                canonical_source,
                clone_source,
                ref,
                normalized_subdirectory,
                preflight=preflight,
            )
            declared_resource_prefix = self._manifest_resource_prefix(revision)
            effective_resource_prefix = (
                declared_resource_prefix
                if prefix_override is None
                else prefix_override
            )
            self._ensure_resource_prefix_available(
                records,
                plugin_id,
                effective_resource_prefix,
            )
            record = PluginLockRecord(
                plugin_id=plugin_id,
                source=canonical_source,
                source_kind=source_kind,
                trust=trust,
                subdirectory=normalized_subdirectory,
                active_revision=revision,
                resource_prefix=effective_resource_prefix,
                resource_prefix_override=prefix_override,
                pending_action="install",
            )
            records[plugin_id] = record
            self._write_records(records)
            return record

    def update(
        self,
        plugin_id: str,
        *,
        ref: str = "HEAD",
        source: str | None = None,
        subdirectory: str | None = None,
        acknowledge_risk: bool = False,
        preflight: Callable[[Path], None] | None = None,
    ) -> PluginLockRecord:
        plugin_id = self._normalize_plugin_id(plugin_id)
        with self._mutex:
            records = self.read_lock()
            current = self._require_record(records, plugin_id)
            raw_source = source if source is not None else current.source
            canonical_source, source_kind, clone_source = self.canonicalize_source(
                raw_source
            )
            if canonical_source != current.source:
                raise PluginStoreError(
                    "plugin source cannot change during update; install it as a new package"
                )
            trust = self._require_source_trust(canonical_source, acknowledge_risk)
            normalized_subdirectory = (
                current.subdirectory
                if subdirectory is None
                else self._validate_subdirectory(subdirectory)
            )
            if normalized_subdirectory != current.subdirectory:
                raise PluginStoreError(
                    "plugin subdirectory cannot change during update"
                )
            revision = self._materialize(
                plugin_id,
                canonical_source,
                clone_source,
                ref,
                normalized_subdirectory,
                preflight=preflight,
            )
            declared_resource_prefix = self._manifest_resource_prefix(revision)
            effective_resource_prefix = (
                declared_resource_prefix
                if current.resource_prefix_override is None
                else current.resource_prefix_override
            )
            self._ensure_resource_prefix_available(
                records,
                plugin_id,
                effective_resource_prefix,
            )
            history = self._prepend_unique_revision(
                current.active_revision,
                current.history,
                exclude=revision,
            )
            record = PluginLockRecord(
                plugin_id=plugin_id,
                source=canonical_source,
                source_kind=source_kind,
                trust=trust,
                subdirectory=normalized_subdirectory,
                active_revision=revision,
                resource_prefix=effective_resource_prefix,
                resource_prefix_override=current.resource_prefix_override,
                history=history,
                pending_action="update",
            )
            records[plugin_id] = record
            self._write_records(records)
            return record

    def rollback(
        self,
        plugin_id: str,
        *,
        commit: str | None = None,
        preflight: Callable[[Path], None] | None = None,
    ) -> PluginLockRecord:
        plugin_id = self._normalize_plugin_id(plugin_id)
        with self._mutex:
            records = self.read_lock()
            current = self._require_record(records, plugin_id)
            if not current.history:
                raise PluginStoreError(f"plugin has no rollback revision: {plugin_id}")
            target = next(
                (
                    revision
                    for revision in current.history
                    if commit is None or revision.commit == commit
                ),
                None,
            )
            if target is None:
                raise PluginStoreError(
                    f"plugin rollback revision not found: {plugin_id}/{commit}"
                )
            if not Path(target.checkout_path).is_dir():
                raise PluginStoreError(
                    f"plugin rollback checkout is missing: {target.checkout_path}"
                )
            if preflight is not None:
                preflight(Path(target.checkout_path))
            declared_resource_prefix = self._manifest_resource_prefix(target)
            effective_resource_prefix = (
                declared_resource_prefix
                if current.resource_prefix_override is None
                else current.resource_prefix_override
            )
            self._ensure_resource_prefix_available(
                records,
                plugin_id,
                effective_resource_prefix,
            )
            remaining = tuple(item for item in current.history if item != target)
            history = self._prepend_unique_revision(
                current.active_revision,
                remaining,
                exclude=target,
            )
            record = PluginLockRecord(
                plugin_id=current.plugin_id,
                source=current.source,
                source_kind=current.source_kind,
                trust=current.trust,
                subdirectory=current.subdirectory,
                active_revision=target,
                resource_prefix=effective_resource_prefix,
                resource_prefix_override=current.resource_prefix_override,
                history=history,
                pending_action="rollback",
            )
            records[plugin_id] = record
            self._write_records(records)
            return record

    def mark_uninstall(self, plugin_id: str) -> PluginLockRecord:
        return self._set_pending_action(plugin_id, "remove")

    def apply_pending(self) -> dict[str, PluginLockRecord]:
        """Apply desired actions at process startup without deleting plugin data."""
        with self._mutex:
            current = self.read_lock()
            applied: dict[str, PluginLockRecord] = {}
            changed = False
            for plugin_id, record in current.items():
                if record.pending_remove:
                    changed = True
                    continue
                if record.pending_action is not None:
                    changed = True
                    record = PluginLockRecord(
                        plugin_id=record.plugin_id,
                        source=record.source,
                        source_kind=record.source_kind,
                        trust=record.trust,
                        subdirectory=record.subdirectory,
                        active_revision=record.active_revision,
                        resource_prefix=record.resource_prefix,
                        resource_prefix_override=record.resource_prefix_override,
                        history=record.history,
                        pending_action=None,
                    )
                applied[plugin_id] = record
            if changed:
                self._write_records(applied)
            return applied

    def cancel_uninstall(self, plugin_id: str) -> PluginLockRecord:
        return self._set_pending_action(plugin_id, None)

    def clear_pending_action(self, plugin_id: str) -> PluginLockRecord:
        return self._set_pending_action(plugin_id, None)

    def get(self, plugin_id: str) -> PluginLockRecord | None:
        return self.read_lock().get(self._normalize_plugin_id(plugin_id))

    def read_lock(self) -> dict[str, PluginLockRecord]:
        with self._mutex:
            if not self.lock_path.exists():
                return {}
            try:
                document = json.loads(self.lock_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise PluginStoreError(f"invalid plugin lock: {exc}") from exc
            if (
                not isinstance(document, dict)
                or document.get("schema_version") != LOCK_SCHEMA_VERSION
            ):
                raise PluginStoreError("invalid plugin lock schema")
            raw_plugins = document.get("plugins", {})
            if not isinstance(raw_plugins, dict):
                raise PluginStoreError("invalid plugin lock plugins mapping")
            records: dict[str, PluginLockRecord] = {}
            for raw_id, payload in raw_plugins.items():
                plugin_id = self._normalize_plugin_id(raw_id)
                if not isinstance(payload, dict):
                    raise PluginStoreError(f"invalid plugin lock record: {plugin_id}")
                records[plugin_id] = self._record_from_payload(plugin_id, payload)
            self._validate_resource_prefixes(records)
            return records

    def installed_manifest_paths(self) -> list[Path]:
        manifests: list[Path] = []
        for plugin_id, record in sorted(self.read_lock().items()):
            if record.pending_remove:
                continue
            checkout = self._verify_revision(plugin_id, record.active_revision)
            manifest = checkout / "extension.toml"
            manifests.append(manifest)
        return manifests

    def verify(self, plugin_id: str) -> PluginLockRecord:
        record = self._require_record(
            self.read_lock(),
            self._normalize_plugin_id(plugin_id),
        )
        self._verify_revision(record.plugin_id, record.active_revision)
        return record

    def snapshot(self) -> dict[str, Any]:
        """Return a deterministic desired-state document suitable for hashing."""
        return self._document_from_records(self.read_lock())

    def _set_pending_action(
        self,
        plugin_id: str,
        action: str | None,
    ) -> PluginLockRecord:
        plugin_id = self._normalize_plugin_id(plugin_id)
        with self._mutex:
            records = self.read_lock()
            current = self._require_record(records, plugin_id)
            record = PluginLockRecord(
                plugin_id=current.plugin_id,
                source=current.source,
                source_kind=current.source_kind,
                trust=current.trust,
                subdirectory=current.subdirectory,
                active_revision=current.active_revision,
                resource_prefix=current.resource_prefix,
                resource_prefix_override=current.resource_prefix_override,
                history=current.history,
                pending_action=action,
            )
            records[plugin_id] = record
            self._write_records(records)
            return record

    def _materialize(
        self,
        plugin_id: str,
        canonical_source: str,
        clone_source: str,
        ref: str,
        subdirectory: str,
        *,
        preflight: Callable[[Path], None] | None = None,
    ) -> PluginRevision:
        requested_ref = self._validate_ref(ref)
        stage_root = Path(tempfile.mkdtemp(prefix=f"{plugin_id}-", dir=self.staging_dir))
        clone_root = stage_root / "repository"
        prepared = stage_root / "package"
        try:
            candidates = self._official_source_candidates.get(
                canonical_source,
                (clone_source,),
            )
            selected = select_git_source(
                candidates,
                requested_ref,
                git_binary=self.git_binary,
            )
            self._run_git(
                "clone",
                "--quiet",
                "--no-checkout",
                "--",
                selected.url,
                str(clone_root),
            )
            commit = self._resolve_ref(clone_root, requested_ref)
            if selected.commit and commit != selected.commit:
                raise RuntimeError("Plugin 镜像在拉取期间发生版本漂移")
            self._run_git(
                "-C",
                str(clone_root),
                "checkout",
                "--quiet",
                "--detach",
                commit,
            )
            package_root = clone_root / subdirectory if subdirectory else clone_root
            self._validate_package_root(plugin_id, clone_root, package_root)
            shutil.copytree(
                package_root,
                prepared,
                symlinks=True,
                ignore=shutil.ignore_patterns(".git"),
            )
            digest = self._content_sha256(prepared)
            destination = (
                self.checkouts_dir
                / plugin_id
                / f"{commit}-{digest[:16]}"
            )
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists():
                if self._content_sha256(destination) != digest:
                    raise PluginStoreError(
                        f"content-addressed checkout mismatch: {destination}"
                    )
            else:
                os.replace(prepared, destination)
            if preflight is not None:
                preflight(destination)
            return PluginRevision(
                commit=commit,
                content_sha256=digest,
                checkout_path=str(destination.resolve()),
                requested_ref=requested_ref,
            )
        except PluginStoreError:
            raise
        except Exception as exc:
            raise PluginStoreError(f"failed to install plugin {plugin_id}: {exc}") from exc
        finally:
            shutil.rmtree(stage_root, ignore_errors=True)

    def _resolve_ref(self, repository: Path, ref: str) -> str:
        candidates = [ref]
        if not ref.startswith("refs/") and ref != "HEAD":
            candidates.append(f"refs/remotes/origin/{ref}")
        for candidate in candidates:
            completed = subprocess.run(
                [
                    self.git_binary,
                    "-C",
                    str(repository),
                    "rev-parse",
                    "--verify",
                    "--end-of-options",
                    f"{candidate}^{{commit}}",
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=60,
            )
            commit = completed.stdout.strip()
            if completed.returncode == 0 and _COMMIT_RE.fullmatch(commit):
                return commit.lower()
        raise PluginStoreError(f"git ref does not resolve to a commit: {ref}")

    def _run_git(self, *args: str) -> None:
        try:
            subprocess.run(
                [self.git_binary, *args],
                check=True,
                capture_output=True,
                text=True,
                timeout=180,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            details = (
                exc.stderr.strip()
                if isinstance(exc, subprocess.CalledProcessError) and exc.stderr
                else str(exc)
            )
            raise PluginStoreError(f"git command failed: {details}") from exc

    def _validate_package_root(
        self,
        plugin_id: str,
        clone_root: Path,
        package_root: Path,
    ) -> None:
        try:
            resolved_clone = clone_root.resolve(strict=True)
            resolved_package = package_root.resolve(strict=True)
            resolved_package.relative_to(resolved_clone)
        except (OSError, ValueError) as exc:
            raise InvalidPluginPackageError(
                "plugin subdirectory does not exist or escapes repository"
            ) from exc
        if not resolved_package.is_dir():
            raise InvalidPluginPackageError("plugin subdirectory must be a directory")
        for path in resolved_package.rglob("*"):
            if path.name == "__pycache__" or path.suffix in {".pyc", ".pyo"}:
                raise InvalidPluginPackageError(
                    f"plugin package cannot contain Python bytecode cache: {path}"
                )
            if not path.is_symlink():
                continue
            try:
                path.resolve(strict=True).relative_to(resolved_package)
            except (OSError, ValueError) as exc:
                raise InvalidPluginPackageError(
                    f"plugin symlink escapes package root: {path}"
                ) from exc
        manifest_path = resolved_package / "extension.toml"
        if not manifest_path.is_file():
            raise InvalidPluginPackageError(
                f"plugin extension.toml is missing: {resolved_package}"
            )
        try:
            with manifest_path.open("rb") as handle:
                manifest = tomllib.load(handle)
            extension_id = manifest.get("extension", {}).get("id")
        except (OSError, tomllib.TOMLDecodeError, AttributeError) as exc:
            raise InvalidPluginPackageError(
                f"plugin extension.toml is invalid: {exc}"
            ) from exc
        if extension_id != plugin_id:
            raise InvalidPluginPackageError(
                f"manifest plugin id {extension_id!r} does not match {plugin_id!r}"
            )

    @staticmethod
    def _normalize_resource_prefix_override(
        resource_prefix: str | None,
    ) -> str | None:
        if resource_prefix is None:
            return None
        try:
            return validate_resource_prefix(resource_prefix, allow_empty=False)
        except ValueError as exc:
            raise PluginStoreError(str(exc)) from exc

    def _manifest_resource_prefix(self, revision: PluginRevision) -> str:
        manifest_path = Path(revision.checkout_path) / "extension.toml"
        try:
            with manifest_path.open("rb") as handle:
                manifest = tomllib.load(handle)
            namespace = manifest.get("resource_namespace", {})
            if not isinstance(namespace, dict):
                raise ValueError("[resource_namespace] must be a TOML table")
            raw_prefix = namespace.get("prefix", "")
            if not isinstance(raw_prefix, str):
                raise ValueError("resource_namespace.prefix must be a string")
            return validate_resource_prefix(raw_prefix)
        except (OSError, tomllib.TOMLDecodeError, ValueError) as exc:
            raise InvalidPluginPackageError(
                f"plugin resource namespace is invalid: {exc}"
            ) from exc

    @staticmethod
    def _ensure_resource_prefix_available(
        records: dict[str, PluginLockRecord],
        plugin_id: str,
        resource_prefix: str,
    ) -> None:
        if not resource_prefix:
            return
        for owner, record in records.items():
            if owner != plugin_id and record.resource_prefix == resource_prefix:
                raise PluginStoreError(
                    f"resource prefix {resource_prefix!r} is already used by "
                    f"plugin {owner!r}"
                )

    @classmethod
    def _validate_resource_prefixes(
        cls,
        records: dict[str, PluginLockRecord],
    ) -> None:
        accepted: dict[str, PluginLockRecord] = {}
        for plugin_id, record in records.items():
            cls._ensure_resource_prefix_available(
                accepted,
                plugin_id,
                record.resource_prefix,
            )
            accepted[plugin_id] = record

    @staticmethod
    def _content_sha256(root: Path) -> str:
        digest = hashlib.sha256()

        def visit(directory: Path) -> None:
            for path in sorted(directory.iterdir(), key=lambda item: item.name):
                if path.name == ".git":
                    continue
                if (
                    path.name == "__pycache__"
                    and path.is_dir()
                    and not path.is_symlink()
                ):
                    continue
                relative = path.relative_to(root).as_posix().encode("utf-8")
                if path.is_symlink():
                    digest.update(b"L\0" + relative + b"\0")
                    digest.update(os.readlink(path).encode("utf-8"))
                    digest.update(b"\0")
                elif path.is_dir():
                    digest.update(b"D\0" + relative + b"\0")
                    visit(path)
                elif path.is_file():
                    executable = b"1" if path.stat().st_mode & 0o111 else b"0"
                    digest.update(b"F\0" + relative + b"\0" + executable + b"\0")
                    with path.open("rb") as handle:
                        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                            digest.update(chunk)
                    digest.update(b"\0")

        visit(root)
        return digest.hexdigest()

    def _write_records(self, records: dict[str, PluginLockRecord]) -> None:
        document = self._document_from_records(records)
        temp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self.root,
                prefix=".plugins.lock.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temp_path = Path(handle.name)
                json.dump(
                    document,
                    handle,
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                )
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, self.lock_path)
        except Exception as exc:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)
            raise PluginStoreError(f"failed to write plugin lock: {exc}") from exc

    def _document_from_records(
        self,
        records: dict[str, PluginLockRecord],
    ) -> dict[str, Any]:
        plugins: dict[str, Any] = {}
        for plugin_id, record in sorted(records.items()):
            plugins[plugin_id] = {
                "source": record.source,
                "source_kind": record.source_kind,
                "trust": record.trust,
                "subdirectory": record.subdirectory,
                "resource_prefix": record.resource_prefix,
                "resource_prefix_override": record.resource_prefix_override,
                "active_revision": self._revision_to_payload(record.active_revision),
                "history": [
                    self._revision_to_payload(revision)
                    for revision in record.history
                ],
                "pending_action": record.pending_action,
            }
        return {"schema_version": LOCK_SCHEMA_VERSION, "plugins": plugins}

    def _record_from_payload(
        self,
        plugin_id: str,
        payload: dict[str, Any],
    ) -> PluginLockRecord:
        try:
            source = str(payload["source"])
            source_kind = str(payload["source_kind"])
            trust = str(payload["trust"])
            subdirectory = self._validate_subdirectory(
                str(payload.get("subdirectory", ""))
            )
            resource_prefix = validate_resource_prefix(
                str(payload.get("resource_prefix", ""))
            )
            active = self._revision_from_payload(payload["active_revision"])
            raw_history = payload.get("history", [])
            if not isinstance(raw_history, list):
                raise TypeError("history must be a list")
            history = tuple(self._revision_from_payload(item) for item in raw_history)
            pending_action = payload.get("pending_action")
            if pending_action not in {None, "install", "update", "rollback", "remove"}:
                raise ValueError("invalid pending_action")
            if source_kind not in {"local", "git"}:
                raise ValueError("invalid source_kind")
            if trust not in {"official", "third_party"}:
                raise ValueError("invalid trust")
            resource_prefix_override = self._resource_prefix_override_from_payload(
                payload,
                resource_prefix=resource_prefix,
                active=active,
                history=history,
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise PluginStoreError(
                f"invalid plugin lock record {plugin_id}: {exc}"
            ) from exc
        return PluginLockRecord(
            plugin_id=plugin_id,
            source=source,
            source_kind=source_kind,
            trust=trust,
            subdirectory=subdirectory,
            active_revision=active,
            resource_prefix=resource_prefix,
            resource_prefix_override=resource_prefix_override,
            history=history,
            pending_action=pending_action,
        )

    def _resource_prefix_override_from_payload(
        self,
        payload: dict[str, Any],
        *,
        resource_prefix: str,
        active: PluginRevision,
        history: tuple[PluginRevision, ...],
    ) -> str | None:
        """Read explicit override provenance and migrate older lock records."""
        if "resource_prefix_override" in payload:
            raw_override = payload["resource_prefix_override"]
            if raw_override is None:
                return None
            return validate_resource_prefix(
                str(raw_override),
                allow_empty=False,
            )
        if "resource_prefix" not in payload:
            return None
        declared_prefixes = {
            self._manifest_resource_prefix(revision)
            for revision in (active, *history)
        }
        if resource_prefix in declared_prefixes:
            return None
        return resource_prefix

    def _revision_to_payload(self, revision: PluginRevision) -> dict[str, str]:
        checkout = self._safe_locked_checkout(revision.checkout_path)
        return {
            "commit": revision.commit,
            "content_sha256": revision.content_sha256,
            "checkout": checkout.relative_to(self.root).as_posix(),
            "requested_ref": revision.requested_ref,
        }

    def _revision_from_payload(self, payload: Any) -> PluginRevision:
        if not isinstance(payload, dict):
            raise TypeError("revision must be an object")
        commit = str(payload["commit"])
        digest = str(payload["content_sha256"])
        requested_ref = str(payload["requested_ref"])
        if not _COMMIT_RE.fullmatch(commit):
            raise ValueError("invalid revision commit")
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError("invalid revision content_sha256")
        checkout = self._safe_locked_checkout(str(payload["checkout"]))
        return PluginRevision(
            commit=commit,
            content_sha256=digest,
            checkout_path=str(checkout),
            requested_ref=requested_ref,
        )

    def _safe_locked_checkout(self, value: str) -> Path:
        raw = Path(value)
        checkout = raw if raw.is_absolute() else self.root / raw
        checkout = checkout.resolve()
        try:
            checkout.relative_to(self.checkouts_dir.resolve())
        except ValueError as exc:
            raise PluginStoreError(f"plugin checkout escapes store: {value}") from exc
        return checkout

    def _verify_revision(
        self,
        plugin_id: str,
        revision: PluginRevision,
    ) -> Path:
        checkout = self._safe_locked_checkout(revision.checkout_path)
        if not checkout.is_dir():
            raise PluginStoreError(
                f"installed plugin checkout is missing: {plugin_id}"
            )
        manifest = checkout / "extension.toml"
        if not manifest.is_file():
            raise PluginStoreError(
                f"installed plugin manifest is missing: {plugin_id}"
            )
        actual_digest = self._content_sha256(checkout)
        if actual_digest != revision.content_sha256:
            raise PluginStoreError(
                f"installed plugin content hash mismatch: {plugin_id}"
            )
        return checkout

    @staticmethod
    def _prepend_unique_revision(
        revision: PluginRevision,
        history: tuple[PluginRevision, ...],
        *,
        exclude: PluginRevision,
    ) -> tuple[PluginRevision, ...]:
        values = [revision, *history]
        result: list[PluginRevision] = []
        seen: set[tuple[str, str]] = set()
        excluded_key = (exclude.commit, exclude.content_sha256)
        for item in values:
            key = (item.commit, item.content_sha256)
            if key == excluded_key or key in seen:
                continue
            seen.add(key)
            result.append(item)
        return tuple(result)

    def _require_source_trust(
        self,
        canonical_source: str,
        acknowledge_risk: bool,
    ) -> str:
        if canonical_source in self._official_sources:
            return "official"
        if not acknowledge_risk:
            raise SourceTrustError(
                "third-party plugin source requires acknowledge_risk=true"
            )
        return "third_party"

    @staticmethod
    def canonicalize_source(source: str) -> tuple[str, str, str]:
        raw = str(source).strip()
        if not raw:
            raise PluginStoreError("plugin source cannot be empty")
        split = urlsplit(raw)
        if split.scheme == "file":
            if split.netloc or split.query or split.fragment:
                raise PluginStoreError(
                    "file Git URL must not contain authority, query, or fragment"
                )
            local = Path(url2pathname(unquote(split.path))).expanduser().resolve()
            canonical = local.as_uri()
            return canonical, "local", canonical
        if split.scheme:
            scheme = split.scheme.lower()
            if scheme not in {"git", "http", "https", "ssh"}:
                raise PluginStoreError("unsupported Git URL scheme")
            hostname = (split.hostname or "").lower()
            if not hostname:
                raise PluginStoreError("invalid Git URL")
            if split.password is not None:
                raise PluginStoreError("Git URL must not contain an inline password")
            if split.query or split.fragment:
                raise PluginStoreError(
                    "Git URL must not contain query parameters or fragments"
                )
            if split.username and scheme in {"http", "https"}:
                raise PluginStoreError(
                    "HTTP Git URL must not contain user credentials"
                )
            user = f"{split.username}@" if split.username else ""
            port = f":{split.port}" if split.port is not None else ""
            netloc = f"{user}{hostname}{port}"
            path = split.path.rstrip("/") or "/"
            canonical = urlunsplit((scheme, netloc, path, "", ""))
            return canonical, "git", canonical
        scp_match = _SCP_SOURCE_RE.fullmatch(raw)
        if scp_match:
            canonical = (
                f"{scp_match.group('user')}@{scp_match.group('host').lower()}:"
                f"{scp_match.group('path').rstrip('/')}"
            )
            return canonical, "git", canonical
        local = Path(raw).expanduser().resolve()
        canonical = local.as_uri()
        return canonical, "local", canonical

    @staticmethod
    def _validate_subdirectory(subdirectory: str) -> str:
        try:
            return validate_plugin_subdirectory(subdirectory)
        except ValueError as exc:
            raise InvalidPluginPackageError(str(exc)) from exc

    @staticmethod
    def _validate_ref(ref: str) -> str:
        try:
            return validate_plugin_ref(ref)
        except ValueError as exc:
            raise PluginStoreError(str(exc)) from exc

    @staticmethod
    def _normalize_plugin_id(plugin_id: str) -> str:
        try:
            return validate_plugin_id(plugin_id)
        except ValueError as exc:
            raise PluginStoreError(str(exc)) from exc

    @staticmethod
    def _require_record(
        records: dict[str, PluginLockRecord],
        plugin_id: str,
    ) -> PluginLockRecord:
        record = records.get(plugin_id)
        if record is None:
            raise PluginStoreError(f"plugin is not installed: {plugin_id}")
        return record
