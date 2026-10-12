from __future__ import annotations

import asyncio

import httpx
import pytest

from app.core.config import Settings
from app.llm.gateway_client import GatewayLLMClient


def test_gateway_llm_client_preserves_governance_metadata() -> None:
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["headers"] = request.headers
        captured["body"] = request.content
        return httpx.Response(
            200,
            json={
                "content": "fallback answer",
                "model_variant_id": "model_backup",
                "model_name": "backup",
                "provider_type": "openai_compatible",
                "request_tokens": 10,
                "response_tokens": 5,
                "cost": 0.001,
                "latency_ms": 20,
                "fallback_from": "model_primary",
                "warnings": ["primary model failed"],
            },
        )

    settings = Settings(
        model_gateway_enabled=True,
        model_gateway_internal_token="internal-secret",
        model_gateway_workspace_id="workspace_1",
        model_gateway_variant_id="model_primary",
    )
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://gateway",
    )

    async def scenario():
        try:
            gateway = GatewayLLMClient(settings, client=client)
            response = await gateway.chat(
                [{"role": "user", "content": "hello"}],
                task_id="task_1",
            )
            assert response.content == "fallback answer"
            assert response.token_estimate == 15
            assert response.raw["fallback_from"] == "model_primary"
        finally:
            await client.aclose()

    asyncio.run(scenario())
    assert captured["headers"]["x-internal-token"] == "internal-secret"
    assert captured["headers"]["x-workspace-id"] == "workspace_1"
    assert b'"task_id":"task_1"' in captured["body"]


def test_gateway_configuration_requires_trusted_context() -> None:
    with pytest.raises(ValueError, match="model gateway"):
        Settings(model_gateway_enabled=True)
