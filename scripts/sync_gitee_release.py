#!/usr/bin/env python3
"""Synchronize public Git repositories and one GitHub Release to Gitee."""

from __future__ import annotations

import argparse
import json
import logging
import os
import shlex
import subprocess
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any


LOGGER = logging.getLogger("sync-gitee-release")
GITEE_API = "https://gitee.com/api/v5"


class GiteeApiError(RuntimeError):
    def __init__(self, status: int, message: str):
        super().__init__(f"Gitee API {status}: {message}")
        self.status = status


@dataclass(frozen=True)
class RepositoryTarget:
    path: Path
    owner: str
    name: str

    @property
    def ssh_url(self) -> str:
        return f"git@gitee.com:{self.owner}/{self.name}.git"


def _run(
    command: list[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
) -> str:
    completed = subprocess.run(
        command,
        cwd=cwd,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout


def _git_environment(ssh_key: Path | None) -> dict[str, str]:
    environment = os.environ.copy()
    if ssh_key is not None:
        if not ssh_key.is_file():
            raise RuntimeError(f"Gitee SSH 密钥不存在: {ssh_key}")
        environment["GIT_SSH_COMMAND"] = (
            "ssh -o BatchMode=yes -o IdentitiesOnly=yes "
            f"-i {shlex.quote(str(ssh_key))}"
        )
    return environment


def _git_commit(repository: Path, revision: str) -> str:
    return _run(
        ["git", "rev-parse", f"{revision}^{{commit}}"],
        cwd=repository,
    ).strip()


def _assert_clean(repository: Path) -> None:
    if _run(["git", "status", "--porcelain"], cwd=repository).strip():
        raise RuntimeError(f"工作树不干净，停止同步: {repository}")


def _remote_refs(
    target: RepositoryTarget,
    tag: str | None,
    environment: dict[str, str],
) -> dict[str, str]:
    patterns = ["refs/heads/main"]
    if tag:
        patterns.extend([f"refs/tags/{tag}", f"refs/tags/{tag}^{{}}"])
    output = _run(
        ["git", "ls-remote", target.ssh_url, *patterns],
        cwd=target.path,
        env=environment,
    )
    result: dict[str, str] = {}
    for line in output.splitlines():
        commit, ref = line.split("\t", 1)
        result[ref] = commit
    return result


def sync_repository(
    target: RepositoryTarget,
    *,
    environment: dict[str, str],
    tag: str | None = None,
    dry_run: bool = False,
) -> None:
    _assert_clean(target.path)
    local_main = _git_commit(target.path, "main")
    if tag:
        local_tag = _git_commit(target.path, f"refs/tags/{tag}")
    else:
        local_tag = ""
    if not dry_run:
        _run(
            ["git", "push", target.ssh_url, "main:main", "--tags"],
            cwd=target.path,
            env=environment,
        )
        refs = _remote_refs(target, tag, environment)
        if refs.get("refs/heads/main") != local_main:
            raise RuntimeError(f"Gitee main 校验失败: {target.name}")
        if tag and refs.get(f"refs/tags/{tag}^{{}}", refs.get(f"refs/tags/{tag}")) != local_tag:
            raise RuntimeError(f"Gitee Tag 校验失败: {target.name}/{tag}")
    LOGGER.info("代码同步已校验: %s@%s", target.name, local_main[:12])


def _request_json(
    method: str,
    path: str,
    token: str,
    payload: dict[str, Any] | None = None,
) -> Any:
    data = None
    headers = {
        "Accept": "application/json",
        "Authorization": f"token {token}",
        "User-Agent": "DeterminFlow-Gitee-Sync",
    }
    if payload is not None:
        data = urllib.parse.urlencode(payload).encode("utf-8")
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    request = urllib.request.Request(
        f"{GITEE_API}{path}",
        data=data,
        headers=headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        message = exc.read().decode("utf-8", errors="replace")[:500]
        raise GiteeApiError(exc.code, message) from exc
    if not raw:
        return {}
    return json.loads(raw)


def _github_release(repository: str, tag: str) -> dict[str, Any]:
    payload = json.loads(
        _run([
            "gh",
            "release",
            "view",
            tag,
            "--repo",
            repository,
            "--json",
            "assets,body,isDraft,isPrerelease,name,tagName,targetCommitish",
        ])
    )
    if payload["isDraft"] or payload["isPrerelease"]:
        raise RuntimeError(f"GitHub Release 不是正式版本: {tag}")
    return payload


def _ensure_gitee_release(
    *,
    owner: str,
    repo: str,
    tag: str,
    release: dict[str, Any],
    token: str,
) -> dict[str, Any]:
    tag_path = urllib.parse.quote(tag, safe="")
    try:
        current = _request_json(
            "GET",
            f"/repos/{owner}/{repo}/releases/tags/{tag_path}",
            token,
        )
    except GiteeApiError as exc:
        if exc.status != 404:
            raise
        current = None
    payload = {
        "tag_name": tag,
        "name": release["name"] or f"DeterminFlow {tag}",
        "body": release["body"],
        "target_commitish": release["targetCommitish"] or "main",
        "prerelease": "false",
    }
    if current is None:
        return _request_json(
            "POST",
            f"/repos/{owner}/{repo}/releases",
            token,
            payload,
        )
    return _request_json(
        "PATCH",
        f"/repos/{owner}/{repo}/releases/{current['id']}",
        token,
        payload,
    )


def _rewrite_gitee_manifest(
    manifest_path: Path,
    *,
    owner: str,
    repo: str,
    tag: str,
) -> None:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    platforms = manifest.get("platforms")
    if not isinstance(platforms, dict) or not platforms:
        raise RuntimeError("GitHub latest.json 缺少 platforms")
    for platform in platforms.values():
        if not isinstance(platform, dict) or not isinstance(platform.get("url"), str):
            raise RuntimeError("GitHub latest.json 平台下载地址无效")
        filename = Path(
            urllib.parse.unquote(urllib.parse.urlsplit(platform["url"]).path)
        ).name
        if not filename:
            raise RuntimeError("GitHub latest.json 下载文件名无效")
        encoded = urllib.parse.quote(filename)
        platform["url"] = (
            f"https://gitee.com/{owner}/{repo}/releases/download/{tag}/{encoded}"
        )
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _upload_attachment(
    *,
    owner: str,
    repo: str,
    release_id: int,
    path: Path,
    token: str,
) -> dict[str, Any]:
    config = f'header = "Authorization: token {token}"\n'
    completed = subprocess.run(
        [
            "curl",
            "--config",
            "-",
            "--fail-with-body",
            "--silent",
            "--show-error",
            "--request",
            "POST",
            "--form",
            f"file=@{path}",
            f"{GITEE_API}/repos/{owner}/{repo}/releases/{release_id}/attach_files",
        ],
        input=config,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


def _delete_attachment(
    *,
    owner: str,
    repo: str,
    release_id: int,
    attachment_id: int,
    token: str,
) -> None:
    _request_json(
        "DELETE",
        (
            f"/repos/{owner}/{repo}/releases/{release_id}"
            f"/attach_files/{attachment_id}"
        ),
        token,
    )


def _same_attachment(attachment: dict[str, Any], path: Path) -> bool:
    size = attachment.get("size")
    try:
        return int(size) == path.stat().st_size
    except (TypeError, ValueError):
        return False


def sync_release_assets(
    *,
    github_repo: str,
    owner: str,
    repo: str,
    tag: str,
    token: str,
    replace_assets: bool,
) -> None:
    release = _github_release(github_repo, tag)
    with tempfile.TemporaryDirectory(prefix="determinflow-gitee-release-") as raw:
        assets_dir = Path(raw)
        _run([
            "gh",
            "release",
            "download",
            tag,
            "--repo",
            github_repo,
            "--dir",
            str(assets_dir),
        ])
        manifest = assets_dir / "latest.json"
        if not manifest.is_file():
            raise RuntimeError("GitHub Release 缺少 latest.json")
        _rewrite_gitee_manifest(
            manifest,
            owner=owner,
            repo=repo,
            tag=tag,
        )
        gitee_release = _ensure_gitee_release(
            owner=owner,
            repo=repo,
            tag=tag,
            release=release,
            token=token,
        )
        release_id = int(gitee_release["id"])
        attachments = _request_json(
            "GET",
            f"/repos/{owner}/{repo}/releases/{release_id}/attach_files",
            token,
        )
        existing = {
            item["name"]: item
            for item in attachments
            if isinstance(item, dict) and isinstance(item.get("name"), str)
        }
        for path in sorted(assets_dir.iterdir()):
            current = existing.get(path.name)
            if current is not None and _same_attachment(current, path):
                continue
            if current is not None:
                if not replace_assets:
                    raise RuntimeError(
                        f"Gitee 已存在不同附件，使用 --replace-assets 重试: {path.name}"
                    )
                _delete_attachment(
                    owner=owner,
                    repo=repo,
                    release_id=release_id,
                    attachment_id=int(current["id"]),
                    token=token,
                )
            _upload_attachment(
                owner=owner,
                repo=repo,
                release_id=release_id,
                path=path,
                token=token,
            )

        verified = _request_json(
            "GET",
            f"/repos/{owner}/{repo}/releases/tags/{urllib.parse.quote(tag, safe='')}",
            token,
        )
        names = {
            asset["name"]
            for asset in verified.get("assets", [])
            if isinstance(asset, dict) and isinstance(asset.get("name"), str)
        }
        expected = {path.name for path in assets_dir.iterdir()}
        if not expected.issubset(names):
            raise RuntimeError(
                f"Gitee Release 附件不完整: {sorted(expected - names)}"
            )
    LOGGER.info("发行产物同步已校验: %s/%s@%s", owner, repo, tag)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tag", required=True)
    parser.add_argument("--core-repo", type=Path, default=Path.cwd())
    parser.add_argument("--plugins-repo", type=Path)
    parser.add_argument("--gitee-owner", default="alikon")
    parser.add_argument("--gitee-core-repo", default="DeterminFlow")
    parser.add_argument("--gitee-plugins-repo", default="DeterminFlow-Plugins")
    parser.add_argument("--ssh-key", type=Path)
    parser.add_argument("--skip-assets", action="store_true")
    parser.add_argument("--replace-assets", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> int:
    options = parse_args()
    core = options.core_repo.resolve()
    plugins = (options.plugins_repo or core.parent / "DeterminFlow-Plugins").resolve()
    ssh_key = options.ssh_key
    if ssh_key is None and os.getenv("GITEE_SSH_KEY"):
        ssh_key = Path(os.environ["GITEE_SSH_KEY"]).expanduser().resolve()
    token = os.getenv("GITEE_TOKEN", "").strip()
    if not options.skip_assets and not options.dry_run and not token:
        raise RuntimeError("同步 Gitee Release 附件需要设置 GITEE_TOKEN")
    environment = _git_environment(ssh_key)
    core_target = RepositoryTarget(core, options.gitee_owner, options.gitee_core_repo)
    plugin_target = RepositoryTarget(
        plugins,
        options.gitee_owner,
        options.gitee_plugins_repo,
    )
    sync_repository(
        core_target,
        environment=environment,
        tag=options.tag,
        dry_run=options.dry_run,
    )
    sync_repository(
        plugin_target,
        environment=environment,
        dry_run=options.dry_run,
    )
    if not options.skip_assets and not options.dry_run:
        sync_release_assets(
            github_repo="alikon-art/DeterminFlow",
            owner=options.gitee_owner,
            repo=options.gitee_core_repo,
            tag=options.tag,
            token=token,
            replace_assets=options.replace_assets,
        )
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    raise SystemExit(main())
