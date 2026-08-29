"""Select a responsive Git transport without weakening revision integrity."""

from __future__ import annotations

import re
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
from urllib.parse import unquote, urlsplit, urlunsplit
from urllib.request import url2pathname


_COMMIT_RE = re.compile(r"^[0-9a-fA-F]{40,64}$")
_SCP_SOURCE_RE = re.compile(r"^(?P<user>[^/@:\s]+)@(?P<host>[^/:\s]+):(?P<path>.+)$")


def canonicalize_plugin_source(source: str) -> tuple[str, str, str]:
    raw = str(source).strip()
    if not raw:
        raise ValueError("plugin source cannot be empty")
    split = urlsplit(raw)
    if split.scheme == "file":
        if split.netloc or split.query or split.fragment:
            raise ValueError(
                "file Git URL must not contain authority, query, or fragment"
            )
        local = Path(url2pathname(unquote(split.path))).expanduser().resolve()
        canonical = local.as_uri()
        return canonical, "local", canonical
    if split.scheme:
        scheme = split.scheme.lower()
        if scheme not in {"git", "http", "https", "ssh"}:
            raise ValueError("unsupported Git URL scheme")
        hostname = (split.hostname or "").lower()
        if not hostname:
            raise ValueError("invalid Git URL")
        if split.password is not None:
            raise ValueError("Git URL must not contain an inline password")
        if split.query or split.fragment:
            raise ValueError(
                "Git URL must not contain query parameters or fragments"
            )
        if split.username and scheme in {"http", "https"}:
            raise ValueError("HTTP Git URL must not contain user credentials")
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


@dataclass(frozen=True)
class GitSourceSelection:
    url: str
    commit: str
    elapsed_seconds: float


@dataclass(frozen=True)
class _GitSourceProbe:
    url: str
    commit: str
    elapsed_seconds: float
    error: str = ""


def _resolve_remote_commit(output: str, ref: str) -> str:
    references: dict[str, str] = {}
    for line in output.splitlines():
        fields = line.split("\t", 1)
        if len(fields) != 2 or fields[0].startswith("ref: "):
            continue
        commit, name = fields
        if _COMMIT_RE.fullmatch(commit):
            references[name] = commit.lower()

    if ref == "HEAD":
        return references.get("HEAD", "")
    if ref.startswith("refs/"):
        return references.get(f"{ref}^{{}}", references.get(ref, ""))
    for name in (
        f"refs/heads/{ref}",
        f"refs/tags/{ref}^{{}}",
        f"refs/tags/{ref}",
    ):
        if name in references:
            return references[name]
    normalized = ref.lower()
    if _COMMIT_RE.fullmatch(normalized) and normalized in references.values():
        return normalized
    return ""


def _probe_git_source(
    url: str,
    ref: str,
    *,
    git_binary: str,
    timeout_seconds: float,
) -> _GitSourceProbe:
    started = time.monotonic()
    if ref == "HEAD":
        patterns = ["HEAD"]
    elif _COMMIT_RE.fullmatch(ref):
        patterns = ["HEAD", "refs/heads/*", "refs/tags/*", "refs/tags/*^{}"]
    else:
        patterns = [
            ref,
            f"refs/heads/{ref}",
            f"refs/tags/{ref}",
            f"refs/tags/{ref}^{{}}",
        ]
    try:
        completed = subprocess.run(
            [git_binary, "ls-remote", "--symref", "--", url, *patterns],
            check=True,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
        commit = _resolve_remote_commit(completed.stdout, ref)
        if not commit:
            raise ValueError(f"ref does not resolve: {ref}")
        return _GitSourceProbe(
            url=url,
            commit=commit,
            elapsed_seconds=time.monotonic() - started,
        )
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        return _GitSourceProbe(
            url=url,
            commit="",
            elapsed_seconds=time.monotonic() - started,
            error=str(exc),
        )


def select_git_source(
    urls: Iterable[str],
    ref: str,
    *,
    git_binary: str = "git",
    timeout_seconds: float = 15,
) -> GitSourceSelection:
    """Prefer configured mirrors at the authoritative primary revision."""
    candidates = tuple(dict.fromkeys(str(url).strip() for url in urls if str(url).strip()))
    if not candidates:
        raise ValueError("Plugin 仓库没有可用拉取地址")
    if len(candidates) == 1:
        return GitSourceSelection(candidates[0], "", 0.0)

    probes: dict[str, _GitSourceProbe] = {}
    with ThreadPoolExecutor(max_workers=min(len(candidates), 4)) as executor:
        futures = {
            executor.submit(
                _probe_git_source,
                url,
                ref,
                git_binary=git_binary,
                timeout_seconds=timeout_seconds,
            ): url
            for url in candidates
        }
        for future in as_completed(futures):
            probe = future.result()
            probes[probe.url] = probe

    available = [probe for probe in probes.values() if probe.commit]
    if not available:
        raise ValueError("Plugin 仓库所有拉取地址均不可用")

    primary = probes.get(candidates[0])
    if primary is not None and primary.commit:
        available = [
            probe for probe in available if probe.commit == primary.commit
        ]
    transport_priority = {
        url: index for index, url in enumerate((*candidates[1:], candidates[0]))
    }
    selected = min(available, key=lambda probe: transport_priority[probe.url])
    return GitSourceSelection(
        url=selected.url,
        commit=selected.commit,
        elapsed_seconds=selected.elapsed_seconds,
    )
