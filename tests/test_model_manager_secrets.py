from __future__ import annotations

import json
from pathlib import Path

from src.core.model_manager import ModelManager


def test_provider_api_key_is_resolved_without_mutating_config(
    tmp_path: Path,
    monkeypatch,
):
    config_path = tmp_path / "models_config.json"
    config_path.write_text(
        json.dumps({
            "providers": {
                "demo": {
                    "name": "Demo",
                    "api_key": "",
                    "api_key_env": "TEST_PROVIDER_API_KEY",  # pragma: allowlist secret
                    "models": ["demo-model"],
                }
            }
        }),
        encoding="utf-8",
    )
    monkeypatch.setenv("TEST_PROVIDER_API_KEY", "test-key")

    manager = ModelManager(str(config_path))

    assert manager.get_provider("demo")["api_key"] == "test-key"  # pragma: allowlist secret
    assert manager.get_all_providers()["demo"]["api_key"] == ""

    manager.update_provider("demo", {"name": "Renamed"})
    assert manager.get_provider("demo")["name"] == "Renamed"


def test_provider_api_key_falls_back_to_provider_named_env(
    tmp_path: Path,
    monkeypatch,
):
    config_path = tmp_path / "models_config.json"
    config_path.write_text(
        json.dumps({
            "providers": {
                "demo": {
                    "name": "Demo",
                    "api_key": "",
                    "models": ["demo-model"],
                }
            }
        }),
        encoding="utf-8",
    )
    monkeypatch.setenv("DEMO_API_KEY", "fallback-key")

    manager = ModelManager(str(config_path))

    assert manager.get_provider("demo")["api_key"] == "fallback-key"  # pragma: allowlist secret


def test_env_migration_keeps_default_deepseek_key_name(
    tmp_path: Path,
    monkeypatch,
):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(
        "DEEPSEEK_API_KEY=test-only-value\nOPENAI_API_KEY=\n",  # pragma: allowlist secret
        encoding="utf-8",
    )

    config_path = tmp_path / "models_config.json"
    manager = ModelManager(str(config_path))

    assert manager.config["providers"]["deepseek"]["api_key_env"] == "DEEPSEEK_API_KEY"  # pragma: allowlist secret
    assert "test-only-value" not in config_path.read_text(encoding="utf-8")
