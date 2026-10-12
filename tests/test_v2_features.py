import asyncio
import json

from fastapi.testclient import TestClient

from app.core.config import Settings
from app.core.enums import EventType, TaskStatus, WorkflowType
from app.db.database import init_database
from app.db.repositories import Repository
from app.llm.client import LLMResponse, MockLLMClient
from app.orchestration.orchestrator import Orchestrator
from app.orchestration.routing import route_by_rules
from app.schemas.workflow import FinalSynthesis
from app.services.event_service import EventService
from app.services.result_service import ResultService
from app.tools.builtin.current_time import CurrentTimeTool
from app.tools.builtin.research_tools import CitationCheckerTool, QueryPlannerTool
from app.tools.builtin.weather import WeatherTool
from app.tools.executor import ToolExecutor
from app.tools.policy import ToolPolicy
from app.tools.registry import ToolRegistry


def test_v2_settings_load_weather_and_react_defaults(monkeypatch):
    monkeypatch.setenv("WEATHER_API_KEY", "weather-key")
    monkeypatch.setenv("WEATHER_BASE_URL", "https://weather.example")

    settings = Settings.from_environment()

    assert settings.weather_api_key == "weather-key"
    assert settings.weather_base_url == "https://weather.example"
    assert settings.weather_provider == "weatherapi"
    assert settings.react_max_tool_calls == 5


def test_v2_enums_include_new_workflows_and_cancelled():
    assert WorkflowType.REACT.value == "react"
    assert WorkflowType.RESEARCH.value == "research"
    assert WorkflowType.SUPERVISOR.value == "supervisor"
    assert WorkflowType.DAG.value == "dag"
    assert TaskStatus.CANCELLED.value == "cancelled"
    assert EventType.TASK_CANCELLED.value == "task_cancelled"


def test_rule_router_selects_react_and_research():
    weather = route_by_rules("What is the weather in Beijing and local time?")
    research = route_by_rules("Research Tesla company with citations.")
    supervisor = route_by_rules("Analyze framework trends, risks, and use cases.")
    dag = route_by_rules("First collect facts, then assess risks, finally report.")

    assert weather.workflow == WorkflowType.REACT
    assert research.workflow == WorkflowType.RESEARCH
    assert supervisor.workflow == WorkflowType.SUPERVISOR
    assert dag.workflow == WorkflowType.DAG


def test_weather_tool_normalizes_weatherapi_response():
    tool = WeatherTool(
        Settings(weather_api_key="test"),
        weather_func=lambda city: {
            "location": {
                "name": city,
                "country": "China",
                "localtime": "2026-06-05 12:00",
            },
            "current": {
                "temp_c": 25,
                "condition": {"text": "Sunny"},
                "humidity": 40,
                "wind_kph": 8,
            },
        },
    )

    result = asyncio.run(tool.run({"city": "Beijing"}))

    assert result["city"] == "Beijing"
    assert result["temperature_c"] == 25
    assert result["condition"] == "Sunny"


def test_current_time_tool_accepts_utc_offset():
    result = asyncio.run(CurrentTimeTool().run({"utc_offset": "+08:00"}))

    assert result["timezone"] == "UTC+08:00"
    assert "iso_time" in result


def test_query_planner_and_citation_checker_are_deterministic():
    planner = QueryPlannerTool()
    planned = asyncio.run(planner.run({"query": "Research Shannon and LangGraph"}))
    checker = CitationCheckerTool(
        evidence_loader=lambda task_id: [
            {
                "evidence_id": "evidence_1",
                "title": "Source",
                "url": "https://example.com",
            }
        ]
    )
    checked = asyncio.run(
        checker.run(
            {
                "task_id": "task_1",
                "citations": [
                    {
                        "title": "Source",
                        "url": "https://example.com",
                        "evidence_id": "evidence_1",
                    }
                ],
            }
        )
    )

    assert planned["queries"]
    assert checked["valid"] is True


def test_cancel_api_sets_status_and_event():
    app = __import__("app.main", fromlist=["create_app"]).create_app(testing=True)
    client = TestClient(app)
    task_id = app.state.repository.create_task("waiting task")["task_id"]

    response = client.post(f"/tasks/{task_id}/cancel")

    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"
    assert app.state.repository.get_task(task_id)["status"] == "cancelled"
    assert app.state.repository.list_events(task_id)[-1]["type"] == "task_cancelled"


def test_cancelled_task_cannot_be_completed_after_in_flight_work_returns():
    orchestrator, repo = _orchestrator([], ToolRegistry())
    task = repo.create_task("cancel while running")
    repo.update_task_status(task["task_id"], TaskStatus.CANCELLED)
    synthesis = FinalSynthesis(
        answer="late result",
        citations=[],
        limitations=[],
        confidence=0.8,
        used_workflow=WorkflowType.DIRECT,
        web_used=False,
    )

    orchestrator._complete(task["task_id"], synthesis, degraded=False)

    assert repo.get_task(task["task_id"])["status"] == "cancelled"
    assert repo.get_result(task["task_id"]) is None


def test_empty_search_results_are_not_reported_as_web_evidence():
    class EmptySearchTool:
        name = "web_search"
        description = "empty search"

        async def run(self, arguments):
            return {"results": []}

    registry = ToolRegistry()
    registry.register(EmptySearchTool())
    orchestrator, repo = _orchestrator([], registry)
    task = repo.create_task("Research an unavailable topic")

    context, web_used = asyncio.run(
        orchestrator._research_context(task["task_id"], None)
    )

    assert web_used is False
    assert context == ""


def _orchestrator(responses: list[dict], registry: ToolRegistry) -> tuple[Orchestrator, Repository]:
    connection = init_database(":memory:")
    repo = Repository(connection)
    event_service = EventService(repo)
    executor = ToolExecutor(
        registry=registry,
        policy=ToolPolicy(max_tool_calls=10),
        repository=repo,
        event_service=event_service,
    )
    llm = MockLLMClient([LLMResponse(content=json.dumps(item)) for item in responses])
    return (
        Orchestrator(
            settings=Settings(tavily_api_key="test", react_max_tool_calls=5),
            repository=repo,
            event_service=event_service,
            result_service=ResultService(repo),
            llm=llm,
            tool_executor=executor,
        ),
        repo,
    )


def test_react_workflow_uses_weather_and_time_tools():
    registry = ToolRegistry()
    registry.register(
        WeatherTool(
            Settings(weather_api_key="test"),
            weather_func=lambda city: {
                "location": {"name": city, "country": "China", "localtime": "2026-06-05 12:00"},
                "current": {
                    "temp_c": 25,
                    "condition": {"text": "Sunny"},
                    "humidity": 40,
                    "wind_kph": 8,
                },
            },
        )
    )
    registry.register(CurrentTimeTool())
    orchestrator, repo = _orchestrator(
        [
            {
                "workflow": "react",
                "complexity": "simple",
                "reason": "tool use",
                "requires_web": False,
                "expected_sub_agents": 0,
                "estimated_steps": 2,
                "risk_flags": [],
                "constraints": {},
            },
            {
                "answer": "Beijing is sunny and the local time is available.",
                "citations": [],
                "limitations": [],
                "confidence": 0.8,
                "used_workflow": "react",
                "web_used": False,
            },
        ],
        registry,
    )
    task = repo.create_task("weather in Beijing and current time")

    asyncio.run(orchestrator.run_task(task["task_id"]))

    events = repo.list_events(task["task_id"])
    assert repo.get_task(task["task_id"])["workflow"] == "react"
    assert [event["type"] for event in events].count("tool_call_completed") == 2


def test_research_workflow_completes_with_persisted_evidence():
    class SearchTool:
        name = "web_search"
        description = "mock search"

        async def run(self, arguments):
            return {
                "results": [
                    {
                        "title": "Tesla source",
                        "url": "https://example.com/tesla",
                        "snippet": "Tesla business overview",
                        "summary": "Tesla business overview",
                        "source": "mock",
                        "rank": 1,
                        "source_type": "search_result",
                    }
                ]
            }

    registry = ToolRegistry()
    registry.register(SearchTool())
    registry.register(QueryPlannerTool())
    registry.register(CitationCheckerTool(evidence_loader=lambda task_id: []))
    orchestrator, repo = _orchestrator(
        [
            {
                "workflow": "research",
                "complexity": "medium",
                "reason": "needs sources",
                "requires_web": True,
                "expected_sub_agents": 0,
                "estimated_steps": 3,
                "risk_flags": [],
                "constraints": {},
            },
            {
                "answer": "Tesla is an EV and energy company.",
                "citations": [{"title": "Tesla source", "url": "https://example.com/tesla", "evidence_id": None}],
                "limitations": [],
                "confidence": 0.8,
                "used_workflow": "research",
                "web_used": True,
            },
        ],
        registry,
    )
    task = repo.create_task("Research Tesla company with citations")

    asyncio.run(orchestrator.run_task(task["task_id"]))

    assert repo.get_task(task["task_id"])["status"] == "completed"
    assert repo.list_evidence(task["task_id"])
    assert repo.get_result(task["task_id"])["used_workflow"] == "research"
