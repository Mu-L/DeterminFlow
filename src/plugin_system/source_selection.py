"""Select a responsive Git transport without weakening revision integrity."""

from __future__ import annotations

import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Iterable


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
        if len(commit) == 40:
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
    if len(normalized) == 40 and normalized in references.values():
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
    patterns = ["HEAD"] if ref == "HEAD" else [
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
    """Choose the fastest source at the authoritative primary revision."""
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
    selected = min(available, key=lambda probe: probe.elapsed_seconds)
    return GitSourceSelection(
        url=selected.url,
        commit=selected.commit,
        elapsed_seconds=selected.elapsed_seconds,
    )
