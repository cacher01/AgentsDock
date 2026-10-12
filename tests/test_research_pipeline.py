import asyncio

from app.core.config import Settings
from app.core.enums import EventType, WorkflowType
from app.db.database import init_database
from app.db.repositories import Repository
from app.llm.client import MockLLMClient
from app.orchestration.orchestrator import Orchestrator
from app.schemas.workflow import Citation, FinalSynthesis
from app.services.event_service import EventService
from app.services.result_service import ResultService
from app.tools.builtin.research_tools import CitationCheckerTool
from app.tools.executor import ToolExecutor
from app.tools.policy import ToolPolicy
from app.tools.registry import ToolRegistry


class QueryPlanner:
    name = "query_planner"
    description = "test planner"

    async def run(self, arguments):
        return {"queries": ["alpha research", "beta research", "gamma research"]}


class SearchTool:
    name = "web_search"
    description = "test search"

    def __init__(self):
        self.queries = []

    async def run(self, arguments):
        query = arguments["query"]
        self.queries.append(query)
        suffix = "shared" if query != "gamma research" else "gamma"
        return {
            "results": [
                {
                    "title": f"{query} source",
                    "url": f"https://example.com/{suffix}/",
                    "snippet": f"evidence for {query}",
                    "summary": f"evidence for {query}",
                    "source": "mock",
                    "rank": 1,
                    "source_type": "search_result",
                }
            ]
        }


class FetchTool:
    name = "web_fetch"
    description = "test fetch"

    def __init__(self):
        self.urls = []

    async def run(self, arguments):
        self.urls.append(arguments["url"])
        return {
            "url": arguments["url"],
            "text": "full fetched page",
            "summary": "full fetched page",
        }


def _orchestrator():
    repo = Repository(init_database(":memory:"))
    events = EventService(repo)
    registry = ToolRegistry()
    search = SearchTool()
    fetch = FetchTool()
    registry.register(QueryPlanner())
    registry.register(search)
    registry.register(fetch)
    executor = ToolExecutor(
        registry,
        ToolPolicy(max_tool_calls=12),
        repo,
        events,
    )
    orchestrator = Orchestrator(
        Settings(search_results_limit=5, fetch_top_results=2),
        repo,
        events,
        ResultService(repo),
        MockLLMClient([]),
        executor,
    )
    return orchestrator, repo, search, fetch


def test_research_runs_multiple_queries_deduplicates_and_fetches_top_pages():
    orchestrator, repo, search, fetch = _orchestrator()
    task = repo.create_task("research topic")

    context, web_used = asyncio.run(
        orchestrator._research_context(task["task_id"], None)
    )

    assert web_used is True
    assert search.queries[:2] == ["alpha research", "beta research"]
    assert len(search.queries) >= 2
    assert len(fetch.urls) <= 2
    assert "full fetched page" in context
    evidence = repo.list_evidence(task["task_id"])
    assert {item["source_type"] for item in evidence} == {
        "search_result",
        "fetched_page",
    }


def test_citation_checker_is_executed_and_emits_event():
    orchestrator, repo, _, _ = _orchestrator()
    orchestrator.tool_executor.registry.register(
        CitationCheckerTool(evidence_loader=repo.list_evidence)
    )
    task = repo.create_task("research topic")
    evidence = repo.save_evidence(
        task_id=task["task_id"],
        title="Source",
        url="https://example.com/source",
        snippet="evidence",
        source="mock",
        rank=1,
        source_type="search_result",
        summary="evidence",
    )
    synthesis = FinalSynthesis(
        answer="Evidence-backed answer.",
        citations=[
            Citation(
                title=evidence["title"],
                url=evidence["url"],
                evidence_id=evidence["evidence_id"],
            )
        ],
        limitations=[],
        confidence=0.8,
        used_workflow=WorkflowType.RESEARCH,
        web_used=True,
    )

    valid = asyncio.run(
        orchestrator._run_citation_check(task["task_id"], synthesis)
    )

    assert valid is True
    assert repo.list_events(task["task_id"])[-1]["type"] == (
        EventType.CITATION_CHECK_COMPLETED.value
    )
