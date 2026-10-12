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


class CapturingLLM:
    def __init__(self, inner):
        self.inner = inner
        self.responses = []

    async def chat(self, messages, **kwargs):
        response = await self.inner.chat(messages, **kwargs)
        self.responses.append({"messages": messages, "content": response.content})
        return response


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
    llm = CapturingLLM(OpenAICompatibleClient(settings))
    orchestrator = Orchestrator(
        settings=settings,
        repository=repository,
        event_service=events,
        result_service=results,
        llm=llm,
        tool_executor=tools,
    )
    task_input = (
        sys.argv[1]
        if len(sys.argv) > 1
        else "请联网搜索 Shannon 多智能体编排框架，概括它的核心设计，并给出2个来源引用。"
    )
    task = repository.create_task(task_input)
    try:
        await orchestrator.run_task(task["task_id"])
    except Exception as exc:
        print("orchestrator_error", type(exc).__name__, str(exc))
        for index, response in enumerate(llm.responses[-5:], start=max(1, len(llm.responses) - 4)):
            system = response["messages"][0]["content"][:80] if response["messages"] else ""
            print("llm_response_index", index)
            print("system", system.replace("\n", " "))
            print("content", repr(response["content"][:1200]))
        raise
    loaded = repository.get_task(task["task_id"])
    result = repository.get_result(task["task_id"]) or {}
    evidence = repository.list_evidence(task["task_id"])
    event_types = [event["type"] for event in repository.list_events(task["task_id"])]
    print("status", loaded["status"] if loaded else None)
    print("workflow", loaded["workflow"] if loaded else None)
    print("agent_spawned", event_types.count("agent_spawned"))
    print("tool_completed", event_types.count("tool_call_completed"))
    print("evidence_count", len(evidence))
    print("citation_count", len(result.get("citations", [])))
    print("answer", result.get("answer", "")[:500])
    for citation in result.get("citations", [])[:5]:
        print("citation", citation.get("title", "")[:100], citation.get("url", "")[:160])


if __name__ == "__main__":
    asyncio.run(main())
