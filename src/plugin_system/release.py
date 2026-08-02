"""Build-time snapshots of immutable plugin checkouts for production releases."""

from __future__ import annotations

import argparse
import json
import shutil
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from .store import PluginStore, PluginStoreError

RELEASE_METADATA = "release-plugins.json"


class PluginReleaseError(RuntimeError):
    """Raised when runtime plugin state cannot form an immutable release."""


def prepare_release_snapshot(
    plugins_root: Path,
    destination: Path,
    *,
    required_plugins: Iterable[str],
) -> dict[str, Any]:
    """Copy verified active revisions and a history-free lock into a release tree."""
    plugins_root = Path(plugins_root).expanduser().resolve()
    destination = Path(destination).expanduser().resolve()
    if destination.exists():
        raise PluginReleaseError(
            f"plugin release destination already exists: {destination}"
        )

    store = PluginStore(plugins_root)
    records = store.read_lock()
    selected_plugins = tuple(dict.fromkeys(required_plugins))
    if not selected_plugins:
        raise PluginReleaseError("at least one release plugin must be selected")
    missing = sorted(set(selected_plugins) - set(records))
    if missing:
        raise PluginReleaseError(
            "required production plugins are not installed: " + ", ".join(missing)
        )
    pending = sorted(
        plugin_id
        for plugin_id in selected_plugins
        for record in (records[plugin_id],)
        if record.pending_action is not None
    )
    if pending:
        raise PluginReleaseError(
            "plugin changes require a Core restart before release: "
            + ", ".join(pending)
        )

    full_lock = store.snapshot()
    lock_document = {
        "schema_version": full_lock["schema_version"],
        "plugins": {
            plugin_id: full_lock["plugins"][plugin_id]
            for plugin_id in sorted(selected_plugins)
        },
    }
    metadata_plugins: dict[str, dict[str, str]] = {}
    destination.mkdir(parents=True)
    (destination / "checkouts").mkdir()
    for plugin_id in sorted(selected_plugins):
        try:
            verified = store.verify(plugin_id)
        except PluginStoreError as exc:
            raise PluginReleaseError(
                f"plugin verification failed: {plugin_id}: {exc}"
            ) from exc
        source = Path(verified.active_revision.checkout_path).resolve()
        symlink = next((path for path in source.rglob("*") if path.is_symlink()), None)
        if symlink is not None:
            raise PluginReleaseError(
                f"production plugin snapshot forbids symlinks: {plugin_id}"
            )

        relative_checkout = Path(
            lock_document["plugins"][plugin_id]["active_revision"]["checkout"]
        )
        expected_checkout = (
            Path("checkouts")
            / plugin_id
            / (
                f"{verified.active_revision.commit}-"
                f"{verified.active_revision.content_sha256[:16]}"
            )
        )
        if relative_checkout != expected_checkout:
            raise PluginReleaseError(
                f"plugin checkout path is not canonical: {plugin_id}"
            )
        target = destination / relative_checkout
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(
            source,
            target,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"),
        )
        lock_document["plugins"][plugin_id]["history"] = []
        metadata_plugins[plugin_id] = {
            "commit": verified.active_revision.commit,
            "content_sha256": verified.active_revision.content_sha256,
            "checkout": relative_checkout.as_posix(),
            "source": verified.source,
            "trust": verified.trust,
        }

    lock_path = destination / "plugins.lock.json"
    lock_path.write_text(
        json.dumps(lock_document, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    copied_store = PluginStore(destination)
    for plugin_id in selected_plugins:
        copied_store.verify(plugin_id)

    metadata = {"schema_version": 1, "plugins": metadata_plugins}
    (destination / RELEASE_METADATA).write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return metadata


def load_release_plugin(
    snapshot_root: Path,
    plugin_id: str,
) -> dict[str, str]:
    """Read one validated identity from a prepared release snapshot."""
    metadata_path = Path(snapshot_root).resolve() / RELEASE_METADATA
    try:
        document = json.loads(metadata_path.read_text(encoding="utf-8"))
        plugin = document["plugins"][plugin_id]
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise PluginReleaseError(
            f"invalid release plugin metadata for {plugin_id}"
        ) from exc
    required = {"commit", "content_sha256", "checkout", "source", "trust"}
    if not isinstance(plugin, dict) or set(plugin) != required:
        raise PluginReleaseError(f"invalid release plugin identity: {plugin_id}")
    return {key: str(value) for key, value in plugin.items()}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    snapshot = subparsers.add_parser("snapshot")
    snapshot.add_argument("plugins_root", type=Path)
    snapshot.add_argument("destination", type=Path)
    snapshot.add_argument(
        "--required",
        action="append",
        dest="required_plugins",
        required=True,
    )
    identity = subparsers.add_parser("identity")
    identity.add_argument("snapshot_root", type=Path)
    identity.add_argument("plugin_id")
    identity.add_argument(
        "--field",
        choices=("commit", "content_sha256", "checkout", "source", "trust"),
    )
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        if args.command == "snapshot":
            metadata = prepare_release_snapshot(
                args.plugins_root,
                args.destination,
                required_plugins=args.required_plugins,
            )
            print(json.dumps(metadata, sort_keys=True, separators=(",", ":")))
        else:
            identity = load_release_plugin(args.snapshot_root, args.plugin_id)
            if args.field:
                print(identity[args.field])
            else:
                print(json.dumps(identity, sort_keys=True, separators=(",", ":")))
    except PluginReleaseError as exc:
        print(f"plugin release rejected: {exc}", file=__import__("sys").stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
