from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.core import model_manager as model_manager_module
from src.core.model_manager import ModelManager
from src.web.api_routes import router


class _ExtensionManager:
    def __init__(self, enabled: bool = True) -> None:
        self.enabled = enabled

    def is_enabled(self, owner: str) -> bool:
        return self.enabled and owner == "public-api"


def _client(
    tmp_path: Path,
    monkeypatch,
    *,
    providers: dict | None = None,
    enabled: bool = True,
) -> tuple[TestClient, ModelManager]:
    config_path = tmp_path / "models_config.json"
    config_path.write_text(
        json.dumps({"providers": providers or {}}),
        encoding="utf-8",
    )
    manager = ModelManager(str(config_path))
    monkeypatch.setattr(model_manager_module, "_model_manager", manager)
    app = FastAPI()
    app.state.extension_manager = _ExtensionManager(enabled)
    app.include_router(router)
    return TestClient(app), manager


def _managed_provider() -> dict:
    return {
        "name": "DeterminFlow 公益模型",
        "provider_type": "openai_compatible",
        "base_url": "https://relay.example.test/v1",
        "api_key": "public-key",
        "models": ["public-model"],
        "models_config": {
            "public-model": {"provider_type": "openai_compatible"},
        },
        "hyperparameter_values": {},
        "error_messages": {
            "quota_exhausted": "公益模型额度已用完，请稍后再试",
            "rate_limited": "公益模型请求过于频繁，请稍后再试",
        },
        "managed_by": "public-api",
    }


def test_enabled_plugin_provider_is_read_only_for_regular_settings_requests(
    tmp_path: Path,
    monkeypatch,
) -> None:
    client, manager = _client(
        tmp_path,
        monkeypatch,
        providers={"determinflow-public": _managed_provider()},
    )

    listed = client.get("/api/model-providers")
    assert listed.status_code == 200
    assert listed.json()["providers"]["determinflow-public"]["is_managed"] is True

    assert client.put(
        "/api/model-providers/determinflow-public",
        json={"name": "Changed"},
    ).status_code == 403
    assert client.delete(
        "/api/model-providers/determinflow-public"
    ).status_code == 403
    assert client.put(
        "/api/model-providers/determinflow-public/priority"
    ).status_code == 403
    assert manager.get_provider("determinflow-public")["name"] == (
        "DeterminFlow 公益模型"
    )


def test_provider_owner_can_update_and_remove_its_managed_provider(
    tmp_path: Path,
    monkeypatch,
) -> None:
    client, manager = _client(
        tmp_path,
        monkeypatch,
        providers={"determinflow-public": _managed_provider()},
    )
    headers = {"X-DeterminFlow-Provider-Owner": "public-api"}

    updated = client.put(
        "/api/model-providers/determinflow-public",
        headers=headers,
        json={"name": "Renewed"},
    )
    assert updated.status_code == 200
    assert manager.get_provider("determinflow-public")["name"] == "Renewed"

    messages_updated = client.put(
        "/api/model-providers/determinflow-public",
        headers=headers,
        json={"error_messages": {"unknown": "公益模型调用失败，请稍后再试"}},
    )
    assert messages_updated.status_code == 200
    assert manager.get_provider("determinflow-public")["error_messages"] == {
        "unknown": "公益模型调用失败，请稍后再试",
    }

    removed = client.delete(
        "/api/model-providers/determinflow-public",
        headers=headers,
    )
    assert removed.status_code == 200
    assert manager.get_provider("determinflow-public") is None


def test_only_provider_owner_can_create_a_managed_provider(
    tmp_path: Path,
    monkeypatch,
) -> None:
    client, manager = _client(tmp_path, monkeypatch)
    payload = {
        "provider_id": "determinflow-public",
        **_managed_provider(),
    }

    assert client.post("/api/model-providers", json=payload).status_code == 403
    created = client.post(
        "/api/model-providers",
        headers={"X-DeterminFlow-Provider-Owner": "public-api"},
        json=payload,
    )
    assert created.status_code == 200
    assert manager.get_provider("determinflow-public")["managed_by"] == "public-api"
    assert manager.get_provider("determinflow-public")["error_messages"][
        "quota_exhausted"
    ] == "公益模型额度已用完，请稍后再试"


def test_managed_provider_becomes_user_mutable_when_plugin_is_disabled(
    tmp_path: Path,
    monkeypatch,
) -> None:
    client, manager = _client(
        tmp_path,
        monkeypatch,
        providers={"determinflow-public": _managed_provider()},
        enabled=False,
    )

    listed = client.get("/api/model-providers")
    assert listed.json()["providers"]["determinflow-public"]["is_managed"] is False
    removed = client.delete("/api/model-providers/determinflow-public")
    assert removed.status_code == 200
    assert manager.get_provider("determinflow-public") is None
