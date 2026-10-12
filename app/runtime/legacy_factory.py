from __future__ import annotations

from collections.abc import Callable

from app.core.config import Settings
from app.db.repositories import Repository
from app.llm.client import LLMClient, OpenAICompatibleClient
from app.llm.gateway_client import GatewayLLMClient
from app.orchestration.orchestrator import Orchestrator
from app.services.artifact_service import ArtifactService
from app.services.event_service import EventService
from app.services.result_service import ResultService
from app.tools.builtin.artifacts import ArtifactArchiverTool, ArtifactWriterTool
from app.tools.builtin.calculator import CalculatorTool
from app.tools.builtin.current_time import CurrentTimeTool
from app.tools.builtin.date_calculator import DateCalculatorTool
from app.tools.builtin.research_tools import (
    CitationCheckerTool,
    QueryPlannerTool,
    ResultCriticTool,
)
from app.tools.builtin.tavily_search import TavilySearchTool
from app.tools.builtin.unit_converter import UnitConverterTool
from app.tools.builtin.weather import WeatherTool
from app.tools.builtin.web_fetch import WebFetchTool
from app.tools.executor import ToolExecutor
from app.tools.policy import ToolPolicy
from app.tools.registry import ToolRegistry


def runtime_llm(settings: Settings) -> LLMClient:
    if settings.model_gateway_enabled:
        return GatewayLLMClient(settings)
    return OpenAICompatibleClient(settings)


def build_legacy_orchestrator(
    settings: Settings,
    repository: Repository,
    *,
    llm: LLMClient | None = None,
    allowed_tools: set[str] | None = None,
    checkpoint_callback: Callable[[str], None] | None = None,
) -> Orchestrator:
    event_service = EventService(repository)
    result_service = ResultService(repository)
    artifact_service = ArtifactService(settings, repository)
    registry = ToolRegistry()

    def register(tool) -> None:
        if allowed_tools is None or tool.name in allowed_tools:
            registry.register(tool)

    register(CurrentTimeTool())
    register(CalculatorTool())
    register(DateCalculatorTool())
    register(UnitConverterTool())
    register(QueryPlannerTool())
    register(CitationCheckerTool(evidence_loader=repository.list_evidence))
    register(ResultCriticTool())
    register(ArtifactWriterTool(artifact_service))
    register(ArtifactArchiverTool(artifact_service))
    if settings.weather_api_key:
        register(WeatherTool(settings))
    if settings.tavily_api_key:
        register(TavilySearchTool(settings))
        register(WebFetchTool(settings))

    tool_executor = ToolExecutor(
        registry=registry,
        policy=ToolPolicy(max_tool_calls=settings.max_tool_calls),
        repository=repository,
        event_service=event_service,
    )
    return Orchestrator(
        settings=settings,
        repository=repository,
        event_service=event_service,
        result_service=result_service,
        llm=llm or runtime_llm(settings),
        tool_executor=tool_executor,
        checkpoint_callback=checkpoint_callback,
    )
