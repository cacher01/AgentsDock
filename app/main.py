import json
from pathlib import Path

from fastapi import FastAPI

from app.api import register_routes
from app.api.platform_routes import register_platform_routes
from app.core.config import Settings
from app.db.database import init_database
from app.db.repositories import Repository
from app.llm.client import LLMResponse, MockLLMClient, OpenAICompatibleClient
from app.llm.gateway_client import GatewayLLMClient
from app.orchestration.orchestrator import Orchestrator
from app.persistence.provider import create_platform_repository_provider
from app.services.event_service import EventService
from app.services.artifact_service import ArtifactService
from app.services.quota_service import QuotaService
from app.services.result_service import ResultService
from app.services.task_service import TaskService
from app.tools.builtin.tavily_search import TavilySearchTool
from app.tools.builtin.web_fetch import WebFetchTool
from app.tools.builtin.calculator import CalculatorTool
from app.tools.builtin.current_time import CurrentTimeTool
from app.tools.builtin.date_calculator import DateCalculatorTool
from app.tools.builtin.research_tools import (
    CitationCheckerTool,
    QueryPlannerTool,
    ResultCriticTool,
)
from app.tools.builtin.unit_converter import UnitConverterTool
from app.tools.builtin.weather import WeatherTool
from app.tools.builtin.artifacts import ArtifactArchiverTool, ArtifactWriterTool
from app.tools.executor import ToolExecutor
from app.tools.policy import ToolPolicy
from app.tools.registry import ToolRegistry


def create_app(testing: bool = False) -> FastAPI:
    settings = Settings(database_url="sqlite:///:memory:") if testing else Settings.from_env_file()
    db_path = ":memory:" if testing else _sqlite_path(settings.database_url)
    connection = init_database(db_path)
    repository = Repository(connection)
    platform_repositories = create_platform_repository_provider(settings)
    event_service = EventService(repository)
    result_service = ResultService(repository)
    artifact_service = ArtifactService(settings, repository)
    quota_service = QuotaService(repository, model=settings.llm_model)
    registry = ToolRegistry()
    registry.register(CurrentTimeTool())
    registry.register(CalculatorTool())
    registry.register(DateCalculatorTool())
    registry.register(UnitConverterTool())
    registry.register(QueryPlannerTool())
    registry.register(CitationCheckerTool(evidence_loader=repository.list_evidence))
    registry.register(ResultCriticTool())
    registry.register(ArtifactWriterTool(artifact_service))
    registry.register(ArtifactArchiverTool(artifact_service))
    if settings.weather_api_key:
        registry.register(WeatherTool(settings))
    if settings.tavily_api_key:
        registry.register(TavilySearchTool(settings))
        registry.register(WebFetchTool(settings))
    tool_executor = ToolExecutor(
        registry=registry,
        policy=ToolPolicy(max_tool_calls=settings.max_tool_calls),
        repository=repository,
        event_service=event_service,
    )
    llm = _testing_llm() if testing else _runtime_llm(settings)
    orchestrator = Orchestrator(
        settings=settings,
        repository=repository,
        event_service=event_service,
        result_service=result_service,
        llm=llm,
        tool_executor=tool_executor,
    )
    task_service = TaskService(
        repository=repository,
        event_service=event_service,
        orchestrator=orchestrator,
        run_inline=testing,
    )

    app = FastAPI(title="EZcode")
    app.state.repository = repository
    app.state.platform_repositories = platform_repositories
    app.router.add_event_handler("shutdown", platform_repositories.close)
    app.state.event_service = event_service
    app.state.result_service = result_service
    app.state.task_service = task_service
    app.state.artifact_service = artifact_service
    app.state.orchestrator = orchestrator
    app.state.quota_service = quota_service

    static_dir = Path(__file__).parent / "static"
    register_routes(
        app,
        repository=repository,
        event_service=event_service,
        result_service=result_service,
        task_service=task_service,
        artifact_service=artifact_service,
        quota_service=quota_service,
        settings=settings,
        static_dir=static_dir,
    )
    register_platform_routes(
        app,
        provider=platform_repositories,
        settings=settings,
    )

    return app


def _sqlite_path(database_url: str) -> str:
    if database_url.startswith("sqlite:///"):
        return database_url.replace("sqlite:///", "", 1)
    return "orchestration.db"


def _runtime_llm(settings: Settings):
    if settings.model_gateway_enabled:
        return GatewayLLMClient(settings)
    return OpenAICompatibleClient(settings)


def _testing_llm() -> MockLLMClient:
    responses = []
    for _ in range(10):
        responses.extend(
            [
                LLMResponse(
                    content=json.dumps(
                        {
                            "workflow": "direct",
                            "complexity": "simple",
                            "reason": "test",
                            "requires_web": False,
                            "expected_sub_agents": 0,
                            "estimated_steps": 1,
                            "risk_flags": [],
                            "constraints": {
                                "max_agents": 4,
                                "max_swarm_rounds": 2,
                                "max_concurrent_agents": 2,
                            },
                        }
                    )
                ),
                LLMResponse(
                    content=json.dumps(
                        {
                            "answer": "A multi-agent orchestration framework coordinates task execution across agents.",
                            "citations": [],
                            "limitations": [],
                            "confidence": 0.8,
                            "used_workflow": "direct",
                            "web_used": False,
                        }
                    )
                ),
            ]
        )
    return MockLLMClient(responses)


app = create_app()
