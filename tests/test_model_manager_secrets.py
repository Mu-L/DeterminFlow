from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from src.core.model_manager import ModelManager


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_example_model_config_uses_one_api_key_field() -> None:
    document = json.loads(
        (REPO_ROOT / "config/models_config.example.json").read_text(encoding="utf-8")
    )

    assert document["providers"]["deepseek"]["api_key"] == "${DEEPSEEK_API_KEY}"
    assert all(
        "api_key_env" not in provider
        for provider in document["providers"].values()
    )


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
                    "api_key": "${TEST_PROVIDER_API_KEY}",
                    "models": ["demo-model"],
                }
            }
        }),
        encoding="utf-8",
    )
    monkeypatch.setenv("TEST_PROVIDER_API_KEY", "test-key")

    manager = ModelManager(str(config_path))

    assert manager.get_provider("demo")["api_key"] == "test-key"
    assert manager.get_all_providers()["demo"]["api_key"] == "${TEST_PROVIDER_API_KEY}"

    manager.update_provider("demo", {"name": "Renamed"})
    assert manager.get_provider("demo")["name"] == "Renamed"


def test_provider_api_key_does_not_guess_an_environment_variable(
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

    assert manager.get_provider("demo")["api_key"] == ""


def test_provider_api_key_supports_inline_values(tmp_path: Path) -> None:
    config_path = tmp_path / "models_config.json"
    config_path.write_text(
        json.dumps({
            "providers": {
                "demo": {
                    "name": "Demo",
                    "api_key": "inline-test-key",
                    "models": ["demo-model"],
                }
            }
        }),
        encoding="utf-8",
    )

    manager = ModelManager(str(config_path))

    assert manager.get_provider("demo")["api_key"] == "inline-test-key"


def test_missing_environment_reference_resolves_to_unconfigured(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config_path = tmp_path / "models_config.json"
    config_path.write_text(
        json.dumps({
            "providers": {
                "demo": {
                    "name": "Demo",
                    "api_key": "${MISSING_PROVIDER_API_KEY}",
                    "models": ["demo-model"],
                }
            }
        }),
        encoding="utf-8",
    )
    monkeypatch.delenv("MISSING_PROVIDER_API_KEY", raising=False)

    manager = ModelManager(str(config_path))

    assert manager.get_provider("demo")["api_key"] == ""
    assert manager.get_all_providers()["demo"]["api_key"] == "${MISSING_PROVIDER_API_KEY}"


def test_legacy_api_key_env_is_migrated_to_the_api_key_expression(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "models_config.json"
    config_path.write_text(
        json.dumps({
            "providers": {
                "demo": {
                    "name": "Demo",
                    "api_key": "",
                    "api_key_env": "DEMO_API_KEY",
                    "models": ["demo-model"],
                }
            }
        }),
        encoding="utf-8",
    )

    manager = ModelManager(str(config_path))
    persisted = json.loads(config_path.read_text(encoding="utf-8"))

    assert manager.get_all_providers()["demo"]["api_key"] == "${DEMO_API_KEY}"
    assert "api_key_env" not in manager.get_all_providers()["demo"]
    assert persisted["providers"]["demo"]["api_key"] == "${DEMO_API_KEY}"
    assert "api_key_env" not in persisted["providers"]["demo"]


def test_missing_config_uses_the_default_expression_instead_of_env_migration(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config_path = tmp_path / "config" / "models_config.json"
    (tmp_path / ".env").write_text(
        "OPENAI_API_KEY=legacy-test-value\nMODEL_NAME=legacy-model\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    manager = ModelManager(str(config_path))

    provider = manager.get_all_providers()["deepseek"]
    assert provider["api_key"] == "${DEEPSEEK_API_KEY}"
    assert provider["models"] == ["deepseek-v4-flash", "deepseek-v4-pro"]
    assert manager.config == json.loads(
        (REPO_ROOT / "config/models_config.example.json").read_text(encoding="utf-8")
    )


def test_default_model_config_path_can_use_a_secret_file(tmp_path: Path) -> None:
    config_path = tmp_path / "models_config.json"
    config_path.write_text(json.dumps({"providers": {}}), encoding="utf-8")
    environment = os.environ.copy()
    environment["DETERMINFLOW_MODELS_CONFIG_FILE"] = str(config_path)

    result = subprocess.run(
        (
            sys.executable,
            "-c",
            "import sys; from pathlib import Path; "
            "from src.core.model_manager import ModelManager; "
            "manager = ModelManager(); "
            "assert manager.config_path == Path(sys.argv[1])",
            str(config_path),
        ),
        cwd=REPO_ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
