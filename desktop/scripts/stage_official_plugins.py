"""Build a verified snapshot containing every public official Plugin."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.extension_host.plugin_preflight import validate_plugin_checkout
from src.extension_host.source_config import (
    fetch_plugin_catalog,
    load_plugin_sources,
)
from src.plugin_system.release import prepare_release_snapshot
from src.plugin_system.store import PluginStore


def stage_official_plugins(repo_root: Path, output_dir: Path) -> dict[str, object]:
    """Resolve the current official catalog and stage exact immutable revisions."""
    source_file = (
        repo_root
        / "desktop"
        / "generated"
        / "default-config"
        / "plugin-sources.json"
    )
    sources = tuple(
        source
        for source in load_plugin_sources(source_file)
        if source.kind == "official"
    )
    if not sources:
        raise RuntimeError("桌面 Full 构建没有配置官方 Plugin 仓库")

    catalog = fetch_plugin_catalog(sources)
    errors = [
        f"{source['name']}: {source['error']}"
        for source in catalog["sources"]
        if source["error"]
    ]
    if errors:
        raise RuntimeError("官方 Plugin Catalog 获取失败: " + "; ".join(errors))
    entries = catalog["plugins"]
    if not entries:
        raise RuntimeError("官方 Plugin Catalog 不包含可安装 Plugin")

    output_dir.parent.mkdir(parents=True, exist_ok=True)
    temporary_root = Path(
        tempfile.mkdtemp(prefix="official-plugins-", dir=output_dir.parent)
    )
    try:
        store_root = temporary_root / "store"
        store = PluginStore(
            store_root,
            official_sources=(source.url for source in sources),
        )
        for entry in entries:
            store.install(
                entry["id"],
                entry["source"],
                ref=entry["ref"],
                subdirectory=entry["subdirectory"],
                preflight=lambda checkout, plugin_id=entry["id"]: (
                    validate_plugin_checkout(plugin_id, checkout)
                ),
            )
        store.apply_pending()

        snapshot = temporary_root / "snapshot"
        metadata = prepare_release_snapshot(
            store_root,
            snapshot,
            required_plugins=(entry["id"] for entry in entries),
        )
        metadata["catalog"] = {
            "sources": [
                {
                    "id": source["id"],
                    "ref": source["ref"],
                    "resolved_commit": source["resolved_commit"],
                }
                for source in catalog["sources"]
            ]
        }
        (snapshot / "release-plugins.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        if output_dir.exists():
            shutil.rmtree(output_dir)
        snapshot.replace(output_dir)
        return metadata
    finally:
        shutil.rmtree(temporary_root, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path(__file__).resolve().parents[2],
    )
    parser.add_argument("--output", type=Path)
    options = parser.parse_args()
    repo_root = options.repo_root.resolve()
    output = (
        options.output
        or repo_root / "desktop" / "generated" / "bundled-plugins"
    )
    stage_official_plugins(repo_root, output.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
