from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from src.plugin_system import store as store_module
from src.plugin_system import (
    InvalidPluginPackageError,
    PluginStore,
    PluginStoreError,
    SourceTrustError,
)
from src.plugin_system.source_selection import GitSourceSelection


def _git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _commit(repo: Path, message: str) -> str:
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


def _create_repo(
    tmp_path: Path,
    *,
    subdirectory: str = "",
    plugin_id: str = "demo-plugin",
    resource_prefix: str = "",
) -> tuple[Path, Path, str]:
    repo = tmp_path / f"source-{plugin_id}"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.name", "Plugin Test")
    _git(repo, "config", "user.email", "plugin-test@example.invalid")
    package = repo / subdirectory if subdirectory else repo
    package.mkdir(parents=True, exist_ok=True)
    (package / "extension.toml").write_text(
        "\n".join(
            [
                "[extension]",
                f'id = "{plugin_id}"',
                'name = "Demo Plugin"',
                'version = "1.0.0"',
                'api_version = "1"',
                *(
                    [
                        "",
                        "[resource_namespace]",
                        f'prefix = "{resource_prefix}"',
                    ]
                    if resource_prefix
                    else []
                ),
            ]
        ),
        encoding="utf-8",
    )
    (package / "payload.txt").write_text("one\n", encoding="utf-8")
    return repo, package, _commit(repo, "initial")


def test_install_local_subdirectory_locks_exact_commit_and_manifest(tmp_path: Path) -> None:
    repo, _, commit = _create_repo(tmp_path, subdirectory="packages/demo")
    store = PluginStore(
        tmp_path / "store",
        official_sources=[str(repo)],
    )

    record = store.install(
        "demo-plugin",
        str(repo),
        ref="main",
        subdirectory="packages/demo",
    )

    assert record.active_revision.commit == commit
    assert len(record.active_revision.content_sha256) == 64
    assert record.trust == "official"
    assert record.pending_action == "install"
    manifest_paths = store.installed_manifest_paths()
    assert len(manifest_paths) == 1
    assert manifest_paths[0].is_file()
    assert manifest_paths[0].parent != repo / "packages/demo"
    assert manifest_paths[0].parent.is_relative_to((tmp_path / "store").resolve())
    assert json.loads(store.lock_path.read_text(encoding="utf-8"))["schema_version"] == 1
    assert store.snapshot() == store.snapshot()


def test_official_mirror_is_transport_only_and_lock_keeps_primary_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mirror, _, commit = _create_repo(tmp_path)
    primary = tmp_path / "primary-unavailable"
    expected_primary = primary.resolve().as_uri()
    expected_mirror = mirror.resolve().as_uri()

    def select(urls, ref, **kwargs):
        assert tuple(urls) == (expected_primary, expected_mirror)
        assert ref == "main"
        return GitSourceSelection(expected_mirror, commit, 0.1)

    monkeypatch.setattr(store_module, "select_git_source", select)
    store = PluginStore(
        tmp_path / "store",
        official_sources=[str(primary)],
        official_source_mirrors={str(primary): [str(mirror)]},
    )

    record = store.install("demo-plugin", str(primary), ref="main")

    assert record.source == expected_primary
    assert record.trust == "official"
    assert record.active_revision.commit == commit


def test_install_rejects_mirror_ref_drift_after_probe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mirror, _, _ = _create_repo(tmp_path)
    primary = tmp_path / "primary-unavailable"
    expected_mirror = mirror.resolve().as_uri()
    monkeypatch.setattr(
        store_module,
        "select_git_source",
        lambda *args, **kwargs: GitSourceSelection(
            expected_mirror,
            "f" * 40,
            0.1,
        ),
    )
    store = PluginStore(
        tmp_path / "store",
        official_sources=[str(primary)],
        official_source_mirrors={str(primary): [str(mirror)]},
    )

    with pytest.raises(PluginStoreError, match="拉取期间发生版本漂移"):
        store.install("demo-plugin", str(primary), ref="main")


def test_install_uses_manifest_resource_prefix_unless_explicitly_overridden(
    tmp_path: Path,
) -> None:
    repo, _, _ = _create_repo(tmp_path, resource_prefix="demo")
    store = PluginStore(tmp_path / "store", official_sources=[str(repo)])

    defaulted = store.install("demo-plugin", str(repo))

    assert defaulted.resource_prefix == "demo"
    assert defaulted.resource_prefix_override is None
    assert store.snapshot()["plugins"]["demo-plugin"]["resource_prefix"] == "demo"
    assert (
        store.snapshot()["plugins"]["demo-plugin"]["resource_prefix_override"]
        is None
    )

    store.mark_uninstall("demo-plugin")
    store.apply_pending()
    overridden = store.install(
        "demo-plugin",
        str(repo),
        resource_prefix="custom-demo",
    )

    assert overridden.resource_prefix == "custom-demo"
    assert overridden.resource_prefix_override == "custom-demo"


def test_resource_prefix_override_is_immutable_across_update_and_rollback(
    tmp_path: Path,
) -> None:
    repo, package, _ = _create_repo(tmp_path, resource_prefix="developer-default")
    store = PluginStore(tmp_path / "store", official_sources=[str(repo)])
    installed = store.install(
        "demo-plugin",
        str(repo),
        resource_prefix="installed-prefix",
    )
    manifest = package / "extension.toml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace(
            'prefix = "developer-default"',
            'prefix = "changed-by-developer"',
        ),
        encoding="utf-8",
    )
    (package / "payload.txt").write_text("two\n", encoding="utf-8")
    _commit(repo, "change default prefix")

    updated = store.update("demo-plugin", ref="main")
    rolled_back = store.rollback("demo-plugin")

    assert installed.resource_prefix == "installed-prefix"
    assert updated.resource_prefix == "installed-prefix"
    assert rolled_back.resource_prefix == "installed-prefix"
    assert installed.resource_prefix_override == "installed-prefix"
    assert updated.resource_prefix_override == "installed-prefix"
    assert rolled_back.resource_prefix_override == "installed-prefix"


def test_developer_resource_prefix_follows_update_and_rollback(
    tmp_path: Path,
) -> None:
    repo, package, _ = _create_repo(tmp_path)
    store = PluginStore(tmp_path / "store", official_sources=[str(repo)])
    installed = store.install("demo-plugin", str(repo))
    manifest = package / "extension.toml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8")
        + '\n[resource_namespace]\nprefix = "demo"\n',
        encoding="utf-8",
    )
    (package / "payload.txt").write_text("two\n", encoding="utf-8")
    _commit(repo, "add default prefix")

    updated = store.update("demo-plugin", ref="main")
    rolled_back = store.rollback("demo-plugin")

    assert installed.resource_prefix == ""
    assert updated.resource_prefix == "demo"
    assert rolled_back.resource_prefix == ""
    assert installed.resource_prefix_override is None
    assert updated.resource_prefix_override is None
    assert rolled_back.resource_prefix_override is None


def test_legacy_lock_without_resource_prefix_defaults_to_empty(
    tmp_path: Path,
) -> None:
    repo, _, _ = _create_repo(tmp_path, resource_prefix="new-default")
    store = PluginStore(tmp_path / "store", official_sources=[str(repo)])
    store.install("demo-plugin", str(repo))
    document = json.loads(store.lock_path.read_text(encoding="utf-8"))
    document["plugins"]["demo-plugin"].pop("resource_prefix")
    document["plugins"]["demo-plugin"].pop("resource_prefix_override")
    store.lock_path.write_text(json.dumps(document), encoding="utf-8")

    record = store.get("demo-plugin")
    assert record is not None
    assert record.resource_prefix == ""
    assert record.resource_prefix_override is None


def test_lock_without_override_provenance_recovers_developer_default(
    tmp_path: Path,
) -> None:
    repo, package, _ = _create_repo(tmp_path)
    store = PluginStore(tmp_path / "store", official_sources=[str(repo)])
    store.install("demo-plugin", str(repo))
    manifest = package / "extension.toml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8")
        + '\n[resource_namespace]\nprefix = "demo"\n',
        encoding="utf-8",
    )
    _commit(repo, "add namespace")
    store.update("demo-plugin", ref="main")

    document = json.loads(store.lock_path.read_text(encoding="utf-8"))
    payload = document["plugins"]["demo-plugin"]
    payload.pop("resource_prefix_override")
    payload["resource_prefix"] = ""
    store.lock_path.write_text(json.dumps(document), encoding="utf-8")

    recovered = store.get("demo-plugin")

    assert recovered is not None
    assert recovered.resource_prefix == ""
    assert recovered.resource_prefix_override is None
    updated = store.update("demo-plugin", ref="main")
    assert updated.resource_prefix == "demo"
    assert updated.resource_prefix_override is None


def test_lock_rejects_empty_explicit_resource_prefix_override(
    tmp_path: Path,
) -> None:
    repo, _, _ = _create_repo(tmp_path, resource_prefix="demo")
    store = PluginStore(tmp_path / "store", official_sources=[str(repo)])
    store.install("demo-plugin", str(repo))
    document = json.loads(store.lock_path.read_text(encoding="utf-8"))
    document["plugins"]["demo-plugin"]["resource_prefix_override"] = ""
    store.lock_path.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(PluginStoreError, match="invalid resource prefix"):
        store.get("demo-plugin")


def test_install_rejects_invalid_or_conflicting_resource_prefix(
    tmp_path: Path,
) -> None:
    first_repo, _, _ = _create_repo(
        tmp_path,
        plugin_id="first-plugin",
        resource_prefix="shared",
    )
    second_repo, _, _ = _create_repo(
        tmp_path,
        plugin_id="second-plugin",
        resource_prefix="shared",
    )
    store = PluginStore(
        tmp_path / "store",
        official_sources=[str(first_repo), str(second_repo)],
    )
    store.install("first-plugin", str(first_repo))

    with pytest.raises(PluginStoreError, match="resource prefix.*first-plugin"):
        store.install("second-plugin", str(second_repo))

    with pytest.raises(PluginStoreError, match="invalid resource prefix"):
        store.install(
            "second-plugin",
            str(second_repo),
            resource_prefix="Bad_Prefix",
        )

    installed = store.install(
        "second-plugin",
        str(second_repo),
        resource_prefix="second",
    )
    assert installed.resource_prefix == "second"


def test_file_url_is_supported_and_official_matching_is_canonical_exact(
    tmp_path: Path,
) -> None:
    repo, _, _ = _create_repo(tmp_path)
    store = PluginStore(
        tmp_path / "store",
        official_sources=[repo.as_uri() + "/"],
    )

    record = store.install("demo-plugin", repo.as_uri(), ref="HEAD")

    assert record.trust == "official"
    assert record.source == repo.resolve().as_uri()


@pytest.mark.parametrize(
    "source",
    [
        "https://token@example.com/repository.git",
        "https://example.com/repository.git?access_token=secret",
        "https://example.com/repository.git#main",
        "file://remote-host/tmp/repository",
        "ftp://example.com/repository.git",
    ],
)
def test_source_url_rejects_credential_and_ambiguous_forms(
    tmp_path: Path,
    source: str,
) -> None:
    store = PluginStore(tmp_path / "store")

    with pytest.raises(PluginStoreError):
        store.install(
            "demo-plugin",
            source,
            acknowledge_risk=True,
        )


def test_third_party_install_and_update_require_explicit_acknowledgement(
    tmp_path: Path,
) -> None:
    repo, package, first_commit = _create_repo(tmp_path)
    store = PluginStore(tmp_path / "store")

    with pytest.raises(SourceTrustError, match="acknowledge_risk"):
        store.install("demo-plugin", str(repo))

    first = store.install("demo-plugin", str(repo), acknowledge_risk=True)
    assert first.active_revision.commit == first_commit
    assert first.trust == "third_party"

    (package / "payload.txt").write_text("two\n", encoding="utf-8")
    second_commit = _commit(repo, "update")
    with pytest.raises(SourceTrustError, match="acknowledge_risk"):
        store.update("demo-plugin", ref="main")

    second = store.update(
        "demo-plugin",
        ref="main",
        acknowledge_risk=True,
    )
    assert second.active_revision.commit == second_commit
    assert [item.commit for item in second.history] == [first_commit]
    assert second.pending_action == "update"
    assert Path(first.active_revision.checkout_path).exists()


def test_rollback_reuses_preserved_checkout_and_pending_remove_keeps_files(
    tmp_path: Path,
) -> None:
    repo, package, first_commit = _create_repo(tmp_path)
    store = PluginStore(
        tmp_path / "store",
        official_sources=[str(repo)],
    )
    first = store.install("demo-plugin", str(repo))
    (package / "payload.txt").write_text("two\n", encoding="utf-8")
    second_commit = _commit(repo, "update")
    second = store.update("demo-plugin", ref="main")

    rolled_back = store.rollback("demo-plugin")

    assert rolled_back.active_revision.commit == first_commit
    assert rolled_back.pending_action == "rollback"
    assert second_commit in [item.commit for item in rolled_back.history]
    assert Path(first.active_revision.checkout_path).exists()
    assert Path(second.active_revision.checkout_path).exists()

    pending = store.mark_uninstall("demo-plugin")
    assert pending.pending_action == "remove"
    assert store.installed_manifest_paths() == []
    assert Path(pending.active_revision.checkout_path).exists()


def test_installed_manifest_paths_fail_closed_after_checkout_tampering(
    tmp_path: Path,
) -> None:
    repo, _, _ = _create_repo(tmp_path)
    store = PluginStore(tmp_path / "store", official_sources=[str(repo)])
    record = store.install("demo-plugin", str(repo))
    (Path(record.active_revision.checkout_path) / "payload.txt").write_text(
        "tampered\n",
        encoding="utf-8",
    )

    with pytest.raises(PluginStoreError, match="hash mismatch"):
        store.installed_manifest_paths()


def test_runtime_bytecode_cache_does_not_invalidate_locked_checkout(
    tmp_path: Path,
) -> None:
    repo, _, _ = _create_repo(tmp_path)
    store = PluginStore(tmp_path / "store", official_sources=[str(repo)])
    record = store.install("demo-plugin", str(repo))
    cache = Path(record.active_revision.checkout_path) / "__pycache__"
    cache.mkdir()
    (cache / "runtime.cpython-313.pyc").write_bytes(b"runtime cache")

    assert store.verify("demo-plugin") == record


def test_package_rejects_committed_python_bytecode_cache(tmp_path: Path) -> None:
    repo, package, _ = _create_repo(tmp_path)
    cache = package / "__pycache__"
    cache.mkdir()
    (cache / "plugin.cpython-313.pyc").write_bytes(b"tracked cache")
    _commit(repo, "track bytecode")
    store = PluginStore(tmp_path / "store", official_sources=[str(repo)])

    with pytest.raises(
        InvalidPluginPackageError,
        match="cannot contain Python bytecode cache",
    ):
        store.install("demo-plugin", str(repo))


def test_apply_pending_clears_actions_and_forgets_remove_without_deleting_checkout(
    tmp_path: Path,
) -> None:
    repo, package, _ = _create_repo(tmp_path)
    store = PluginStore(tmp_path / "store", official_sources=[str(repo)])
    installed = store.install("demo-plugin", str(repo))

    applied = store.apply_pending()
    assert applied["demo-plugin"].pending_action is None

    (package / "payload.txt").write_text("two\n", encoding="utf-8")
    _commit(repo, "update")
    updated = store.update("demo-plugin", ref="main")
    preserved_paths = {
        Path(updated.active_revision.checkout_path),
        *(Path(item.checkout_path) for item in updated.history),
    }
    store.mark_uninstall("demo-plugin")

    assert store.apply_pending() == {}
    assert store.read_lock() == {}
    assert all(path.exists() for path in preserved_paths)
    assert Path(installed.active_revision.checkout_path).exists()

    reinstalled = store.install("demo-plugin", str(repo))
    assert reinstalled.pending_action == "install"


def test_update_cannot_silently_change_source_repository(tmp_path: Path) -> None:
    repo, _, _ = _create_repo(tmp_path)
    other_repo = tmp_path / "other-source"
    subprocess.run(
        ["git", "clone", "--quiet", str(repo), str(other_repo)],
        check=True,
    )
    store = PluginStore(tmp_path / "store", official_sources=[str(repo), str(other_repo)])
    store.install("demo-plugin", str(repo))

    with pytest.raises(PluginStoreError, match="source cannot change"):
        store.update("demo-plugin", source=str(other_repo))


@pytest.mark.parametrize("subdirectory", ["/absolute", "../escape", "a/../../escape"])
def test_subdirectory_must_be_relative_and_cannot_escape(
    tmp_path: Path,
    subdirectory: str,
) -> None:
    repo, _, _ = _create_repo(tmp_path)
    store = PluginStore(tmp_path / "store", official_sources=[str(repo)])

    with pytest.raises(InvalidPluginPackageError, match="subdirectory"):
        store.install("demo-plugin", str(repo), subdirectory=subdirectory)


def test_manifest_is_required_and_must_match_requested_plugin_id(tmp_path: Path) -> None:
    repo, package, _ = _create_repo(tmp_path)
    store = PluginStore(tmp_path / "store", official_sources=[str(repo)])
    (package / "extension.toml").unlink()
    _commit(repo, "remove manifest")

    with pytest.raises(InvalidPluginPackageError, match="extension.toml"):
        store.install("demo-plugin", str(repo))

    (package / "extension.toml").write_text(
        '[extension]\nid = "different-plugin"\n',
        encoding="utf-8",
    )
    _commit(repo, "wrong id")
    with pytest.raises(InvalidPluginPackageError, match="different-plugin"):
        store.install("demo-plugin", str(repo))


@pytest.mark.parametrize("plugin_id", ["demo_plugin", "demo.plugin", "Demo"])
def test_plugin_id_must_be_lowercase_kebab_case(
    tmp_path: Path,
    plugin_id: str,
) -> None:
    repo, _, _ = _create_repo(tmp_path)
    store = PluginStore(tmp_path / "store", official_sources=[str(repo)])

    with pytest.raises(PluginStoreError, match="invalid plugin id"):
        store.install(plugin_id, str(repo))


def test_symlink_cannot_escape_checked_out_package(tmp_path: Path) -> None:
    repo, package, _ = _create_repo(tmp_path, subdirectory="package")
    (repo / "outside.txt").write_text("outside\n", encoding="utf-8")
    (package / "escape.txt").symlink_to("../outside.txt")
    _commit(repo, "escaping symlink")
    store = PluginStore(tmp_path / "store", official_sources=[str(repo)])

    with pytest.raises(InvalidPluginPackageError, match="symlink"):
        store.install("demo-plugin", str(repo), subdirectory="package")


def test_failed_lock_replace_preserves_previous_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, package, _ = _create_repo(tmp_path)
    store = PluginStore(tmp_path / "store", official_sources=[str(repo)])
    store.install("demo-plugin", str(repo))
    before = store.lock_path.read_bytes()
    (package / "payload.txt").write_text("two\n", encoding="utf-8")
    _commit(repo, "update")

    original_replace = __import__("os").replace

    def fail_replace(source: Path, target: Path) -> None:
        if Path(target) == store.lock_path:
            raise OSError("replace failed")
        original_replace(source, target)

    monkeypatch.setattr("src.plugin_system.store.os.replace", fail_replace)
    with pytest.raises(PluginStoreError, match="lock"):
        store.update("demo-plugin", ref="main")

    assert store.lock_path.read_bytes() == before


def test_git_commands_never_enable_shell(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, _, _ = _create_repo(tmp_path)
    store = PluginStore(tmp_path / "store", official_sources=[str(repo)])
    original_run = subprocess.run
    observed: list[dict] = []

    def tracked_run(*args, **kwargs):
        observed.append(dict(kwargs))
        return original_run(*args, **kwargs)

    monkeypatch.setattr("src.plugin_system.store.subprocess.run", tracked_run)
    store.install("demo-plugin", str(repo))

    assert observed
    assert all(call.get("shell") is not True for call in observed)
