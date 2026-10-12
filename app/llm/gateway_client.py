from __future__ import annotations

from typing import Any

import httpx

from app.core.config import Settings
from app.llm.client import LLMResponse


class GatewayLLMClient:
    def __init__(
        self,
        settings: Settings,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.settings = settings
        self._client = client

    async def chat(self, messages: list[dict[str, str]], **kwargs: Any) -> LLMResponse:
        payload: dict[str, Any] = {
            "messages": messages,
            "purpose": kwargs.get("purpose", "task"),
        }
        variant = kwargs.get("model_variant_id", self.settings.model_gateway_variant_id)
        if variant:
            payload["model_variant_id"] = variant
        if kwargs.get("temperature") is not None:
            payload["temperature"] = kwargs["temperature"]
        if kwargs.get("max_tokens") is not None:
            payload["max_tokens"] = kwargs["max_tokens"]
        if kwargs.get("task_id"):
            payload["task_id"] = kwargs["task_id"]
        if kwargs.get("agent_id"):
            payload["agent_id"] = kwargs["agent_id"]

        workspace_id = kwargs.get(
            "workspace_id", self.settings.model_gateway_workspace_id
        )
        if not workspace_id:
            raise RuntimeError("model gateway workspace is required")
        headers = {
            "X-Internal-Token": self.settings.model_gateway_internal_token,
            "X-Workspace-ID": workspace_id,
        }
        if self._client is not None:
            response = await self._client.post(
                "/internal/v1/model/invoke",
                json=payload,
                headers=headers,
            )
        else:
            async with httpx.AsyncClient(
                base_url=self.settings.model_gateway_url.rstrip("/"),
                timeout=self.settings.request_timeout_seconds,
            ) as client:
                response = await client.post(
                    "/internal/v1/model/invoke",
                    json=payload,
                    headers=headers,
                )
        if response.status_code >= 400:
            detail = response.json().get("detail", "model gateway failed")
            raise RuntimeError(str(detail))
        data = response.json()
        token_estimate = int(data.get("request_tokens", 0)) + int(data.get("response_tokens", 0))
        return LLMResponse(
            content=str(data["content"]),
            token_estimate=token_estimate,
            raw=data,
        )
