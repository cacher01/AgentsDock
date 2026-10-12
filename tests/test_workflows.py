import asyncio
import json

from app.core.config import Settings
from app.core.enums import TaskStatus
from app.db.database import init_database
from app.db.repositories import Repository
from app.llm.client import LLMResponse, MockLLMClient
from app.orchestration.orchestrator import Orchestrator
from app.orchestration.orchestrator import candidate_search_queries
from app.services.event_service import EventService
from app.services.result_service import ResultService
from app.tools.executor import ToolExecutor
from app.tools.policy import ToolPolicy
from app.tools.registry import ToolRegistry
from app.tools.builtin.tavily_search import TavilySearchTool


def _orchestrator(responses: list[dict | str]) -> tuple[Orchestrator, Repository]:
    connection = init_database(":memory:")
    repo = Repository(connection)
    event_service = EventService(repo)
    registry = ToolRegistry()
    executor = ToolExecutor(
        registry=registry,
        policy=ToolPolicy(max_tool_calls=5),
        repository=repo,
        event_service=event_service,
    )
    llm = MockLLMClient(
        [
            LLMResponse(
                content=response if isinstance(response, str) else json.dumps(response),
                token_estimate=10,
            )
            for response in responses
        ]
    )
    orchestrator = Orchestrator(
        settings=Settings(),
        repository=repo,
        event_service=event_service,
        result_service=ResultService(repo),
        llm=llm,
        tool_executor=executor,
    )
    return orchestrator, repo


def _orchestrator_with_search(
    responses: list[dict | str],
) -> tuple[Orchestrator, Repository]:
    connection = init_database(":memory:")
    repo = Repository(connection)
    event_service = EventService(repo)
    registry = ToolRegistry()
    registry.register(
        TavilySearchTool(
            settings=Settings(tavily_api_key="test"),
            search_func=lambda query, max_results: {
                "results": [
                    {
                        "title": "Source",
                        "url": "https://example.com/source",
                        "content": "Useful source",
                    }
                ]
            },
        )
    )
    executor = ToolExecutor(
        registry=registry,
        policy=ToolPolicy(max_tool_calls=5),
        repository=repo,
        event_service=event_service,
    )
    llm = MockLLMClient(
        [
            LLMResponse(
                content=response if isinstance(response, str) else json.dumps(response),
                token_estimate=10,
            )
            for response in responses
        ]
    )
    orchestrator = Orchestrator(
        settings=Settings(tavily_api_key="test"),
        repository=repo,
        event_service=event_service,
        result_service=ResultService(repo),
        llm=llm,
        tool_executor=executor,
    )
    return orchestrator, repo


def test_direct_workflow_completes_without_sub_agents():
    orchestrator, repo = _orchestrator(
        [
            {
                "workflow": "direct",
                "complexity": "simple",
                "reason": "definition",
                "requires_web": False,
                "expected_sub_agents": 0,
                "estimated_steps": 1,
                "risk_flags": [],
                "constraints": {
                    "max_agents": 4,
                    "max_swarm_rounds": 2,
                    "max_concurrent_agents": 2,
                },
            },
            {
                "answer": "A multi-agent orchestration framework coordinates agents.",
                "citations": [],
                "limitations": [],
                "confidence": 0.8,
                "used_workflow": "direct",
                "web_used": False,
            },
        ]
    )
    task = repo.create_task("Explain multi-agent orchestration")

    asyncio.run(orchestrator.run_task(task["task_id"]))
    loaded = repo.get_task(task["task_id"])
    result = repo.get_result(task["task_id"])

    assert loaded["status"] == TaskStatus.COMPLETED.value
    assert loaded["workflow"] == "direct"
    assert result["answer"].startswith("A multi-agent")


def test_direct_final_synthesis_receives_original_user_input():
    orchestrator, repo = _orchestrator(
        [
            {
                "workflow": "direct",
                "complexity": "simple",
                "reason": "definition",
                "requires_web": False,
                "expected_sub_agents": 0,
                "estimated_steps": 1,
                "risk_flags": [],
                "constraints": {
                    "max_agents": 4,
                    "max_swarm_rounds": 2,
                    "max_concurrent_agents": 2,
                },
            },
            {
                "answer": "A multi-agent orchestration framework coordinates agents.",
                "citations": [],
                "limitations": [],
                "confidence": 0.8,
                "used_workflow": "direct",
                "web_used": False,
            },
        ]
    )
    task = repo.create_task("Explain multi-agent orchestration")

    asyncio.run(orchestrator.run_task(task["task_id"]))
    final_call = orchestrator.llm.calls[-1]

    assert "Explain multi-agent orchestration" in final_call[-1]["content"]


def test_swarm_workflow_spawns_dynamic_agents_and_finishes():
    orchestrator, repo = _orchestrator(
        [
            {
                "workflow": "swarm",
                "complexity": "complex",
                "reason": "multi-framework comparison",
                "requires_web": False,
                "expected_sub_agents": 2,
                "estimated_steps": 4,
                "risk_flags": [],
                "constraints": {
                    "max_agents": 4,
                    "max_swarm_rounds": 2,
                    "max_concurrent_agents": 2,
                },
            },
            {
                "objective": "Compare frameworks",
                "plan_summary": "Split research and analysis",
                "subtasks": [
                    {
                        "subtask_id": "subtask_1",
                        "title": "Research Shannon",
                        "description": "Summarize Shannon",
                        "expected_output": "Notes",
                        "requires_web": False,
                        "priority": 1,
                        "depends_on": [],
                    },
                    {
                        "subtask_id": "subtask_2",
                        "title": "Research LangGraph",
                        "description": "Summarize LangGraph",
                        "expected_output": "Notes",
                        "requires_web": False,
                        "priority": 2,
                        "depends_on": [],
                    },
                ],
                "success_criteria": ["comparison produced"],
            },
            {
                "round": 1,
                "agents": [
                    {
                        "agent_name": "research_shannon",
                        "template": "research",
                        "assigned_subtasks": ["subtask_1"],
                        "goal": "Research Shannon",
                        "context_brief": "Focus on orchestration",
                        "allowed_tools": [],
                        "expected_output": "Notes",
                        "stop_condition": "summary complete",
                    },
                    {
                        "agent_name": "research_langgraph",
                        "template": "research",
                        "assigned_subtasks": ["subtask_2"],
                        "goal": "Research LangGraph",
                        "context_brief": "Focus on orchestration",
                        "allowed_tools": [],
                        "expected_output": "Notes",
                        "stop_condition": "summary complete",
                    },
                ],
            },
            {
                "status": "completed",
                "summary": "Shannon notes",
                "findings": ["dynamic agents"],
                "evidence": [],
                "open_questions": [],
                "confidence": 0.8,
                "recommended_next_action": "combine",
            },
            {
                "status": "completed",
                "summary": "LangGraph notes",
                "findings": ["graph orchestration"],
                "evidence": [],
                "open_questions": [],
                "confidence": 0.8,
                "recommended_next_action": "combine",
            },
            {
                "round": 1,
                "status": "sufficient",
                "summary": "Enough information",
                "completed_subtasks": ["subtask_1", "subtask_2"],
                "incomplete_subtasks": [],
                "knowledge_gaps": [],
                "needs_supplemental_search": False,
                "next_action": "finish",
                "next_round_focus": "",
            },
            {
                "answer": "Shannon and LangGraph differ in orchestration style.",
                "citations": [],
                "limitations": [],
                "confidence": 0.8,
                "used_workflow": "swarm",
                "web_used": False,
            },
        ]
    )
    task = repo.create_task("Use swarm to compare Shannon and LangGraph")

    asyncio.run(orchestrator.run_task(task["task_id"]))
    events = repo.list_events(task["task_id"])
    result = repo.get_result(task["task_id"])

    assert result["used_workflow"] == "swarm"
    assert [event["type"] for event in events].count("agent_spawned") == 2
    assert repo.get_task(task["task_id"])["status"] == "completed"


def test_plan_execute_uses_web_search_when_required():
    orchestrator, repo = _orchestrator_with_search(
        [
            {
                "workflow": "plan_execute",
                "complexity": "medium",
                "reason": "needs search",
                "requires_web": True,
                "expected_sub_agents": 0,
                "estimated_steps": 2,
                "risk_flags": [],
                "constraints": {
                    "max_agents": 4,
                    "max_swarm_rounds": 2,
                    "max_concurrent_agents": 2,
                },
            },
            {
                "objective": "Research with source",
                "plan_summary": "Search and answer",
                "subtasks": [],
                "success_criteria": ["citation included"],
            },
            {
                "answer": "Answer with source.",
                "citations": [
                    {
                        "title": "Source",
                        "url": "https://example.com/source",
                        "evidence_id": "evidence_mock",
                    }
                ],
                "limitations": [],
                "confidence": 0.8,
                "used_workflow": "plan_execute",
                "web_used": True,
            },
        ]
    )
    task = repo.create_task("Research something")

    asyncio.run(orchestrator.run_task(task["task_id"]))
    events = repo.list_events(task["task_id"])
    evidence = repo.list_evidence(task["task_id"])

    assert any(event["type"] == "tool_call_completed" for event in events)
    assert evidence[0]["source"] == "tavily"


def test_web_synthesis_allows_missing_evidence_id_and_replaces_from_evidence():
    orchestrator, repo = _orchestrator_with_search(
        [
            {
                "workflow": "direct",
                "complexity": "simple",
                "reason": "search summary",
                "requires_web": True,
                "expected_sub_agents": 0,
                "estimated_steps": 1,
                "risk_flags": [],
                "constraints": {
                    "max_agents": 4,
                    "max_swarm_rounds": 2,
                    "max_concurrent_agents": 2,
                },
            },
            {
                "answer": "Answer with citation.",
                "citations": [
                    {
                        "title": "Source",
                        "url": "https://example.com/source",
                        "evidence_id": None,
                    }
                ],
                "limitations": [],
                "confidence": 0.8,
                "used_workflow": "direct",
                "web_used": True,
            },
        ]
    )
    task = repo.create_task("Research something")

    asyncio.run(orchestrator.run_task(task["task_id"]))
    evidence = repo.list_evidence(task["task_id"])
    result = repo.get_result(task["task_id"])

    assert result["citations"][0]["evidence_id"] == evidence[0]["evidence_id"]


def test_candidate_search_queries_adds_english_entity_query():
    queries = candidate_search_queries("请联网搜索 Shannon 多智能体编排框架")

    assert queries[0] == "Shannon multi-agent orchestration framework"
    assert queries[1] == "请联网搜索 Shannon 多智能体编排框架"
