from collections.abc import Callable
from typing import Any

import httpx

from app.core.config import Settings


class TavilySearchTool:
    name = "web_search"
    description = "Search the web using Tavily Search API."

    def __init__(
        self,
        settings: Settings,
        search_func: Callable[[str, int], dict[str, Any]] | None = None,
    ) -> None:
        self.settings = settings
        self.search_func = search_func

    async def run(self, arguments: dict[str, Any]) -> dict[str, Any]:
        query = str(arguments["query"])
        max_results = int(arguments.get("max_results") or self.settings.search_results_limit)
        if self.search_func is not None:
            data = self.search_func(query, max_results)
        else:
            data = await self._call_tavily(query, max_results)
        return {"results": self._normalize(data)}

    async def _call_tavily(self, query: str, max_results: int) -> dict[str, Any]:
        if not self.settings.tavily_api_key:
            raise RuntimeError("Tavily API key is not configured")
        async with httpx.AsyncClient(timeout=self.settings.request_timeout_seconds) as client:
            response = await client.post(
                "https://api.tavily.com/search",
                headers={"Authorization": f"Bearer {self.settings.tavily_api_key}"},
                json={"query": query, "max_results": max_results},
            )
            response.raise_for_status()
            return response.json()

    def _normalize(self, data: dict[str, Any]) -> list[dict[str, Any]]:
        items = data.get("results", [])
        normalized = []
        for index, item in enumerate(items, start=1):
            snippet = item.get("content") or item.get("snippet") or ""
            normalized.append(
                {
                    "title": item.get("title", ""),
                    "url": item.get("url", ""),
                    "snippet": snippet,
                    "summary": snippet,
                    "source": "tavily",
                    "rank": index,
                    "source_type": "search_result",
                }
            )
        return normalized
