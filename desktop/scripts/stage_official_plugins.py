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

from desktop.scripts.official_plugin_lock import (
    load_official_plugin_lock,
    pin_official_sources,
    validate_locked_catalog,
)
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
        repo_root / "desktop" / "generated" / "default-config" / "plugin-sources.json"
    )
    configured_sources = tuple(
        source
        for source in load_plugin_sources(source_file)
        if source.kind == "official"
    )
    lock = load_official_plugin_lock(repo_root)
    sources = pin_official_sources(configured_sources, lock)
    catalog = fetch_plugin_catalog(sources)
    entries = validate_locked_catalog(catalog, lock)

    output_dir.parent.mkdir(parents=True, exist_ok=True)
    temporary_root = Path(
        tempfile.mkdtemp(prefix="official-plugins-", dir=output_dir.parent)
    )
    try:
        store_root = temporary_root / "store"
        store = PluginStore(
            store_root,
            official_sources=(source.url for source in sources),
            official_source_mirrors={source.url: source.mirrors for source in sources},
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
        metadata["build_lock"] = lock
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
    output = options.output or repo_root / "desktop" / "generated" / "bundled-plugins"
    stage_official_plugins(repo_root, output.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
