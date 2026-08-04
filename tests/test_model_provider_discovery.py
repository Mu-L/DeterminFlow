from __future__ import annotations

import asyncio

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
            provider_id="openai",
            base_url="https://models.example.test/v1/",
            api_key="test-key",
        )
    ))

    assert result == {"models": ["model-b", "model-a"]}
    assert captured["url"] == "https://models.example.test/v1/models"
    assert captured["headers"] == {"Authorization": "Bearer test-key"}
    assert captured["options"]["follow_redirects"] is False
