import asyncio
import json

import pytest

from app.core.config import Settings
from app.db.database import init_database
from app.db.repositories import Repository
from app.llm.client import LLMResponse, MockLLMClient
from app.orchestration.orchestrator import Orchestrator
from app.schemas.workflow import RoutingDecision
from app.services.event_service import EventService
from app.services.result_service import ResultService
from app.tools.executor import ToolExecutor
from app.tools.policy import ToolPolicy
from app.tools.registry import ToolRegistry


class RecordingTool:
    description = "record arguments"

    def __init__(self, name):
        self.name = name
        self.calls = []

    async def run(self, arguments):
        self.calls.append(arguments)
        return {"tool": self.name, "arguments": arguments}


@pytest.mark.parametrize(
    ("tool_name", "arguments"),
    [
        ("weather", {"city": "Beijing"}),
        ("current_time", {"utc_offset": "+08:00"}),
        (
            "date_calculator",
            {"base_date": "2026-06-07", "operation": "add", "days": 3},
        ),
        (
            "unit_converter",
            {"value": 2, "source_unit": "km", "target_unit": "m"},
        ),
        ("calculator", {"expression": "2 + 3 * 4"}),
    ],
)
def test_react_can_select_each_functional_tool(tool_name, arguments):
    repo = Repository(init_database(":memory:"))
    events = EventService(repo)
    registry = ToolRegistry()
    tool = RecordingTool(tool_name)
    registry.register(tool)
    llm = MockLLMClient(
        [
            LLMResponse(
                content=json.dumps(
                    {
                        "action": "tool_call",
                        "tool_name": tool_name,
                        "arguments": arguments,
                        "summary": "use requested tool",
                        "answer": "",
                    }
                )
            ),
            LLMResponse(
                content=json.dumps(
                    {
                        "action": "final_answer",
                        "tool_name": "",
                        "arguments": {},
                        "summary": "enough information",
                        "answer": "Tool result is ready.",
                    }
                )
            ),
            LLMResponse(
                content=json.dumps(
                    {
                        "answer": "Tool result is ready.",
                        "citations": [],
                        "limitations": [],
                        "confidence": 0.8,
                        "used_workflow": "react",
                        "web_used": False,
                    }
                )
            ),
        ]
    )
    orchestrator = Orchestrator(
        Settings(react_max_tool_calls=5),
        repo,
        events,
        ResultService(repo),
        llm,
        ToolExecutor(registry, ToolPolicy(10), repo, events),
    )
    task = repo.create_task(f"use {tool_name}")
    repo.update_task_workflow(task["task_id"], "react", task["input"])
    routing = RoutingDecision(
        workflow="react",
        complexity="simple",
        reason="functional tool",
        requires_web=False,
        expected_sub_agents=0,
        estimated_steps=2,
    )

    asyncio.run(orchestrator._run_react(task["task_id"], routing))

    assert tool.calls == [arguments]
    assert repo.get_result(task["task_id"])["used_workflow"] == "react"


def test_react_rejects_tool_outside_allowlist():
    repo = Repository(init_database(":memory:"))
    events = EventService(repo)
    registry = ToolRegistry()
    registry.register(RecordingTool("web_fetch"))
    llm = MockLLMClient(
        [
            LLMResponse(
                content=json.dumps(
                    {
                        "action": "tool_call",
                        "tool_name": "web_fetch",
                        "arguments": {"url": "https://example.com"},
                        "summary": "unsafe selection",
                        "answer": "",
                    }
                )
            ),
            LLMResponse(
                content=json.dumps(
                    {
                        "answer": "The requested tool is not available in ReAct.",
                        "citations": [],
                        "limitations": ["Disallowed tool selection."],
                        "confidence": 0.4,
                        "used_workflow": "react",
                        "web_used": False,
                    }
                )
            ),
        ]
    )
    orchestrator = Orchestrator(
        Settings(),
        repo,
        events,
        ResultService(repo),
        llm,
        ToolExecutor(registry, ToolPolicy(10), repo, events),
    )
    task = repo.create_task("fetch a page")
    repo.update_task_workflow(task["task_id"], "react", task["input"])
    routing = RoutingDecision(
        workflow="react",
        complexity="simple",
        reason="tool",
        requires_web=False,
        expected_sub_agents=0,
        estimated_steps=1,
    )

    asyncio.run(orchestrator._run_react(task["task_id"], routing))

    assert repo.list_tool_calls(task["task_id"]) == []
    assert repo.get_task(task["task_id"])["status"] == "degraded"
