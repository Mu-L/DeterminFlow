from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from src.web import api_routes


def test_model_discovery_reads_openai_compatible_data_and_deduplicates(monkeypatch):
    captured = {}

    class FakeResponse:
        status_code = 200

        @staticmethod
        def json():
            return {
                "data": [
                    {"id": "model-b"},
                    {"id": "model-a"},
                    {"id": "model-b"},
                    {"missing": "id"},
                ]
            }

    class FakeAsyncClient:
        def __init__(self, **options):
            captured["options"] = options

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, url, *, headers):
            captured["url"] = url
            captured["headers"] = headers
            return FakeResponse()

    monkeypatch.setattr(api_routes.httpx, "AsyncClient", FakeAsyncClient)

    result = asyncio.run(api_routes.discover_provider_models(
        api_routes.DiscoverProviderModelsRequest(
            provider_id="custom-openai",
            provider_type="openai_compatible",
            base_url="https://models.example.test/v1/",
            api_key="test-key",
        )
    ))

    assert result == {"models": ["model-b", "model-a"]}
    assert captured["url"] == "https://models.example.test/v1/models"
    assert captured["headers"] == {"Authorization": "Bearer test-key"}
    assert captured["options"]["follow_redirects"] is False


def test_model_discovery_uses_anthropic_headers(monkeypatch):
    captured = {}

    class FakeResponse:
        status_code = 200

        @staticmethod
        def json():
            return {"data": [{"id": "claude-sonnet-4-6"}]}

    class FakeAsyncClient:
        def __init__(self, **_options):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, url, *, headers):
            captured["url"] = url
            captured["headers"] = headers
            return FakeResponse()

    monkeypatch.setattr(api_routes.httpx, "AsyncClient", FakeAsyncClient)

    result = asyncio.run(api_routes.discover_provider_models(
        api_routes.DiscoverProviderModelsRequest(
            provider_id="claude-main",
            provider_type="anthropic",
            base_url="https://api.anthropic.com/v1/",
            api_key="test-key",
        )
    ))

    assert result == {"models": ["claude-sonnet-4-6"]}
    assert captured["url"] == "https://api.anthropic.com/v1/models"
    assert captured["headers"] == {
        "anthropic-version": "2023-06-01",
        "x-api-key": "test-key",
    }


def test_model_discovery_rejects_unknown_provider_type():
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(api_routes.discover_provider_models(
            api_routes.DiscoverProviderModelsRequest(
                provider_id="custom-provider",
                provider_type="unknown",
                base_url="https://models.example.test/v1",
            )
        ))

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "Unsupported provider_type: unknown"


def test_add_provider_request_rejects_invalid_instance_id():
    with pytest.raises(ValidationError):
        api_routes.AddModelProviderRequest(
            provider_id="bad provider id",
            provider_type="deepseek",
            name="Bad ID",
            base_url="https://api.deepseek.com/v1",
        )
