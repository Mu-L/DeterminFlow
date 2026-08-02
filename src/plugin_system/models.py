"""Strongly typed contracts for installable plugin packages and processes."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Mapping


_PLUGIN_ID_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_RESOURCE_PREFIX_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_PROCESS_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")


@dataclass(frozen=True)
class PluginRevision:
    """One immutable, content-addressed plugin checkout."""

    commit: str
    content_sha256: str
    checkout_path: str
    requested_ref: str

    @property
    def resolved_commit(self) -> str:
        return self.commit


@dataclass(frozen=True)
class PluginLockRecord:
    """Desired plugin package state persisted in plugins.lock.json."""

    plugin_id: str
    source: str
    source_kind: str
    trust: str
    subdirectory: str
    active_revision: PluginRevision
    resource_prefix: str = ""
    resource_prefix_override: str | None = None
    history: tuple[PluginRevision, ...] = ()
    pending_action: str | None = None

    @property
    def source_trust(self) -> str:
        return self.trust

    @property
    def pending_remove(self) -> bool:
        return self.pending_action == "remove"


@dataclass(frozen=True)
class ProcessHealthCheck:
    """A minimal process liveness or HTTP readiness check."""

    kind: str = "alive"
    url: str = ""
    interval_seconds: float = 0.1
    request_timeout_seconds: float = 1.0
    expected_status_min: int = 200
    expected_status_max: int = 399

    def __post_init__(self) -> None:
        if self.kind not in {"alive", "http"}:
            raise ValueError(f"unsupported process health kind: {self.kind}")
        if self.kind == "http" and not self.url:
            raise ValueError("http process health check requires url")
        if self.interval_seconds <= 0:
            raise ValueError("health interval_seconds must be positive")
        if self.request_timeout_seconds <= 0:
            raise ValueError("health request_timeout_seconds must be positive")
        if not 100 <= self.expected_status_min <= self.expected_status_max <= 599:
            raise ValueError("invalid expected HTTP status range")


@dataclass(frozen=True)
class ProcessSpec:
    """Declarative argv-based child process configuration."""

    process_id: str
    argv: tuple[str, ...]
    cwd: str = ""
    env: Mapping[str, str] = field(default_factory=dict)
    log_file: str = ""
    health: ProcessHealthCheck = field(default_factory=ProcessHealthCheck)
    startup_timeout_seconds: float = 10.0
    shutdown_timeout_seconds: float = 5.0

    def __post_init__(self) -> None:
        if not _PROCESS_ID_RE.fullmatch(self.process_id):
            raise ValueError(f"invalid process_id: {self.process_id}")
        if not self.argv or any(not isinstance(item, str) or not item for item in self.argv):
            raise ValueError("process argv must contain non-empty strings")
        if any(
            not isinstance(key, str) or not isinstance(value, str)
            for key, value in self.env.items()
        ):
            raise ValueError("process env must be a string mapping")
        if self.startup_timeout_seconds <= 0:
            raise ValueError("startup_timeout_seconds must be positive")
        if self.shutdown_timeout_seconds <= 0:
            raise ValueError("shutdown_timeout_seconds must be positive")


def validate_plugin_id(plugin_id: str) -> str:
    normalized = str(plugin_id).strip()
    if len(normalized) > 128 or not _PLUGIN_ID_RE.fullmatch(normalized):
        raise ValueError(f"invalid plugin id: {plugin_id}")
    return normalized


def validate_plugin_ref(ref: str) -> str:
    normalized = str(ref).strip()
    if (
        not normalized
        or len(normalized) > 512
        or any(
            ord(character) < 32 or ord(character) == 127
            for character in normalized
        )
    ):
        raise ValueError("invalid Git ref")
    return normalized


def validate_resource_prefix(
    resource_prefix: str,
    *,
    allow_empty: bool = True,
) -> str:
    """Normalize a user-visible resource prefix without rewriting resource IDs."""
    normalized = str(resource_prefix).strip()
    if not normalized and allow_empty:
        return ""
    if len(normalized) > 128 or not _RESOURCE_PREFIX_RE.fullmatch(normalized):
        raise ValueError(f"invalid resource prefix: {resource_prefix}")
    return normalized


def validate_plugin_subdirectory(subdirectory: str) -> str:
    raw = str(subdirectory).strip().replace("\\", "/")
    if raw in {"", "."}:
        return ""
    path = PurePosixPath(raw)
    if path.is_absolute() or any(
        part in {"", ".", ".."} for part in path.parts
    ):
        raise ValueError(
            f"plugin subdirectory must be a safe relative path: {subdirectory}"
        )
    return path.as_posix()
