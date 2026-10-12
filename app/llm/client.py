import asyncio
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from app.core.config import Settings


@dataclass
class LLMResponse:
    content: str
    token_estimate: int = 0
    raw: dict[str, Any] | None = None


class LLMClient(Protocol):
    async def chat(self, messages: list[dict[str, str]], **kwargs: Any) -> LLMResponse: ...


class OpenAICompatibleClient:
    def __init__(self, settings: Settings):
        self.settings = settings

    async def chat(self, messages: list[dict[str, str]], **kwargs: Any) -> LLMResponse:
        payload = {
            "model": kwargs.get("model", self.settings.llm_model),
            "messages": messages,
            "temperature": kwargs.get("temperature", self.settings.llm_temperature),
            "max_tokens": kwargs.get("max_tokens", self.settings.llm_max_tokens),
        }
        headers = {"Authorization": f"Bearer {self.settings.llm_api_key}"}
        attempts = self.settings.llm_retries + 1
        response = None
        for attempt in range(attempts):
            try:
                async with httpx.AsyncClient(
                    timeout=self.settings.request_timeout_seconds
                ) as client:
                    response = await client.post(
                        f"{self.settings.llm_base_url.rstrip('/')}/v1/chat/completions",
                        json=payload,
                        headers=headers,
                    )
                    response.raise_for_status()
                break
            except (httpx.TimeoutException, httpx.RequestError) as exc:
                if attempt + 1 >= attempts:
                    raise RuntimeError(
                        f"LLM request failed after {attempts} attempts: {type(exc).__name__}"
                    ) from exc
                await asyncio.sleep(min(2**attempt, 4))
        if response is None:
            raise RuntimeError("LLM request failed without a response")
        data = response.json()
        content = data["choices"][0]["message"]["content"]
        usage = data.get("usage") or {}
        token_estimate = int(usage.get("total_tokens") or _estimate_tokens(content))
        return LLMResponse(content=content, token_estimate=token_estimate, raw=data)


class MockLLMClient:
    def __init__(self, responses: list[LLMResponse]):
        self.responses = list(responses)
        self.calls: list[list[dict[str, str]]] = []

    async def chat(self, messages: list[dict[str, str]], **kwargs: Any) -> LLMResponse:
        self.calls.append(messages)
        if not self.responses:
            raise RuntimeError("MockLLMClient has no responses left")
        return self.responses.pop(0)


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)
