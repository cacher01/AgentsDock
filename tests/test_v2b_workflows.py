import asyncio
import json

from app.core.config import Settings
from app.core.enums import EventType
from app.db.database import init_database
from app.db.repositories import Repository
from app.llm.client import LLMResponse, MockLLMClient
from app.orchestration.orchestrator import Orchestrator
from app.services.event_service import EventService
from app.services.result_service import ResultService
from app.tools.executor import ToolExecutor
from app.tools.policy import ToolPolicy
from app.tools.registry import ToolRegistry


def _orchestrator(responses: list[dict]) -> tuple[Orchestrator, Repository, EventService]:
    connection = init_database(":memory:")
    repo = Repository(connection)
    event_service = EventService(repo)
    executor = ToolExecutor(
        registry=ToolRegistry(),
        policy=ToolPolicy(max_tool_calls=10),
        repository=repo,
        event_service=event_service,
    )
    llm = MockLLMClient([LLMResponse(content=json.dumps(item)) for item in responses])
    orchestrator = Orchestrator(
        settings=Settings(max_concurrent_agents=2),
        repository=repo,
        event_service=event_service,
        result_service=ResultService(repo),
        llm=llm,
        tool_executor=executor,
    )
    return orchestrator, repo, event_service


def test_graph_event_helpers_emit_node_and_edge_events():
    connection = init_database(":memory:")
    repo = Repository(connection)
    event_service = EventService(repo)
    task = repo.create_task("graph task")

    event_service.create_graph_node(
        task["task_id"],
        node_id="workflow_1",
        node_type="workflow",
        label="Supervisor",
        status="running",
    )
    event_service.create_graph_edge(
        task["task_id"],
        edge_id="edge_1",
        source="workflow_1",
        target="agent_1",
        edge_type="spawned",
    )
    event_service.update_graph_node(
        task["task_id"],
        node_id="workflow_1",
        node_type="workflow",
        label="Supervisor",
        status="completed",
    )

    events = repo.list_events(task["task_id"])
    assert [event["type"] for event in events] == [
        "graph_node_created",
        "graph_edge_created",
        "graph_node_updated",
    ]
    assert events[0]["payload"]["node_id"] == "workflow_1"


def test_supervisor_workflow_spawns_workers_and_completes():
    orchestrator, repo, _ = _orchestrator(
        [
            {
                "workflow": "supervisor",
                "complexity": "complex",
                "reason": "broad analysis",
                "requires_web": False,
                "expected_sub_agents": 2,
                "estimated_steps": 4,
                "risk_flags": [],
                "constraints": {},
            },
            {
                "objective": "Analyze framework trends",
                "plan_summary": "Split trend and risk analysis",
                "subtasks": [
                    {
                        "subtask_id": "subtask_1",
                        "title": "Trend analysis",
                        "description": "Analyze trends",
                        "expected_output": "trend notes",
                        "requires_web": False,
                        "priority": 1,
                        "depends_on": [],
                    },
                    {
                        "subtask_id": "subtask_2",
                        "title": "Risk analysis",
                        "description": "Analyze risks",
                        "expected_output": "risk notes",
                        "requires_web": False,
                        "priority": 2,
                        "depends_on": [],
                    },
                ],
                "success_criteria": ["final analysis"],
            },
            {
                "status": "completed",
                "summary": "Trend notes",
                "findings": ["more dynamic workflows"],
                "evidence": [],
                "open_questions": [],
                "confidence": 0.8,
                "recommended_next_action": "combine",
            },
            {
                "status": "completed",
                "summary": "Risk notes",
                "findings": ["tool safety matters"],
                "evidence": [],
                "open_questions": [],
                "confidence": 0.8,
                "recommended_next_action": "combine",
            },
            {
                "answer": "Multi-agent frameworks are moving toward dynamic workflows.",
                "citations": [],
                "limitations": [],
                "confidence": 0.8,
                "used_workflow": "supervisor",
                "web_used": False,
            },
        ]
    )
    task = repo.create_task("Analyze multi-agent framework trends, risks, and use cases")

    asyncio.run(orchestrator.run_task(task["task_id"]))

    events = repo.list_events(task["task_id"])
    assert repo.get_task(task["task_id"])["workflow"] == "supervisor"
    assert [event["type"] for event in events].count("agent_spawned") == 2
    assert any(event["type"] == "graph_node_created" for event in events)
    assert repo.get_result(task["task_id"])["used_workflow"] == "supervisor"
    assert {agent["status"] for agent in repo.list_agents(task["task_id"])} == {
        "completed"
    }


def test_dag_workflow_executes_dependency_layers():
    orchestrator, repo, _ = _orchestrator(
        [
            {
                "workflow": "dag",
                "complexity": "complex",
                "reason": "dependent subtasks",
                "requires_web": False,
                "expected_sub_agents": 2,
                "estimated_steps": 4,
                "risk_flags": [],
                "constraints": {},
            },
            {
                "objective": "Research then analyze",
                "plan_summary": "First collect facts, then assess risks",
                "subtasks": [
                    {
                        "subtask_id": "facts",
                        "title": "Collect facts",
                        "description": "Collect company facts",
                        "expected_output": "facts",
                        "requires_web": False,
                        "priority": 1,
                        "depends_on": [],
                    },
                    {
                        "subtask_id": "risks",
                        "title": "Assess risks",
                        "description": "Assess risks from facts",
                        "expected_output": "risk report",
                        "requires_web": False,
                        "priority": 2,
                        "depends_on": ["facts"],
                    },
                ],
                "success_criteria": ["risk report"],
            },
            {
                "status": "completed",
                "summary": "Facts done",
                "findings": ["company facts"],
                "evidence": [],
                "open_questions": [],
                "confidence": 0.8,
                "recommended_next_action": "run dependent task",
            },
            {
                "status": "completed",
                "summary": "Risks done",
                "findings": ["competitive risk"],
                "evidence": [],
                "open_questions": [],
                "confidence": 0.8,
                "recommended_next_action": "finalize",
            },
            {
                "answer": "The risk report is based on collected facts.",
                "citations": [],
                "limitations": [],
                "confidence": 0.8,
                "used_workflow": "dag",
                "web_used": False,
            },
        ]
    )
    task = repo.create_task("First collect facts, then assess risks")

    asyncio.run(orchestrator.run_task(task["task_id"]))

    events = repo.list_events(task["task_id"])
    completed_nodes = [
        event["payload"]["node_id"]
        for event in events
        if event["type"] == EventType.GRAPH_NODE_UPDATED.value
        and event["payload"]["status"] == "completed"
    ]
    assert completed_nodes.index("subtask_facts") < completed_nodes.index("subtask_risks")
    assert any(event["type"] == "graph_edge_created" for event in events)
    assert repo.get_result(task["task_id"])["used_workflow"] == "dag"
    dependent_call = orchestrator.llm.calls[3][-1]["content"]
    assert "Facts done" in dependent_call
