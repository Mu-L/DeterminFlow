"""Refresh or verify the tracked official Plugin input for a Full release."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from desktop.scripts.official_plugin_lock import (
    load_official_plugin_lock,
    resolve_latest_official_plugin_lock,
    write_official_plugin_lock,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--repo-root", type=Path, default=Path(__file__).resolve().parents[2]
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Fail unless the tracked lock matches the current official main.",
    )
    options = parser.parse_args()
    repo_root = options.repo_root.resolve()
    latest = resolve_latest_official_plugin_lock(
        repo_root, repo_root / "config" / "plugin-sources.json"
    )
    if options.check:
        locked = load_official_plugin_lock(repo_root)
        if locked != latest:
            raise RuntimeError(
                "Full 官方 Plugin 构建锁不是当前最新公开版本: "
                f"locked={locked['source']['commit']}, "
                f"latest={latest['source']['commit']}"
            )
        return 0
    path = write_official_plugin_lock(repo_root, latest)
    print(
        json.dumps(
            {
                "lock": str(path.relative_to(repo_root)),
                "commit": latest["source"]["commit"],
                "plugins": {item["id"]: item["version"] for item in latest["plugins"]},
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
