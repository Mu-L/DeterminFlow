from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.extension_api import CoreRuntime
from src.extension_host.manager import ExtensionManager
from src.plugin_system import PluginStore, PluginStoreError
from src.web_server import create_app


class _ToolRegistry:
    def unregister_owner(self, owner: str) -> None:
        return None


def _runtime() -> CoreRuntime:
    return CoreRuntime(
        app=object(),
        session_manager=object(),
        workflow_runtime=object(),
        tool_registry=_ToolRegistry(),
        event_publisher=None,
    )


@pytest.fixture
def admin_headers(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    token = "plugin-admin-token-with-at-least-32-bytes"
    monkeypatch.setenv("AI_COMPANY_PLUGIN_ADMIN_TOKEN", token)
    return {"Authorization": f"Bearer {token}"}


def _git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _create_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "plugin-repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.name", "Plugin Test")
    _git(repo, "config", "user.email", "plugin-test@example.invalid")
    (repo / "extension.toml").write_text(
        """
[extension]
id = "demo-plugin"
name = "Demo Plugin"
version = "1.0.0"
api_version = "1"
capabilities = ["resources.workflows"]

[resource_namespace]
prefix = "demo"

[settings]
schema = "settings.schema.json"

[page]
label = "Demo"
static_dir = "ui"
entrypoint = "index.html"
""",
        encoding="utf-8",
    )
    (repo / "settings.schema.json").write_text(
        json.dumps({
            "type": "object",
            "properties": {
                "port": {
                    "type": "integer",
                    "minimum": 1,
                    "default": 8080,
                },
                "token": {
                    "type": "string",
                    "format": "password",
                },
            },
        }),
        encoding="utf-8",
    )
    ui_dir = repo / "ui"
    ui_dir.mkdir()
    (ui_dir / "index.html").write_text("<h1>Plugin page</h1>", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "initial")
    return repo


def _create_dependency_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "dependency-repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.name", "Plugin Test")
    _git(repo, "config", "user.email", "plugin-test@example.invalid")
    for plugin_id, dependencies in (
        ("base-plugin", []),
        ("feature-plugin", ["base-plugin"]),
    ):
        plugin_dir = repo / "plugins" / plugin_id
        plugin_dir.mkdir(parents=True)
        dependency_values = ", ".join(
            f'"{dependency}"' for dependency in dependencies
        )
        (plugin_dir / "extension.toml").write_text(
            "\n".join([
                "[extension]",
                f'id = "{plugin_id}"',
                f'name = "{plugin_id}"',
                'version = "1.0.0"',
                'api_version = "1"',
                f"dependencies = [{dependency_values}]",
            ]),
            encoding="utf-8",
        )
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "initial")
    return repo


def _manager(
    base_dir: Path,
    store: PluginStore,
) -> ExtensionManager:
    config_dir = base_dir / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    config_file = config_dir / "extensions.json"
    if not config_file.exists():
        config_file.write_text(
            '{"enabled":[],"strict_startup":false}',
            encoding="utf-8",
        )
    return ExtensionManager(
        base_dir,
        config_file=config_file,
        plugin_store=store,
        discover_entry_points=False,
    )


def test_plugin_management_routes_preserve_restart_boundary_and_static_page(
    tmp_path: Path,
    admin_headers: dict[str, str],
):
    repo = _create_repo(tmp_path)
    base_dir = tmp_path / "core"
    store = PluginStore(
        tmp_path / "runtime" / "plugins",
        official_sources=[str(repo)],
    )
    manager = _manager(base_dir, store)
    client = TestClient(create_app(manager))

    assert client.get("/api/plugins").json() == {
        "plugins": [],
        "restart_required": False,
        "package_management_read_only": False,
    }

    installed = client.post(
        "/api/plugins/install",
        headers=admin_headers,
        json={
            "plugin_id": "demo-plugin",
            "source": str(repo),
            "ref": "main",
            "subdirectory": "",
            "resource_prefix": "custom-demo",
            "acknowledge_risk": False,
        },
    )
    assert installed.status_code == 200
    plugin = installed.json()["plugin"]
    assert plugin["active_version"] is None
    assert plugin["desired_version"] == "1.0.0"
    assert plugin["source"]["trust"] == "official"
    assert plugin["resource_prefix"] == "custom-demo"
    assert plugin["pending_action"] == "install"
    assert plugin["restart_required"] is True
    assert manager.get_statuses() == []

    enabled = client.put(
        "/api/plugins/demo-plugin/enabled",
        headers=admin_headers,
        json={"enabled": True},
    )
    assert enabled.status_code == 200
    assert enabled.json()["plugin"]["desired_enabled"] is True
    assert manager.get_statuses() == []

    configured = client.put(
        "/api/plugins/demo-plugin/config",
        headers=admin_headers,
        json={"settings": {}},
    )
    assert configured.status_code == 200
    assert configured.json()["plugin"]["settings"] == {}
    assert configured.json()["restart_required"] is True
    secret_configured = client.put(
        "/api/plugins/demo-plugin/config",
        headers=admin_headers,
        json={"settings": {"port": 8080, "token": "not-returned"}},
    )
    assert secret_configured.status_code == 200
    assert secret_configured.json()["plugin"]["settings"] == {
        "port": 8080,
        "token": "",
    }
    assert json.loads(
        (store.root / "config" / "demo-plugin.json").read_text(encoding="utf-8")
    )["token"] == "not-returned"

    restarted = _manager(base_dir, store)

    async def run_start() -> None:
        await restarted.start(_runtime())

    asyncio.run(run_start())
    restarted_client = TestClient(create_app(restarted))
    response = restarted_client.get("/api/plugins")
    assert response.status_code == 200
    active = response.json()["plugins"][0]
    assert active["runtime_status"] == "running"
    assert active["active_enabled"] is True
    assert active["desired_enabled"] is True
    assert active["restart_required"] is False
    assert response.json()["restart_required"] is False

    page = restarted_client.get("/api/plugins/demo-plugin/ui/index.html")
    assert page.status_code == 200
    assert "Plugin page" in page.text

    asyncio.run(restarted.stop())


def test_plugin_list_drops_removed_secret_fields_but_allows_reset(
    tmp_path: Path,
    admin_headers: dict[str, str],
):
    repo = _create_repo(tmp_path)
    store = PluginStore(
        tmp_path / "runtime" / "plugins",
        official_sources=[str(repo)],
    )
    store.install("demo-plugin", str(repo))
    store_config = store.root / "config"
    store_config.mkdir(parents=True)
    config_path = store_config / "demo-plugin.json"
    config_path.write_text(
        '{"OLD_TOKEN":"must-not-leak"}',
        encoding="utf-8",
    )
    client = TestClient(create_app(_manager(tmp_path / "core", store)))

    listed = client.get("/api/plugins")

    assert listed.status_code == 200
    assert "must-not-leak" not in listed.text
    plugin = listed.json()["plugins"][0]
    assert plugin["settings"] == {}
    assert plugin["config_present"] is True

    reset = client.delete(
        "/api/plugins/demo-plugin/config",
        headers=admin_headers,
    )

    assert reset.status_code == 200
    assert reset.json()["plugin"]["config_present"] is False
    assert not config_path.exists()


def test_plugin_list_fails_closed_when_schema_changes_object_to_string(
    tmp_path: Path,
):
    repo = _create_repo(tmp_path)
    store = PluginStore(
        tmp_path / "runtime" / "plugins",
        official_sources=[str(repo)],
    )
    store.install("demo-plugin", str(repo))
    store_config = store.root / "config"
    store_config.mkdir(parents=True)
    (store_config / "demo-plugin.json").write_text(
        '{"token":{"old_password":"must-not-leak"}}',  # pragma: allowlist secret
        encoding="utf-8",
    )
    client = TestClient(create_app(_manager(tmp_path / "core", store)))

    listed = client.get("/api/plugins")

    assert listed.status_code == 200
    assert "must-not-leak" not in listed.text
    plugin = listed.json()["plugins"][0]
    assert plugin["settings"] == {}
    assert plugin["config_present"] is True
    assert "必须是 string" in plugin["error"]


def test_plugin_list_keeps_historical_password_redacted_after_downgrade(
    tmp_path: Path,
    admin_headers: dict[str, str],
):
    repo = _create_repo(tmp_path)
    store = PluginStore(
        tmp_path / "runtime" / "plugins",
        official_sources=[str(repo)],
    )
    store.install("demo-plugin", str(repo))
    base_dir = tmp_path / "core"
    first_client = TestClient(create_app(_manager(base_dir, store)))
    saved = first_client.put(
        "/api/plugins/demo-plugin/config",
        headers=admin_headers,
        json={"settings": {"token": "must-not-leak"}},
    )
    assert saved.status_code == 200

    schema_path = repo / "settings.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    schema["properties"]["token"].pop("format")
    schema_path.write_text(json.dumps(schema), encoding="utf-8")
    _git(repo, "add", "settings.schema.json")
    _git(repo, "commit", "-m", "downgrade token sensitivity")
    store.update("demo-plugin")

    listed = TestClient(
        create_app(_manager(base_dir, store))
    ).get("/api/plugins")

    assert listed.status_code == 200
    assert "must-not-leak" not in listed.text
    plugin = listed.json()["plugins"][0]
    assert plugin["settings"] == {"token": ""}
    assert plugin["config_present"] is True


def test_release_read_only_mode_rejects_package_mutations_but_allows_state(
    tmp_path: Path,
    admin_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
):
    repo = _create_repo(tmp_path)
    store = PluginStore(
        tmp_path / "runtime" / "plugins",
        official_sources=[str(repo)],
    )
    store.install("demo-plugin", str(repo))
    monkeypatch.setenv("AI_COMPANY_PLUGIN_PACKAGES_READ_ONLY", "true")
    manager = _manager(tmp_path / "core", store)
    client = TestClient(create_app(manager))

    listed = client.get("/api/plugins").json()
    assert listed["package_management_read_only"] is True
    assert client.get("/api/plugins/catalog").json() == {
        "sources": [],
        "plugins": [],
        "package_management_read_only": True,
    }

    for response in (
        client.post(
            "/api/plugins/install",
            headers=admin_headers,
            json={
                "plugin_id": "other-plugin",
                "source": str(repo),
                "acknowledge_risk": False,
            },
        ),
        client.post(
            "/api/plugins/demo-plugin/update",
            headers=admin_headers,
        ),
        client.post(
            "/api/plugins/demo-plugin/rollback",
            headers=admin_headers,
        ),
        client.delete(
            "/api/plugins/demo-plugin",
            headers=admin_headers,
        ),
    ):
        assert response.status_code == 400
        assert "不可变 Release" in response.json()["detail"]

    enabled = client.put(
        "/api/plugins/demo-plugin/enabled",
        headers=admin_headers,
        json={"enabled": True},
    )
    assert enabled.status_code == 200
    configured = client.put(
        "/api/plugins/demo-plugin/config",
        headers=admin_headers,
        json={"settings": {"port": 8081}},
    )
    assert configured.status_code == 200


def test_third_party_install_requires_risk_acknowledgement(
    tmp_path: Path,
    admin_headers: dict[str, str],
):
    repo = _create_repo(tmp_path)
    manager = _manager(
        tmp_path / "core",
        PluginStore(tmp_path / "runtime" / "plugins"),
    )
    client = TestClient(create_app(manager))
    payload = {
        "plugin_id": "demo-plugin",
        "source": str(repo),
        "acknowledge_risk": False,
    }

    rejected = client.post(
        "/api/plugins/install",
        headers=admin_headers,
        json=payload,
    )

    assert rejected.status_code == 403
    assert "acknowledge_risk" in rejected.json()["detail"]

    payload["acknowledge_risk"] = True
    accepted = client.post(
        "/api/plugins/install",
        headers=admin_headers,
        json=payload,
    )
    assert accepted.status_code == 200
    assert accepted.json()["plugin"]["source"]["trust"] == "third_party"


def test_install_uses_manifest_prefix_and_rejects_invalid_override(
    tmp_path: Path,
    admin_headers: dict[str, str],
):
    repo = _create_repo(tmp_path)
    store = PluginStore(
        tmp_path / "runtime" / "plugins",
        official_sources=[str(repo)],
    )
    client = TestClient(create_app(_manager(tmp_path / "core", store)))

    rejected = client.post(
        "/api/plugins/install",
        headers=admin_headers,
        json={
            "plugin_id": "demo-plugin",
            "source": str(repo),
            "resource_prefix": "Bad_Prefix",
        },
    )

    assert rejected.status_code == 400
    assert store.get("demo-plugin") is None

    installed = client.post(
        "/api/plugins/install",
        headers=admin_headers,
        json={
            "plugin_id": "demo-plugin",
            "source": str(repo),
        },
    )

    assert installed.status_code == 200
    assert installed.json()["plugin"]["resource_prefix"] == "demo"


def test_requirements_install_only_when_restart_applies_plugin(
    tmp_path: Path,
    admin_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
):
    repo = _create_repo(tmp_path)
    manifest_path = repo / "extension.toml"
    manifest_path.write_text(
        manifest_path.read_text(encoding="utf-8")
        + '\n[installation]\nrequirements = "requirements.txt"\n',
        encoding="utf-8",
    )
    (repo / "requirements.txt").write_text("", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "add requirements")
    installed_requirements: list[str] = []
    monkeypatch.setattr(
        "src.plugin_system.dependencies.install_plugin_requirements",
        lambda manifest: installed_requirements.append(manifest.extension_id),
    )
    base_dir = tmp_path / "core"
    store = PluginStore(
        tmp_path / "runtime" / "plugins",
        official_sources=[str(repo)],
    )
    client = TestClient(create_app(_manager(base_dir, store)))

    assert client.post(
        "/api/plugins/install",
        headers=admin_headers,
        json={
            "plugin_id": "demo-plugin",
            "source": str(repo),
            "ref": "main",
        },
    ).status_code == 200
    assert installed_requirements == []
    assert client.put(
        "/api/plugins/demo-plugin/enabled",
        headers=admin_headers,
        json={"enabled": True},
    ).status_code == 200
    assert installed_requirements == []

    _manager(base_dir, store)
    assert installed_requirements == ["demo-plugin"]


def test_invalid_package_preflight_does_not_pollute_desired_lock(
    tmp_path: Path,
    admin_headers: dict[str, str],
):
    repo = _create_repo(tmp_path)
    manifest_path = repo / "extension.toml"
    manifest_path.write_text(
        manifest_path.read_text(encoding="utf-8")
        + '\n[installation]\nrequirements = "missing.txt"\n',
        encoding="utf-8",
    )
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "break requirements")
    store = PluginStore(
        tmp_path / "runtime" / "plugins",
        official_sources=[str(repo)],
    )
    client = TestClient(create_app(_manager(tmp_path / "core", store)))

    response = client.post(
        "/api/plugins/install",
        headers=admin_headers,
        json={
            "plugin_id": "demo-plugin",
            "source": str(repo),
            "ref": "main",
        },
    )

    assert response.status_code == 400
    assert "requirements" in response.json()["detail"]
    assert store.get("demo-plugin") is None
    assert client.get("/api/plugins").json()["plugins"] == []


def test_reset_config_recovers_schema_removed_fields(
    tmp_path: Path,
    admin_headers: dict[str, str],
):
    repo = _create_repo(tmp_path)
    store = PluginStore(
        tmp_path / "runtime" / "plugins",
        official_sources=[str(repo)],
    )
    store.install("demo-plugin", str(repo))
    config_root = store.root / "config"
    config_root.mkdir()
    config_file = config_root / "demo-plugin.json"
    config_file.write_text('{"removed":"legacy"}', encoding="utf-8")
    client = TestClient(create_app(_manager(tmp_path / "core", store)))

    before = client.get("/api/plugins").json()["plugins"][0]
    assert before["settings"] == {}
    assert before["config_present"] is True
    reset = client.delete(
        "/api/plugins/demo-plugin/config",
        headers=admin_headers,
    )

    assert reset.status_code == 200
    assert reset.json()["plugin"]["settings"] == {}
    assert reset.json()["restart_required"] is True
    assert config_file.exists() is False


def test_update_rollback_and_uninstall_keep_old_checkout_and_config(
    tmp_path: Path,
    admin_headers: dict[str, str],
):
    repo = _create_repo(tmp_path)
    base_dir = tmp_path / "core"
    store = PluginStore(
        tmp_path / "runtime" / "plugins",
        official_sources=[str(repo)],
    )
    manager = _manager(base_dir, store)
    client = TestClient(create_app(manager))
    client.post(
        "/api/plugins/install",
        headers=admin_headers,
        json={
            "plugin_id": "demo-plugin",
            "source": str(repo),
            "ref": "main",
            "acknowledge_risk": False,
        },
    )
    client.put(
        "/api/plugins/demo-plugin/config",
        headers=admin_headers,
        json={"settings": {"port": 9000}},
    )
    first_checkout = Path(store.get("demo-plugin").active_revision.checkout_path)
    manifest = repo / "extension.toml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace("1.0.0", "1.1.0"),
        encoding="utf-8",
    )
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "update")

    updated = client.post(
        "/api/plugins/demo-plugin/update",
        headers=admin_headers,
        json={"ref": "main"},
    )
    assert updated.status_code == 200
    assert updated.json()["plugin"]["desired_version"] == "1.1.0"
    second_checkout = Path(store.get("demo-plugin").active_revision.checkout_path)

    rolled_back = client.post(
        "/api/plugins/demo-plugin/rollback",
        headers=admin_headers,
    )
    assert rolled_back.status_code == 200
    assert rolled_back.json()["plugin"]["desired_version"] == "1.0.0"

    removed = client.delete(
        "/api/plugins/demo-plugin",
        headers=admin_headers,
    )
    assert removed.status_code == 200
    assert removed.json()["plugin"]["desired_version"] is None
    assert removed.json()["plugin"]["pending_action"] == "remove"

    _manager(base_dir, store)
    assert store.get("demo-plugin") is None
    assert first_checkout.exists()
    assert second_checkout.exists()
    assert (store.root / "config" / "demo-plugin.json").exists()


def test_uninstall_failure_restores_enabled_config_and_keeps_lock(
    tmp_path: Path,
    admin_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
):
    repo = _create_repo(tmp_path)
    store = PluginStore(
        tmp_path / "runtime" / "plugins",
        official_sources=[str(repo)],
    )
    store.install("demo-plugin", str(repo))
    base_dir = tmp_path / "core"
    config_dir = base_dir / "config"
    config_dir.mkdir(parents=True)
    config_file = config_dir / "extensions.json"
    config_file.write_text(
        '{"enabled":["demo-plugin"],"strict_startup":false}',
        encoding="utf-8",
    )
    client = TestClient(create_app(_manager(base_dir, store)))

    def fail_uninstall(plugin_id: str):
        raise PluginStoreError(f"lock write failed: {plugin_id}")

    monkeypatch.setattr(
        store,
        "mark_uninstall",
        fail_uninstall,
    )

    response = client.delete(
        "/api/plugins/demo-plugin",
        headers=admin_headers,
    )

    assert response.status_code == 400
    assert json.loads(config_file.read_text(encoding="utf-8"))["enabled"] == [
        "demo-plugin"
    ]
    assert store.get("demo-plugin").pending_action is None


def test_plugin_static_page_rejects_escape_and_disabled_plugin(tmp_path: Path):
    repo = _create_repo(tmp_path)
    store = PluginStore(
        tmp_path / "runtime" / "plugins",
        official_sources=[str(repo)],
    )
    store.install("demo-plugin", str(repo))
    manager = _manager(tmp_path / "core", store)

    try:
        manager.plugin_management.static_file("demo-plugin", "../../outside")
    except PluginStoreError as exc:
        assert "not running" in str(exc)
    else:
        raise AssertionError("disabled Plugin page must be rejected")


def test_enabled_dependent_guards_dependency_disable_and_uninstall(
    tmp_path: Path,
    admin_headers: dict[str, str],
):
    repo = _create_dependency_repo(tmp_path)
    store = PluginStore(
        tmp_path / "runtime" / "plugins",
        official_sources=[str(repo)],
    )
    for plugin_id in ("base-plugin", "feature-plugin"):
        store.install(
            plugin_id,
            str(repo),
            subdirectory=f"plugins/{plugin_id}",
        )
    base_dir = tmp_path / "core"
    config_dir = base_dir / "config"
    config_dir.mkdir(parents=True)
    (config_dir / "extensions.json").write_text(
        '{"enabled":["feature-plugin"],"strict_startup":false}',
        encoding="utf-8",
    )
    client = TestClient(create_app(_manager(base_dir, store)))

    listed = client.get("/api/plugins").json()
    records = {item["id"]: item for item in listed["plugins"]}
    assert records["base-plugin"]["desired_enabled"] is True
    assert records["feature-plugin"]["desired_enabled"] is True
    assert listed["restart_required"] is False

    denied_disable = client.put(
        "/api/plugins/base-plugin/enabled",
        headers=admin_headers,
        json={"enabled": False},
    )
    assert denied_disable.status_code == 400
    assert "feature-plugin" in denied_disable.json()["detail"]
    denied_uninstall = client.delete(
        "/api/plugins/base-plugin",
        headers=admin_headers,
    )
    assert denied_uninstall.status_code == 400

    assert client.put(
        "/api/plugins/feature-plugin/enabled",
        headers=admin_headers,
        json={"enabled": False},
    ).status_code == 200
    allowed_uninstall = client.delete(
        "/api/plugins/base-plugin",
        headers=admin_headers,
    )
    assert allowed_uninstall.status_code == 200


def test_remote_plugin_writes_require_valid_admin_token(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    repo = _create_repo(tmp_path)
    store = PluginStore(
        tmp_path / "runtime" / "plugins",
        official_sources=[str(repo)],
    )
    client = TestClient(create_app(_manager(tmp_path / "core", store)))
    payload = {
        "plugin_id": "demo-plugin",
        "source": str(repo),
    }

    monkeypatch.delenv("AI_COMPANY_PLUGIN_ADMIN_TOKEN", raising=False)
    assert client.get("/api/plugins").status_code == 200
    denied_without_token = client.post("/api/plugins/install", json=payload)
    assert denied_without_token.status_code == 403
    assert store.get("demo-plugin") is None

    token = "correct-plugin-admin-token-with-32-bytes"
    monkeypatch.setenv("AI_COMPANY_PLUGIN_ADMIN_TOKEN", token)
    denied_with_wrong_token = client.post(
        "/api/plugins/install",
        headers={"Authorization": "Bearer wrong"},
        json=payload,
    )
    assert denied_with_wrong_token.status_code == 401
    assert store.get("demo-plugin") is None

    installed = client.post(
        "/api/plugins/install",
        headers={"Authorization": f"Bearer {token}"},
        json=payload,
    )
    assert installed.status_code == 200


def test_plugin_source_routes_persist_custom_repository_and_refresh_catalog(
    tmp_path: Path,
    admin_headers: dict[str, str],
):
    repo = _create_repo(tmp_path)
    (repo / "plugin-repository.toml").write_text(
        """
schema_version = "1"

[[plugins]]
id = "demo-plugin"
subdirectory = ""
""",
        encoding="utf-8",
    )
    _git(repo, "add", "plugin-repository.toml")
    _git(repo, "commit", "-m", "add catalog")
    base_dir = tmp_path / "core"
    store = PluginStore(tmp_path / "runtime" / "plugins")
    client = TestClient(create_app(_manager(base_dir, store)))

    assert client.get("/api/plugins/sources").json()["sources"] == []
    denied = client.post(
        "/api/plugins/sources",
        json={"name": "Team Plugins", "url": str(repo), "ref": "main"},
    )
    assert denied.status_code == 401

    created = client.post(
        "/api/plugins/sources",
        headers=admin_headers,
        json={"name": "Team Plugins", "url": str(repo), "ref": "main"},
    )
    assert created.status_code == 200
    body = created.json()
    source_id = body["source"]["id"]
    assert body["source"]["kind"] == "custom"
    assert body["catalog"]["plugins"][0]["name"] == "Demo Plugin"
    persisted = json.loads(
        (base_dir / "config" / "plugin-sources.json").read_text(
            encoding="utf-8",
        )
    )
    assert persisted["custom_sources"][0]["id"] == source_id

    updated = client.put(
        f"/api/plugins/sources/{source_id}",
        headers=admin_headers,
        json={"name": "Team Stable", "url": str(repo), "ref": "main"},
    )
    assert updated.status_code == 200
    assert updated.json()["source"]["name"] == "Team Stable"

    deleted = client.delete(
        f"/api/plugins/sources/{source_id}",
        headers=admin_headers,
    )
    assert deleted.status_code == 200
    assert client.get("/api/plugins/sources").json()["sources"] == []
