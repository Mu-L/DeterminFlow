from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from src.core.model_manager import DEFAULT_MAX_CONTEXT_TOKENS, ModelManager


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_example_model_config_uses_one_api_key_field() -> None:
    document = json.loads(
        (REPO_ROOT / "config/models_config.example.json").read_text(encoding="utf-8")
    )

    assert document["providers"]["deepseek"]["api_key"] == "${DEEPSEEK_API_KEY}"
    assert document["providers"]["deepseek"]["base_url"] == (
        "https://api.deepseek.com/v1"
    )
    assert document["providers"]["deepseek"]["maxContextTokens"] == 128000
    assert document["providers"]["deepseek"]["provider_type"] == "deepseek"
    assert "category" not in document["providers"]["deepseek"]
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


def test_provider_base_url_is_normalized_on_load(tmp_path: Path) -> None:
    config_path = tmp_path / "models_config.json"
    config_path.write_text(
        json.dumps({
            "providers": {
                "demo": {
                    "name": "Demo",
                    "base_url": "  https://models.example.test/v1///  ",
                    "models": [],
                }
            }
        }),
        encoding="utf-8",
    )

    manager = ModelManager(str(config_path))
    persisted = json.loads(config_path.read_text(encoding="utf-8"))

    assert manager.get_all_providers()["demo"]["base_url"] == (
        "https://models.example.test/v1"
    )
    assert persisted["providers"]["demo"]["base_url"] == (
        "https://models.example.test/v1"
    )


def test_provider_base_url_is_normalized_when_updated(tmp_path: Path) -> None:
    config_path = tmp_path / "models_config.json"
    config_path.write_text(
        json.dumps({"providers": {"demo": {"base_url": "", "models": []}}}),
        encoding="utf-8",
    )
    manager = ModelManager(str(config_path))

    manager.update_provider("demo", {"base_url": "https://custom.example/v1/"})

    assert manager.get_all_providers()["demo"]["base_url"] == (
        "https://custom.example/v1"
    )


def test_provider_base_url_is_normalized_when_added(tmp_path: Path) -> None:
    config_path = tmp_path / "models_config.json"
    config_path.write_text(json.dumps({"providers": {}}), encoding="utf-8")
    manager = ModelManager(str(config_path))

    manager.add_provider(
        "custom",
        {"base_url": "https://custom.example/api/", "models": []},
    )

    assert manager.get_all_providers()["custom"]["base_url"] == (
        "https://custom.example/api"
    )


def test_managed_provider_owner_is_validated_and_persisted(tmp_path: Path) -> None:
    config_path = tmp_path / "models_config.json"
    config_path.write_text(json.dumps({"providers": {}}), encoding="utf-8")
    manager = ModelManager(str(config_path))

    manager.add_provider(
        "managed",
        {
            "base_url": "https://relay.example.test/v1",
            "models": ["public-model"],
            "managed_by": "public-api",
        },
    )

    assert manager.get_all_providers()["managed"]["managed_by"] == "public-api"
    with pytest.raises(ValueError, match="Invalid managed_by"):
        manager.update_provider("managed", {"managed_by": "invalid owner"})


def test_provider_error_messages_are_validated_and_persisted(tmp_path: Path) -> None:
    config_path = tmp_path / "models_config.json"
    config_path.write_text(json.dumps({"providers": {}}), encoding="utf-8")
    manager = ModelManager(str(config_path))

    manager.add_provider(
        "managed",
        {
            "models": ["public-model"],
            "error_messages": {
                "quota_exhausted": "  公益模型额度已用完，请稍后再试  ",
            },
        },
    )

    persisted = json.loads(config_path.read_text(encoding="utf-8"))
    assert persisted["providers"]["managed"]["error_messages"] == {
        "quota_exhausted": "公益模型额度已用完，请稍后再试",
    }
    with pytest.raises(ValueError, match="Unsupported provider error message code"):
        manager.update_provider(
            "managed",
            {"error_messages": {"unsupported": "message"}},
        )


def test_empty_provider_base_url_is_supported(tmp_path: Path) -> None:
    config_path = tmp_path / "models_config.json"
    config_path.write_text(
        json.dumps({"providers": {"demo": {"base_url": "", "models": []}}}),
        encoding="utf-8",
    )
    manager = ModelManager(str(config_path))

    manager.update_provider("demo", {"base_url": None})

    assert manager.get_all_providers()["demo"]["base_url"] == ""


def test_provider_context_window_can_be_updated_and_persisted(tmp_path: Path) -> None:
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
    manager = ModelManager(str(config_path))

    assert manager.get_model_info("demo:demo-model")["maxContextTokens"] == (
        DEFAULT_MAX_CONTEXT_TOKENS
    )

    manager.update_provider("demo", {"maxContextTokens": 64000})

    persisted = json.loads(config_path.read_text(encoding="utf-8"))
    assert persisted["providers"]["demo"]["maxContextTokens"] == 64000
    assert manager.get_model_info("demo:demo-model")["maxContextTokens"] == 64000


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


def test_legacy_provider_category_is_migrated_without_changing_instance_id(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "models_config.json"
    config_path.write_text(
        json.dumps({
            "providers": {
                "primary-ds": {
                    "category": "ds",
                    "base_url": "https://models.example.test/v1",
                    "models": ["model-a"],
                }
            }
        }),
        encoding="utf-8",
    )

    manager = ModelManager(str(config_path))
    persisted = json.loads(config_path.read_text(encoding="utf-8"))

    assert manager.get_provider_type("primary-ds") == "deepseek"
    assert persisted["providers"]["primary-ds"]["provider_type"] == "deepseek"
    assert persisted["providers"]["primary-ds"]["category"] == "ds"


def test_custom_openai_provider_gets_an_explicit_compatible_type(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "models_config.json"
    config_path.write_text(
        json.dumps({
            "providers": {
                "relay-main": {
                    "api_format": "openai",
                    "base_url": "https://relay.example.test/v1",
                    "models": ["model-a"],
                }
            }
        }),
        encoding="utf-8",
    )

    manager = ModelManager(str(config_path))

    assert manager.get_provider_type("relay-main") == "openai_compatible"
    assert set(manager.get_provider_capabilities("relay-main")["model_params"]) == {
        "temperature",
        "top_p",
        "presence_penalty",
        "response_format",
        "stream_chunk_timeout",
    }


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


def test_unspecified_main_uses_first_provider_and_first_model(tmp_path: Path) -> None:
    config_path = tmp_path / "models_config.json"
    config_path.write_text(
        json.dumps({
            "providers": {
                "first": {"name": "First", "models": ["model-a", "model-b"]},
                "second": {"name": "Second", "models": ["model-c"]},
            }
        }),
        encoding="utf-8",
    )

    manager = ModelManager(str(config_path))

    assert manager.get_default_model() == "first:model-a"


def test_no_provider_has_no_hardcoded_default(tmp_path: Path) -> None:
    config_path = tmp_path / "models_config.json"
    config_path.write_text(json.dumps({"providers": {}}), encoding="utf-8")

    manager = ModelManager(str(config_path))

    assert manager.get_default_model() is None
    assert manager.get_model_info() == {
        "provider_id": "",
        "model_name": "",
        "maxContextTokens": 128000,
        "provider_name": "",
    }


def test_provider_priority_controls_dynamic_main_default(tmp_path: Path) -> None:
    config_path = tmp_path / "models_config.json"
    config_path.write_text(
        json.dumps({
            "providers": {
                "first": {"name": "First", "models": ["model-a"]},
                "second": {"name": "Second", "models": ["model-b"]},
            }
        }),
        encoding="utf-8",
    )
    manager = ModelManager(str(config_path))

    manager.move_provider_to_front("second")

    assert list(manager.get_all_providers()) == ["second", "first"]
    assert manager.get_default_model() == "second:model-b"


def test_provider_templates_expose_model_parameter_capabilities(tmp_path: Path) -> None:
    config_path = tmp_path / "models_config.json"
    config_path.write_text(json.dumps({"providers": {}}), encoding="utf-8")
    manager = ModelManager(str(config_path))

    assert set(manager.get_all_schemas()) == {
        "openai_compatible",
        "deepseek",
        "mimo",
        "qwen",
        "openai",
        "anthropic",
    }

    assert manager.get_provider_schema("openai")["default_base_url"] == (
        "https://api.openai.com/v1"
    )
    assert manager.get_provider_schema("deepseek")["default_base_url"] == (
        "https://api.deepseek.com/v1"
    )
    deepseek = manager.get_provider_capabilities("deepseek")
    assert deepseek["reasoning_efforts"] == ["low", "medium", "high", "max"]
    assert set(deepseek["model_params"]) == {
        "thinking_enabled",
        "reasoning_effort",
        "temperature",
        "top_p",
        "presence_penalty",
        "response_format",
        "stream_chunk_timeout",
    }

    alibaba = manager.get_provider_capabilities("alibaba")
    assert alibaba["reasoning_efforts"] == []
    assert "thinking_budget" in alibaba["model_params"]
    assert "reasoning_effort" not in alibaba["model_params"]

    anthropic = manager.get_provider_capabilities("anthropic")
    assert manager.get_provider_schema("anthropic")["api_format"] == "anthropic"
    assert anthropic["reasoning_efforts"] == [
        "low", "medium", "high", "xhigh", "max"
    ]
    assert set(anthropic["model_params"]) == {
        "thinking_enabled",
        "reasoning_effort",
        "thinking_budget",
    }
    assert manager.get_provider_client_base_url(
        "anthropic", "https://api.anthropic.com/v1"
    ) == "https://api.anthropic.com"


def test_multiple_provider_ids_can_share_one_provider_type(tmp_path: Path) -> None:
    config_path = tmp_path / "models_config.json"
    config_path.write_text(json.dumps({"providers": {}}), encoding="utf-8")
    manager = ModelManager(str(config_path))

    manager.add_provider(
        "deepseek-main",
        {"provider_type": "deepseek", "base_url": "https://one.test/v1"},
    )
    manager.add_provider(
        "deepseek-backup",
        {"provider_type": "ds", "base_url": "https://two.test/v1"},
    )

    assert manager.get_provider_type("deepseek-main") == "deepseek"
    assert manager.get_provider_type("deepseek-backup") == "deepseek"
    assert manager.get_all_providers()["deepseek-backup"]["provider_type"] == (
        "deepseek"
    )


def test_one_provider_resolves_model_specific_types_and_capabilities(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "models_config.json"
    config_path.write_text(
        json.dumps({
            "providers": {
                "determinflow-public": {
                    "name": "DeterminFlow 公益模型",
                    "provider_type": "openai_compatible",
                    "base_url": "https://relay.example.test/v1",
                    "models": ["deepseek-v4-flash", "qwen3.8-max"],
                    "models_config": {
                        "deepseek-v4-flash": {
                            "provider_type": "deepseek",
                        },
                        "qwen3.8-max": {
                            "provider_type": "anthropic",
                        },
                    },
                }
            }
        }),
        encoding="utf-8",
    )
    manager = ModelManager(str(config_path))

    assert manager.get_model_provider_type(
        "determinflow-public", "deepseek-v4-flash"
    ) == "deepseek"
    assert manager.get_provider_client_base_url(
        "determinflow-public",
        "https://relay.example.test/v1",
        "deepseek-v4-flash",
    ) == "https://relay.example.test/v1"
    assert "thinking_enabled" in manager.get_model_capabilities(
        "determinflow-public:deepseek-v4-flash"
    )["model_params"]
    assert manager.get_provider_client_base_url(
        "determinflow-public",
        "https://relay.example.test/v1",
        "qwen3.8-max",
    ) == "https://relay.example.test"


def test_unknown_model_provider_type_fails_closed(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "models_config.json"
    config_path.write_text(
        json.dumps({
            "providers": {
                "determinflow-public": {
                    "provider_type": "openai_compatible",
                    "base_url": "https://relay.example.test/v1",
                    "models": ["unsafe-model"],
                    "models_config": {
                        "unsafe-model": {
                            "provider_type": "unknown"
                        }
                    },
                }
            }
        }),
        encoding="utf-8",
    )
    manager = ModelManager(str(config_path))

    with pytest.raises(ValueError, match="Unsupported provider_type"):
        manager.get_model_provider_type("determinflow-public", "unsafe-model")


def test_unknown_provider_type_is_rejected_on_add_and_update(tmp_path: Path) -> None:
    config_path = tmp_path / "models_config.json"
    config_path.write_text(
        json.dumps({"providers": {"demo": {"models": []}}}),
        encoding="utf-8",
    )
    manager = ModelManager(str(config_path))

    with pytest.raises(ValueError, match="Unsupported provider_type"):
        manager.add_provider("invalid", {"provider_type": "unknown"})
    with pytest.raises(ValueError, match="Unsupported provider_type"):
        manager.update_provider("demo", {"provider_type": "unknown"})


def test_active_provider_type_filters_stale_provider_hyperparameters(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "models_config.json"
    config_path.write_text(
        json.dumps({
            "providers": {
                "claude-main": {
                    "provider_type": "anthropic",
                    "hyperparameter_values": {
                        "max_completion_tokens": 8192,
                        "frequency_penalty": 1.5,
                    },
                }
            }
        }),
        encoding="utf-8",
    )
    manager = ModelManager(str(config_path))

    request = manager.build_provider_request(
        "claude-main",
        {"thinking_enabled": False},
    )

    assert request["client_kwargs"] == {
        "max_tokens": 8192,
        "thinking": {"type": "disabled"},
    }


def test_default_agent_definitions_inherit_the_main_model() -> None:
    agents_config_path = Path(__file__).parents[1] / "config" / "agents_config.json"
    agents = json.loads(agents_config_path.read_text(encoding="utf-8"))["agents"]

    assert agents["main"]["model"] is None
    assert all(definition["model"] is None for definition in agents.values())
