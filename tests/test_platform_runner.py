import asyncio
import json
from datetime import datetime, timezone
from types import SimpleNamespace


from app.application.platform_runner import PlatformTaskRunner
from app.core.config import Settings
from app.core.enums import TaskStatus
from app.domain.models import ScheduledTask, TaskSnapshot
from app.llm.client import LLMResponse, MockLLMClient


class Tasks:
    def __init__(self, snapshot):
        self.snapshot = snapshot
        self.updated = None

    async def get_task(self, _context, task_id):
        return self.snapshot if task_id == self.snapshot.task_id else None

    async def update_execution(self, _context, task_id, **values):
        self.updated = (task_id, values)
        return self.snapshot


class Execution:
    def __init__(self):
        self.created = []
        self.statuses = []

    async def create_run(self, _context, run):
        self.created.append(run)
        return run

    async def update_run_status(self, _context, run_id, status, error_summary=""):
        self.statuses.append((run_id, status.value, error_summary))


class Facts:
    def __init__(self):
        self.events = []
        self.results = []
        self.agents = []
        self.tools = []
        self.evidence = []

    async def create_agent_run(self, _context, value):
        self.agents.append(value)

    async def append_event(self, _context, **value):
        self.events.append(value)

    async def save_tool_call(self, _context, value):
        self.tools.append(value)

    async def save_evidence(self, _context, value):
        self.evidence.append(value)

    async def save_result(self, _context, value):
        self.results.append(value)


class Tools:
    async def list_enabled(self, _context):
        return []


class Checkpoints:
    def __init__(self):
        self.saved = []

    async def latest_for_task(self, _context, _task_id):
        return self.saved[-1] if self.saved else None

    async def save(self, _context, checkpoint):
        self.saved.append(checkpoint)


class Documents:
    async def list_task_blocks(self, _context, _task_id):
        return []


class Assets:
    def __init__(self):
        self.artifacts = []

    async def save_artifact(self, _context, value):
        self.artifacts.append(value)


def test_platform_runner_executes_legacy_orchestrator_and_mirrors_result(
    tmp_path,
):
    now = datetime.now(timezone.utc)
    task = TaskSnapshot(
        task_id="task_platform",
        input_text="Summarize this request.",
        status=TaskStatus.RUNNING,
        created_at=now,
        updated_at=now,
        workspace_id="workspace_1",
        owner_user_id="user_1",
    )
    tasks = Tasks(task)
    execution = Execution()
    facts = Facts()
    assets = Assets()
    checkpoints = Checkpoints()
    repositories = SimpleNamespace(
        tasks=tasks,
        execution=execution,
        facts=facts,
        assets=assets,
        documents=Documents(),
        tools=Tools(),
        checkpoints=checkpoints,
    )
    llm = MockLLMClient(
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
                        "answer": "Done.",
                        "citations": [],
                        "limitations": [],
                        "confidence": 0.9,
                        "used_workflow": "direct",
                        "web_used": False,
                    }
                )
            ),
        ]
    )
    runner = PlatformTaskRunner(
        settings=Settings(artifact_root=str(tmp_path)),
        repositories=repositories,
        llm=llm,
    )

    asyncio.run(
        runner(
            ScheduledTask(
                task_id=task.task_id,
                attempt=1,
                workspace_id="workspace_1",
                owner_user_id="user_1",
            )
        )
    )

    assert execution.created[0].task_id == task.task_id
    assert [item[1] for item in execution.statuses] == [
        "running",
        "completed",
    ]
    assert tasks.updated[1]["status"] == TaskStatus.COMPLETED
    assert tasks.updated[1]["workflow"].value == "direct"
    assert facts.results[0].answer == "Done."
    assert any(event["event_type"] == "task_completed" for event in facts.events)
    assert checkpoints.saved
    assert checkpoints.saved[-1].kind == "terminal"
    assert "database_dump" in checkpoints.saved[-1].payload

    retry_llm = MockLLMClient([])
    retry_runner = PlatformTaskRunner(
        settings=Settings(artifact_root=str(tmp_path)),
        repositories=repositories,
        llm=retry_llm,
    )
    asyncio.run(
        retry_runner(
            ScheduledTask(
                task_id=task.task_id,
                attempt=2,
                workspace_id="workspace_1",
                owner_user_id="user_1",
            )
        )
    )
    assert retry_llm.calls == []
    assert any(event["event_type"] == "task_recovered" for event in facts.events)
