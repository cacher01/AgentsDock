import asyncio
from types import SimpleNamespace

import pytest

from app.core.enums import WorkflowType
from app.core.errors import TaskCancelledError
from app.schemas.workflow import (
    FinalSynthesis,
    RoundEvaluation,
    RoutingDecision,
    SpawnAgentSpec,
    SpawnPlan,
    SubAgentOutput,
    SubTask,
    TaskDecomposition,
)
from app.workflows.base import WorkflowContext
from app.workflows.dag import DagWorkflowStrategy
from app.workflows.supervisor import SupervisorWorkflowStrategy
from app.workflows.swarm import SwarmWorkflowStrategy


def _routing(workflow: WorkflowType):
    return RoutingDecision(
        workflow=workflow,
        complexity="complex",
        reason="complex strategy contract",
        requires_web=False,
        expected_sub_agents=2,
        estimated_steps=4,
    )


def _subtask(subtask_id: str, *, depends_on=()):
    return SubTask(
        subtask_id=subtask_id,
        title=f"Task {subtask_id}",
        description=f"Execute {subtask_id}",
        expected_output="analysis",
        requires_web=False,
        priority=1,
        depends_on=list(depends_on),
    )


def _output(status="completed"):
    return SubAgentOutput(
        status=status,
        summary=f"{status} output",
        findings=["finding"] if status == "completed" else [],
        evidence=[],
        open_questions=[],
        confidence=0.8 if status == "completed" else 0.0,
        recommended_next_action="continue",
    )


class EventRecorder:
    def __init__(self):
        self.items = []

    def emit(self, *args):
        self.items.append(("emit", args))

    def create_graph_node(self, *args, **kwargs):
        self.items.append(("create_node", args, kwargs))

    def create_graph_edge(self, *args, **kwargs):
        self.items.append(("create_edge", args, kwargs))

    def update_graph_node(self, *args, **kwargs):
        self.items.append(("update_node", args, kwargs))


class FakeRepository:
    def __init__(self):
        self.created_agents = []
        self.updated_agents = []

    def create_agent(self, **kwargs):
        agent = {"agent_id": f"agent_{len(self.created_agents) + 1}", **kwargs}
        self.created_agents.append(agent)
        return agent

    def update_agent_status(self, agent_id, status):
        self.updated_agents.append((agent_id, status))


class FakeAgentRuntime:
    def __init__(self, outputs=(), *, error=None):
        self.outputs = list(outputs)
        self.error = error
        self.calls = []

    async def run_sub_agent(self, spec):
        self.calls.append(spec)
        if self.error is not None:
            raise self.error
        return self.outputs.pop(0)


class ComplexRuntime:
    def __init__(self, decomposition, outputs=(), *, agent_error=None):
        self.settings = SimpleNamespace(
            max_concurrent_agents=2,
            max_agents=1,
            max_swarm_rounds=2,
        )
        self.repository = FakeRepository()
        self.event_service = EventRecorder()
        self.agent_runtime = FakeAgentRuntime(outputs, error=agent_error)
        self.decomposition = decomposition
        self.finished = None
        self.executed = []
        self.checkpoints = 0
        self.cancel_on_checkpoint = False
        self.spawn_plans = []
        self.evaluations = []

    def checkpoint(self, task_id):
        self.checkpoints += 1
        if self.cancel_on_checkpoint:
            raise TaskCancelledError("cancelled")

    def task_context(self, task_id):
        return "user task"

    async def decompose(self, task_id):
        return self.decomposition

    async def research_subtask(self, task_id, agent_id, subtask, requires_web):
        return "", False

    async def final_synthesis(self, task_id, workflow, web_used, context):
        return FinalSynthesis(
            answer="done",
            citations=[],
            limitations=[],
            confidence=0.8,
            used_workflow=workflow,
            web_used=web_used,
        )

    def multi_agent_fallback_synthesis(self, task_id, user_input, outputs, error):
        return FinalSynthesis(
            answer="fallback",
            citations=[],
            limitations=[str(error)],
            confidence=0.5,
            used_workflow=WorkflowType.SUPERVISOR,
            web_used=False,
        )

    async def execute_workflow(self, workflow, context):
        self.executed.append((workflow, context))

    async def finish(self, task_id, synthesis, degraded):
        self.finished = (task_id, synthesis, degraded)

    async def spawn_plan(self, round_number):
        self.spawn_plans.append(round_number)
        return SpawnPlan(
            round=round_number,
            agents=[
                SpawnAgentSpec(
                    agent_name=f"agent_{round_number}_a",
                    template="analysis",
                    assigned_subtasks=["a"],
                    goal="analyze",
                    context_brief="context",
                    allowed_tools=[],
                    expected_output="analysis",
                    stop_condition="done",
                ),
                SpawnAgentSpec(
                    agent_name=f"agent_{round_number}_b",
                    template="analysis",
                    assigned_subtasks=["b"],
                    goal="review",
                    context_brief="context",
                    allowed_tools=[],
                    expected_output="review",
                    stop_condition="done",
                ),
            ],
        )

    async def round_evaluation(self, round_number):
        action = self.evaluations[round_number - 1]
        return RoundEvaluation(
            round=round_number,
            status="sufficient" if action == "finish" else "needs_more_work",
            summary="evaluation",
            completed_subtasks=[],
            incomplete_subtasks=[],
            knowledge_gaps=[],
            needs_supplemental_search=False,
            next_action=action,
            next_round_focus="",
        )

    async def maybe_research(self, task_id, agent_id, requires_web):
        return "", False


def test_dag_cycle_falls_back_to_plan_execute():
    decomposition = TaskDecomposition(
        objective="cycle",
        plan_summary="cyclic plan",
        subtasks=[
            _subtask("a", depends_on=["b"]),
            _subtask("b", depends_on=["a"]),
        ],
        success_criteria=[],
    )
    runtime = ComplexRuntime(decomposition)
    context = WorkflowContext("task_cycle", _routing(WorkflowType.DAG))

    asyncio.run(DagWorkflowStrategy().execute(context, runtime))

    assert runtime.executed[0][0] == WorkflowType.PLAN_EXECUTE
    assert context.routing.workflow == WorkflowType.PLAN_EXECUTE
    assert runtime.finished is None


def test_dag_failed_node_blocks_dependents_and_degrades_result():
    decomposition = TaskDecomposition(
        objective="failure",
        plan_summary="dependent plan",
        subtasks=[_subtask("a"), _subtask("b", depends_on=["a"])],
        success_criteria=[],
    )
    runtime = ComplexRuntime(decomposition, outputs=[_output("failed")])
    context = WorkflowContext("task_failure", _routing(WorkflowType.DAG))

    asyncio.run(DagWorkflowStrategy().execute(context, runtime))

    updates = [
        item[1]
        for item in runtime.event_service.items
        if item[0] == "update_node"
    ]
    assert any(args[1] == "subtask_b" and args[4] == "blocked" for args in updates)
    assert len(runtime.agent_runtime.calls) == 1
    assert runtime.finished[2] is True


def test_swarm_respects_agent_and_round_limits():
    decomposition = TaskDecomposition(
        objective="swarm",
        plan_summary="two rounds",
        subtasks=[_subtask("a"), _subtask("b")],
        success_criteria=[],
    )
    runtime = ComplexRuntime(
        decomposition,
        outputs=[_output(), _output()],
    )
    runtime.evaluations = ["run_next_round", "finish"]
    context = WorkflowContext("task_swarm", _routing(WorkflowType.SWARM))

    asyncio.run(SwarmWorkflowStrategy().execute(context, runtime))

    assert runtime.spawn_plans == [1, 2]
    assert len(runtime.repository.created_agents) == 2
    assert len(runtime.agent_runtime.calls) == 2
    assert runtime.finished[2] is False


def test_supervisor_worker_error_degrades_but_still_finishes():
    decomposition = TaskDecomposition(
        objective="supervise",
        plan_summary="delegate",
        subtasks=[_subtask("a")],
        success_criteria=[],
    )
    runtime = ComplexRuntime(
        decomposition,
        agent_error=ValueError("invalid worker output"),
    )
    context = WorkflowContext("task_supervisor", _routing(WorkflowType.SUPERVISOR))

    asyncio.run(SupervisorWorkflowStrategy().execute(context, runtime))

    assert runtime.finished is not None
    assert runtime.finished[2] is True
    assert runtime.repository.updated_agents


@pytest.mark.parametrize(
    "strategy,workflow",
    [
        (SupervisorWorkflowStrategy(), WorkflowType.SUPERVISOR),
        (DagWorkflowStrategy(), WorkflowType.DAG),
        (SwarmWorkflowStrategy(), WorkflowType.SWARM),
    ],
)
def test_complex_strategies_honor_cancellation_checkpoint(strategy, workflow):
    decomposition = TaskDecomposition(
        objective="cancel",
        plan_summary="cancel",
        subtasks=[],
        success_criteria=[],
    )
    runtime = ComplexRuntime(decomposition)
    runtime.cancel_on_checkpoint = True
    context = WorkflowContext("task_cancel", _routing(workflow))

    with pytest.raises(TaskCancelledError):
        asyncio.run(strategy.execute(context, runtime))

    assert runtime.finished is None
    assert runtime.repository.created_agents == []