import asyncio

from app.core.config import Settings
from app.db.database import init_database
from app.db.repositories import Repository
from app.llm.client import LLMResponse, MockLLMClient
from app.orchestration.json_repair import parse_structured_output
from app.schemas.workflow import RoutingDecision
from app.services.event_service import EventService
from app.tools.builtin.tavily_search import TavilySearchTool
from app.tools.builtin.web_fetch import is_public_http_url
from app.tools.executor import ToolExecutor
from app.tools.policy import ToolPolicy
from app.tools.registry import ToolRegistry


def test_json_recovery_repairs_malformed_json():
    llm = MockLLMClient(
        responses=[
            LLMResponse(content='{"workflow": "swarm"', token_estimate=3),
            LLMResponse(
                content="""{
                    "workflow": "swarm",
                    "complexity": "complex",
                    "reason": "needs comparison",
                    "requires_web": true,
                    "expected_sub_agents": 3,
                    "estimated_steps": 4,
                    "risk_flags": [],
                    "constraints": {
                        "max_agents": 4,
                        "max_swarm_rounds": 2,
                        "max_concurrent_agents": 2
                    }
                }""",
                token_estimate=30,
            ),
        ]
    )

    parsed = asyncio.run(
        parse_structured_output(
            llm=llm,
            schema=RoutingDecision,
            messages=[{"role": "user", "content": "route"}],
            repair_prompt="repair",
        )
    )

    assert parsed.workflow == "swarm"
    assert len(llm.calls) == 2


def test_json_recovery_extracts_json_from_markdown_without_repair():
    content = """```json
{
  "workflow": "direct",
  "complexity": "simple",
  "reason": "definition",
  "requires_web": false,
  "expected_sub_agents": 0,
  "estimated_steps": 1,
  "risk_flags": [],
  "constraints": {
    "max_agents": 4,
    "max_swarm_rounds": 2,
    "max_concurrent_agents": 2
  }
}
```"""
    llm = MockLLMClient([LLMResponse(content=content, token_estimate=10)])

    parsed = asyncio.run(
        parse_structured_output(
            llm=llm,
            schema=RoutingDecision,
            messages=[{"role": "user", "content": "route"}],
            repair_prompt="repair",
        )
    )

    assert parsed.workflow == "direct"
    assert len(llm.calls) == 1


def test_public_url_validation_rejects_private_hosts():
    assert is_public_http_url("https://example.com/page")
    assert not is_public_http_url("http://localhost:8000")
    assert not is_public_http_url("http://127.0.0.1/page")
    assert not is_public_http_url("file:///tmp/a")


def test_tool_executor_runs_mock_tavily_and_persists_evidence():
    connection = init_database(":memory:")
    repo = Repository(connection)
    task = repo.create_task("Research Shannon")
    events = EventService(repo)
    registry = ToolRegistry()
    registry.register(
        TavilySearchTool(
            settings=Settings(tavily_api_key="test"),
            search_func=lambda query, max_results: {
                "results": [
                    {
                        "title": "Shannon",
                        "url": "https://example.com/shannon",
                        "content": "Multi-agent framework",
                    }
                ]
            },
        )
    )
    executor = ToolExecutor(
        registry=registry,
        policy=ToolPolicy(max_tool_calls=3),
        repository=repo,
        event_service=events,
    )

    result = asyncio.run(
        executor.execute(
            task_id=task["task_id"],
            agent_id=None,
            tool_name="web_search",
            arguments={"query": "Shannon framework", "max_results": 1},
            allowed_tools=["web_search"],
        )
    )

    evidence = repo.list_evidence(task["task_id"])

    assert result["results"][0]["title"] == "Shannon"
    assert evidence[0]["url"] == "https://example.com/shannon"
    assert repo.list_events(task["task_id"])[-1]["type"] == "tool_call_completed"

    cached = asyncio.run(
        executor.execute(
            task_id=task["task_id"],
            agent_id=None,
            tool_name="web_search",
            arguments={"max_results": 1, "query": "Shannon framework"},
            allowed_tools=["web_search"],
        )
    )
    assert cached == result
    assert len(repo.list_tool_calls(task["task_id"])) == 1
    assert repo.list_events(task["task_id"])[-1]["payload"]["reused"] is True
