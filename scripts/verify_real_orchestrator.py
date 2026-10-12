import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import Settings
from app.db.database import init_database
from app.db.repositories import Repository
from app.llm.client import OpenAICompatibleClient
from app.orchestration.orchestrator import Orchestrator
from app.services.event_service import EventService
from app.services.result_service import ResultService
from app.tools.builtin.tavily_search import TavilySearchTool
from app.tools.builtin.web_fetch import WebFetchTool
from app.tools.executor import ToolExecutor
from app.tools.policy import ToolPolicy
from app.tools.registry import ToolRegistry


async def main() -> None:
    settings = Settings.from_env_file()
    connection = init_database(":memory:")
    repository = Repository(connection)
    events = EventService(repository)
    results = ResultService(repository)
    registry = ToolRegistry()
    if settings.tavily_api_key:
        registry.register(TavilySearchTool(settings))
        registry.register(WebFetchTool(settings))
    tools = ToolExecutor(
        registry=registry,
        policy=ToolPolicy(max_tool_calls=settings.max_tool_calls),
        repository=repository,
        event_service=events,
    )
    orchestrator = Orchestrator(
        settings=settings,
        repository=repository,
        event_service=events,
        result_service=results,
        llm=OpenAICompatibleClient(settings),
        tool_executor=tools,
    )
    task = repository.create_task("请用两句话解释什么是多智能体动态编排框架。")
    await orchestrator.run_task(task["task_id"])
    loaded = repository.get_task(task["task_id"])
    result = repository.get_result(task["task_id"])
    print("status", loaded["status"] if loaded else None)
    print("workflow", loaded["workflow"] if loaded else None)
    print("answer", (result or {}).get("answer", "")[:300])


if __name__ == "__main__":
    asyncio.run(main())
