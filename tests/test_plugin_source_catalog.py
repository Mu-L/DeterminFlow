from __future__ import annotations

import json
import subprocess
import threading
from pathlib import Path

from fastapi.testclient import TestClient

from src.extension_host.manager import ExtensionManager
from src.extension_host.source_config import (
    PluginCatalogService,
    fetch_plugin_catalog,
    load_plugin_sources,
)
from src.web_server import create_app


def _git(repository: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    )


def _repository(tmp_path: Path, *, subdirectory: str = "plugins/demo-plugin") -> Path:
    repository = tmp_path / "catalog-repository"
    repository.mkdir()
    _git(repository, "init", "-b", "main")
    _git(repository, "config", "user.name", "Plugin Test")
    _git(repository, "config", "user.email", "plugin-test@example.invalid")
    (repository / "plugin-repository.toml").write_text(
        "\n".join([
            'schema_version = "1"',
            'name = "Test Catalog"',
            "",
            "[[plugins]]",
            'id = "demo-plugin"',
            f'subdirectory = "{subdirectory}"',
        ]),
        encoding="utf-8",
    )
    _git(repository, "add", ".")
    _git(repository, "commit", "-m", "catalog")
    return repository


def _source_file(tmp_path: Path, repository: Path) -> Path:
    config_file = tmp_path / "config" / "plugin-sources.json"
    config_file.parent.mkdir(parents=True)
    config_file.write_text(
        json.dumps({
            "schema_version": 1,
            "official_sources": [{
                "name": "Local Official",
                "url": str(repository),
                "ref": "main",
            }],
        }),
        encoding="utf-8",
    )
    return config_file


def test_fetches_official_repository_index_and_exposes_catalog_route(
    tmp_path: Path,
):
    repository = _repository(tmp_path)
    source_file = _source_file(tmp_path, repository)

    sources = load_plugin_sources(source_file)
    catalog = fetch_plugin_catalog(sources)

    assert catalog["sources"][0]["error"] == ""
    assert len(catalog["sources"][0]["resolved_commit"]) == 40
    assert catalog["plugins"] == [{
        "id": "demo-plugin",
        "source_name": "Local Official",
        "source": repository.resolve().as_uri(),
        "ref": "main",
        "subdirectory": "plugins/demo-plugin",
    }]

    manager = ExtensionManager(
        tmp_path,
        config_file=tmp_path / "config" / "extensions.json",
        enabled=[],
        discover_entry_points=False,
    )
    response = TestClient(create_app(manager)).get("/api/plugins/catalog")
    assert response.status_code == 200
    assert response.json()["plugins"] == catalog["plugins"]


def test_invalid_repository_index_isolated_to_its_source(tmp_path: Path):
    repository = _repository(tmp_path, subdirectory="../escape")
    source_file = _source_file(tmp_path, repository)

    catalog = fetch_plugin_catalog(load_plugin_sources(source_file))

    assert catalog["plugins"] == []
    assert "子路径无效" in catalog["sources"][0]["error"]


def test_catalog_service_caches_and_returns_defensive_copies(
    tmp_path: Path,
    monkeypatch,
):
    calls = 0

    def fetch(sources):
        nonlocal calls
        calls += 1
        return {"sources": [], "plugins": [{"id": "demo-plugin"}]}

    monkeypatch.setattr(
        "src.extension_host.source_config.fetch_plugin_catalog",
        fetch,
    )
    service = PluginCatalogService(())

    first = service.get()
    first["plugins"].clear()
    second = service.get()

    assert calls == 1
    assert second["plugins"] == [{"id": "demo-plugin"}]


def test_catalog_service_returns_immediately_during_first_refresh(
    tmp_path: Path,
    monkeypatch,
):
    started = threading.Event()
    release = threading.Event()

    def fetch(sources):
        started.set()
        assert release.wait(timeout=2)
        return {"sources": [], "plugins": []}

    monkeypatch.setattr(
        "src.extension_host.source_config.fetch_plugin_catalog",
        fetch,
    )
    service = PluginCatalogService(())
    worker = threading.Thread(target=service.get)
    worker.start()
    assert started.wait(timeout=2)

    concurrent = service.get()
    release.set()
    worker.join(timeout=2)

    assert concurrent == {
        "sources": [],
        "plugins": [],
        "refreshing": True,
    }
    assert worker.is_alive() is False


def test_catalog_service_uses_frozen_canonical_source_snapshot(
    tmp_path: Path,
):
    repository = _repository(tmp_path)
    source_file = _source_file(tmp_path, repository)
    sources = load_plugin_sources(source_file)
    service = PluginCatalogService(sources)

    source_file.write_text(
        json.dumps({
            "schema_version": 1,
            "official_sources": [{
                "name": "Changed",
                "url": "https://user:secret@example.invalid/plugins.git",  # pragma: allowlist secret
            }],
        }),
        encoding="utf-8",
    )

    catalog = service.get()

    assert catalog["sources"][0]["name"] == "Local Official"
    assert catalog["sources"][0]["url"] == repository.resolve().as_uri()
    assert catalog["plugins"][0]["source"] == repository.resolve().as_uri()


def test_source_config_rejects_inline_http_credentials(tmp_path: Path):
    source_file = tmp_path / "plugin-sources.json"
    source_file.write_text(
        json.dumps({
            "schema_version": 1,
            "official_sources": [{
                "name": "Unsafe",
                "url": "https://user:secret@example.invalid/plugins.git",  # pragma: allowlist secret
            }],
        }),
        encoding="utf-8",
    )

    try:
        load_plugin_sources(source_file)
    except ValueError as exc:
        assert "credentials" in str(exc) or "password" in str(exc)
    else:
        raise AssertionError("inline credentials must be rejected")


def test_repository_index_uses_install_path_and_ref_validation(
    tmp_path: Path,
):
    repository = _repository(tmp_path, subdirectory="..\\\\escape")
    source_file = _source_file(tmp_path, repository)

    catalog = fetch_plugin_catalog(load_plugin_sources(source_file))

    assert catalog["plugins"] == []
    assert "子路径无效" in catalog["sources"][0]["error"]
