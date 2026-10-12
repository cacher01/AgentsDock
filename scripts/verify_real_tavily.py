import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import Settings
from app.tools.builtin.tavily_search import TavilySearchTool


async def main() -> None:
    settings = Settings.from_env_file()
    print("tavily_key_loaded", bool(settings.tavily_api_key))
    tool = TavilySearchTool(settings)
    result = await tool.run(
        {"query": "Shannon multi-agent orchestration framework", "max_results": 3}
    )
    print("result_count", len(result.get("results", [])))
    for item in result.get("results", [])[:3]:
        print("title", item.get("title", "")[:100])
        print("url", item.get("url", "")[:160])


if __name__ == "__main__":
    asyncio.run(main())
