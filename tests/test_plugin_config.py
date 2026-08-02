import json
from pathlib import Path

import pytest

from src.extension_api import ExtensionManifest
from src.extension_api.registrar import OwnedPath
from src.extension_host.plugin_config import (
    PluginConfigStore,
    load_applied_plugin_configs,
    load_settings_schema,
    redact_plugin_settings,
    settings_environment,
)
from src.workflow.script_library import ScriptLibraryCatalog


def test_plugin_config_applies_defaults_and_validates_nested_values(tmp_path: Path):
    schema = {
        "type": "object",
        "properties": {
            "endpoint": {
                "type": "string",
                "format": "uri",
                "default": "http://127.0.0.1:8080",
            },
            "worker": {
                "type": "object",
                "properties": {
                    "enabled": {"type": "boolean", "default": True},
                    "retries": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 5,
                    },
                },
                "required": ["retries"],
            },
            "tags": {
                "type": "array",
                "items": {"type": "string"},
                "default": [],
            },
        },
        "required": ["worker"],
    }
    store = PluginConfigStore(tmp_path / "config")

    saved = store.save("demo", schema, {"worker": {"retries": 3}})

    assert saved == {"worker": {"retries": 3}}
    assert store.load("demo") == saved
    plugin_root = tmp_path / "plugin"
    plugin_root.mkdir()
    (plugin_root / "settings.schema.json").write_text(
        json.dumps(schema),
        encoding="utf-8",
    )
    applied = load_applied_plugin_configs(
        store,
        {
            "demo": ExtensionManifest(
                extension_id="demo",
                name="Demo",
                version="1.0.0",
                base_path=plugin_root,
                settings_schema="settings.schema.json",
            )
        },
        ["demo"],
    )
    assert applied == {
        "demo": {
            "endpoint": "http://127.0.0.1:8080",
            "worker": {"enabled": True, "retries": 3},
            "tags": [],
        }
    }


def test_plugin_config_preserves_existing_password_when_empty(tmp_path: Path):
    schema = {
        "type": "object",
        "properties": {
            "token": {"type": "string", "format": "password"},
        },
    }
    store = PluginConfigStore(tmp_path / "config")
    store.save("demo", schema, {"token": "secret"})

    saved = store.save("demo", schema, {"token": ""})

    assert saved == {"token": "secret"}


def test_plugin_config_tracks_historical_password_paths(tmp_path: Path):
    password_schema = {
        "type": "object",
        "properties": {
            "token": {"type": "string", "format": "password"},
        },
    }
    plain_schema = {
        "type": "object",
        "properties": {
            "token": {"type": "string"},
        },
    }
    store = PluginConfigStore(tmp_path / "config")
    store.save("demo", password_schema, {"token": "secret"})

    assert store.sensitive_paths(
        "demo",
        plain_schema,
        store.load("demo"),
    ) == {("token",)}

    store.delete("demo")

    assert not store.path_for("demo").exists()
    assert not store.sensitive_path_for("demo").exists()


def test_plugin_password_settings_are_redacted_recursively() -> None:
    schema = {
        "type": "object",
        "properties": {
            "username": {"type": "string"},
            "token": {"type": "string", "format": "password"},
            "service": {
                "type": "object",
                "properties": {
                    "endpoint": {"type": "string"},
                    "secret": {"type": "string", "format": "password"},
                },
            },
        },
    }

    redacted = redact_plugin_settings(
        schema,
        {
            "username": "operator",
            "token": "top-secret",
            "service": {
                "endpoint": "http://127.0.0.1",
                "secret": "nested-secret",  # pragma: allowlist secret
            },
        },
    )

    assert redacted == {
        "username": "operator",
        "token": "",
        "service": {
            "endpoint": "http://127.0.0.1",
            "secret": "",
        },
    }


def test_plugin_settings_redaction_drops_fields_removed_from_schema() -> None:
    schema = {
        "type": "object",
        "properties": {
            "current": {"type": "string"},
        },
    }

    redacted = redact_plugin_settings(
        schema,
        {
            "current": "visible",
            "OLD_TOKEN": "must-not-leak",
        },
    )

    assert redacted == {"current": "visible"}


@pytest.mark.parametrize(
    ("values", "message"),
    [
        ({"worker": {"retries": 0}}, "minimum"),
        ({"worker": {"retries": 2.5}}, "integer"),
        ({"worker": {"retries": 2}, "extra": True}, "未知配置项"),
        ({"worker": {"retries": 2}, "tags": [1]}, "string"),
    ],
)
def test_plugin_config_rejects_invalid_values(
    tmp_path: Path,
    values: dict,
    message: str,
):
    schema = {
        "type": "object",
        "properties": {
            "worker": {
                "type": "object",
                "properties": {
                    "retries": {"type": "integer", "minimum": 1},
                },
                "required": ["retries"],
            },
            "tags": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["worker"],
    }
    store = PluginConfigStore(tmp_path / "config")

    with pytest.raises(ValueError, match=message):
        store.save("demo", schema, values)


def test_load_settings_schema_rejects_remote_ref_and_path_escape(tmp_path: Path):
    plugin_dir = tmp_path / "plugin"
    plugin_dir.mkdir()
    schema_path = plugin_dir / "settings.schema.json"
    schema_path.write_text(
        json.dumps({"type": "object", "$ref": "https://example.com/schema.json"}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="不支持的 Schema 关键字"):
        load_settings_schema(plugin_dir, "settings.schema.json")

    outside = tmp_path / "outside.json"
    outside.write_text('{"type":"object"}', encoding="utf-8")
    schema_path.unlink()
    schema_path.symlink_to(outside)

    with pytest.raises(ValueError, match="必须位于 Plugin 目录内"):
        load_settings_schema(plugin_dir, "settings.schema.json")


@pytest.mark.parametrize(
    ("field_schema", "message"),
    [
        ({"type": "integer", "minimum": "bad"}, "必须是数字"),
        ({"type": "string", "minimum": 1}, "只有 number/integer"),
        (
            {"type": "integer", "minimum": 2, "maximum": 1},
            "minimum 不能大于",
        ),
        ({"type": "integer", "default": "bad"}, "default"),
        ({"type": "integer", "enum": ["bad"]}, "enum"),
    ],
)
def test_settings_schema_rejects_invalid_bounds_defaults_and_enum(
    tmp_path: Path,
    field_schema: dict,
    message: str,
):
    plugin_dir = tmp_path / "plugin"
    plugin_dir.mkdir()
    (plugin_dir / "settings.schema.json").write_text(
        json.dumps({
            "type": "object",
            "properties": {"count": field_schema},
        }),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match=message):
        load_settings_schema(plugin_dir, "settings.schema.json")


def test_plugin_settings_become_owner_scoped_script_environment(tmp_path: Path):
    values = {
        "DB_PORT": 5432,
        "ENABLED": True,
        "OPTIONS": ["one", "two"],
        "lowercase": "ignored",
    }
    environment = settings_environment(values)
    extension_root = tmp_path / "extension-library"
    extension_root.mkdir()
    catalog = ScriptLibraryCatalog(
        tmp_path / "user-library",
        [OwnedPath("demo-plugin", extension_root)],
        owner_environment=lambda owner: environment,
    )

    assert catalog.environment("demo-plugin") == {
        "DB_PORT": "5432",
        "ENABLED": "true",
        "OPTIONS": '["one","two"]',
    }
    assert catalog.environment("user") == {}


def test_plugin_environment_inherits_only_schema_declared_runtime_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    schema = {
        "type": "object",
        "properties": {
            "DB_HOST": {"type": "string"},
            "DB_PASSWORD": {"type": "string", "format": "password"},
            "ENABLED": {"type": "boolean"},
            "lowercase": {"type": "string"},
        },
    }
    monkeypatch.setenv("DB_HOST", "database.internal")
    monkeypatch.setenv("DB_PASSWORD", "inline-secret-must-not-win")
    monkeypatch.setenv("DB_PASSWORD_FILE", "/run/secrets/database-password")
    monkeypatch.setenv("ENABLED", "true")
    monkeypatch.setenv("HOST_SECRET", "must-not-leak")

    environment = settings_environment(
        {"ENABLED": False, "lowercase": "ignored"},
        schema=schema,
    )

    assert environment == {
        "DB_HOST": "database.internal",
        "DB_PASSWORD_FILE": "/run/secrets/database-password",
        "ENABLED": "false",
    }


def test_runtime_environment_precedes_schema_default_but_not_saved_value(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    plugin_root = tmp_path / "plugin"
    plugin_root.mkdir()
    (plugin_root / "settings.schema.json").write_text(
        json.dumps({
            "type": "object",
            "properties": {
                "DB_HOST": {
                    "type": "string",
                    "default": "127.0.0.1",
                },
                "ENABLED": {
                    "type": "boolean",
                    "default": False,
                },
                "port": {
                    "type": "integer",
                    "default": 8080,
                },
                "DB_PASSWORD": {
                    "type": "string",
                    "format": "password",
                    "default": "schema-password",
                },
            },
            "required": ["DB_HOST", "DB_PASSWORD"],
        }),
        encoding="utf-8",
    )
    manifest = ExtensionManifest(
        extension_id="demo-plugin",
        name="Demo",
        version="1.0.0",
        base_path=plugin_root,
        settings_schema="settings.schema.json",
    )
    store = PluginConfigStore(tmp_path / "config")
    monkeypatch.setenv("DB_HOST", "database.internal")
    monkeypatch.setenv("ENABLED", "true")
    monkeypatch.setenv("DB_PASSWORD_FILE", "/run/secrets/database-password")

    from_environment = load_applied_plugin_configs(
        store,
        {"demo-plugin": manifest},
        ["demo-plugin"],
    )
    assert from_environment == {"demo-plugin": {"port": 8080}}

    saved = store.save(
        "demo-plugin",
        load_settings_schema(plugin_root, "settings.schema.json"),
        {"port": 9000},
    )
    assert saved == {"port": 9000}
    assert json.loads(
        store.path_for("demo-plugin").read_text(encoding="utf-8")
    ) == {"port": 9000}
    assert load_applied_plugin_configs(
        store,
        {"demo-plugin": manifest},
        ["demo-plugin"],
    ) == {"demo-plugin": {"port": 9000}}

    store.path_for("demo-plugin").write_text(
        '{"DB_HOST":"configured.internal","port":9000}',
        encoding="utf-8",
    )
    with_saved_value = load_applied_plugin_configs(
        store,
        {"demo-plugin": manifest},
        ["demo-plugin"],
    )
    assert with_saved_value == {
        "demo-plugin": {
            "DB_HOST": "configured.internal",
            "port": 9000,
        },
    }
