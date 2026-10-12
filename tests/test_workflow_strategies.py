import asyncio
from types import SimpleNamespace

from app.core.enums import WorkflowType
from app.llm.client import LLMResponse
from app.schemas.workflow import (
    Citation,
    FinalSynthesis,
    RoutingDecision,
    TaskDecomposition,
)
from app.workflows.base import WorkflowContext
from app.workflows.direct import DirectWorkflowStrategy
from app.workflows.plan_execute import PlanExecuteWorkflowStrategy
from app.workflows.react import ReactWorkflowStrategy
from app.workflows.research import ResearchWorkflowStrategy


def _routing(workflow: WorkflowType, *, requires_web: bool = False):
    return RoutingDecision(
        workflow=workflow,
        complexity="simple",
        reason="strategy contract",
        requires_web=requires_web,
        expected_sub_agents=0,
        estimated_steps=1,
    )


def _synthesis(workflow: WorkflowType, *, web_used: bool = False):
    citations = (
        [Citation(title="source", url="https://example.com", evidence_id="ev_1")]
        if web_used
        else []
    )
    return FinalSynthesis(
        answer="done",
        citations=citations,
        limitations=[],
        confidence=0.8,
        used_workflow=workflow,
        web_used=web_used,
    )


class _Registry:
    def __init__(self, names=()):
        self._names = set(names)

    def list_names(self):
        return sorted(self._names)


class _ToolExecutor:
    def __init__(self, names=()):
        self.registry = _Registry(names)
        self.calls = []

    async def execute(self, *args):
        self.calls.append(args)
        return {"value": "ok"}


class _LLM:
    def __init__(self, responses=()):
        self.responses = list(responses)

    async def chat(self, messages):
        return LLMResponse(content=self.responses.pop(0), token_estimate=1)


class FakeRuntime:
    def __init__(self, *, llm_responses=(), tools=()):
        self.settings = SimpleNamespace(
            react_max_tool_calls=2,
            research_supplemental_searches=1,
        )
        self.llm = _LLM(llm_responses)
        self.tool_executor = _ToolExecutor(tools)
        self.calls = []
        self.finished = None
        self.research_result = ("evidence", True)
        self.citation_results = [True]
        self.critic_result = True
        self.supplemental_result = False

    def checkpoint(self, task_id):
        self.calls.append(("checkpoint", task_id))

    def task_context(self, task_id):
        self.calls.append(("task_context", task_id))
        return "user task"

    async def decompose(self, task_id):
        self.calls.append(("decompose", task_id))
        return TaskDecomposition(
            objective="user task",
            plan_summary="first analyze, then answer",
            subtasks=[],
            success_criteria=[],
        )
    async def maybe_research(self, task_id, agent_id, requires_web):
        self.calls.append(("maybe_research", requires_web))
        return ("research context", requires_web)

    async def research_context(self, task_id, agent_id):
        self.calls.append(("research_context", task_id))
        return self.research_result

    async def react_fallback(self, task_id, user_input, available):
        self.calls.append(("react_fallback", frozenset(available)))
        return []

    async def final_synthesis(self, task_id, workflow, web_used, context):
        self.calls.append(("final_synthesis", workflow, web_used, context))
        return _synthesis(workflow, web_used=web_used)

    async def citation_check(self, task_id, synthesis):
        self.calls.append(("citation_check", task_id))
        return self.citation_results.pop(0)

    async def optional_critic(self, task_id, synthesis, web_used):
        self.calls.append(("optional_critic", web_used))
        return self.critic_result

    async def supplemental_research(self, task_id, agent_id):
        self.calls.append(("supplemental_research", task_id))
        return self.supplemental_result

    def persisted_evidence_context(self, task_id):
        return "supplemental evidence"

    def research_fallback_synthesis(self, task_id, user_input, error):
        self.calls.append(("research_fallback_synthesis", type(error).__name__))
        return _synthesis(WorkflowType.RESEARCH)

    async def finish(self, task_id, synthesis, degraded):
        self.finished = (task_id, synthesis, degraded)


def test_direct_strategy_preserves_context_and_web_flag():
    runtime = FakeRuntime()
    context = WorkflowContext(
        task_id="task_1",
        routing=_routing(WorkflowType.DIRECT, requires_web=True),
    )

    asyncio.run(DirectWorkflowStrategy().execute(context, runtime))

    assert runtime.finished[2] is False
    synthesis_call = next(item for item in runtime.calls if item[0] == "final_synthesis")
    assert synthesis_call[1:3] == (WorkflowType.DIRECT, True)
    assert "research context" in synthesis_call[3]


def test_plan_execute_strategy_emits_plan_and_uses_decomposition():
    runtime = FakeRuntime()
    runtime.event_service = SimpleNamespace(
        emit=lambda *args: runtime.calls.append(("emit", args[2]))
    )
    context = WorkflowContext(
        task_id="task_plan",
        routing=_routing(WorkflowType.PLAN_EXECUTE),
    )

    asyncio.run(PlanExecuteWorkflowStrategy().execute(context, runtime))

    assert ("decompose", "task_plan") in runtime.calls
    assert any(item[0] == "emit" for item in runtime.calls)
    synthesis_call = next(item for item in runtime.calls if item[0] == "final_synthesis")
    assert "first analyze, then answer" in synthesis_call[3]
    assert runtime.finished[2] is False

def test_react_strategy_rejects_unavailable_tool_and_degrades():
    decision = (
        '{"action":"tool_call","tool_name":"shell","arguments":{},'
        '"summary":"try shell","answer":""}'
    )
    runtime = FakeRuntime(llm_responses=[decision], tools={"calculator"})
    context = WorkflowContext(
        task_id="task_2",
        routing=_routing(WorkflowType.REACT),
    )

    asyncio.run(ReactWorkflowStrategy().execute(context, runtime))

    assert runtime.tool_executor.calls == []
    assert runtime.finished[2] is True
    synthesis_call = next(item for item in runtime.calls if item[0] == "final_synthesis")
    assert "Tool unavailable or disallowed: shell" in synthesis_call[3]


def test_react_strategy_keeps_successful_tool_observation():
    tool_call = (
        '{"action":"tool_call","tool_name":"calculator",'
        '"arguments":{"expression":"1+1"},"summary":"calculate","answer":""}'
    )
    final_answer = (
        '{"action":"final_answer","tool_name":"","arguments":{},'
        '"summary":"done","answer":"2"}'
    )
    runtime = FakeRuntime(
        llm_responses=[tool_call, final_answer],
        tools={"calculator"},
    )
    context = WorkflowContext(
        task_id="task_3",
        routing=_routing(WorkflowType.REACT),
    )

    asyncio.run(ReactWorkflowStrategy().execute(context, runtime))

    assert len(runtime.tool_executor.calls) == 1
    assert runtime.finished[2] is False
    synthesis_call = next(item for item in runtime.calls if item[0] == "final_synthesis")
    assert "calculator:" in synthesis_call[3]


def test_research_strategy_supplements_invalid_citations():
    runtime = FakeRuntime()
    runtime.citation_results = [False, True]
    runtime.supplemental_result = True
    context = WorkflowContext(
        task_id="task_4",
        routing=_routing(WorkflowType.RESEARCH, requires_web=True),
    )

    asyncio.run(ResearchWorkflowStrategy().execute(context, runtime))

    assert ("supplemental_research", "task_4") in runtime.calls
    assert sum(item[0] == "final_synthesis" for item in runtime.calls) == 2
    assert runtime.finished[2] is False


def test_research_strategy_marks_missing_evidence_degraded():
    runtime = FakeRuntime()
    runtime.research_result = ("", False)
    runtime.citation_results = [True]
    context = WorkflowContext(
        task_id="task_5",
        routing=_routing(WorkflowType.RESEARCH, requires_web=True),
    )

    asyncio.run(ResearchWorkflowStrategy().execute(context, runtime))

    assert runtime.finished[2] is True