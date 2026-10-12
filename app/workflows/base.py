from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from app.core.enums import WorkflowType
from app.schemas.workflow import (
    FinalSynthesis,
    RoutingDecision,
    SubAgentOutput,
    SubTask,
    TaskDecomposition,
)


@dataclass
class WorkflowResult:
    workflow: WorkflowType
    web_used: bool = False
    sub_agent_summaries: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class WorkflowContext:
    task_id: str
    routing: RoutingDecision


class WorkflowRuntime(Protocol):
    settings: Any
    repository: Any
    event_service: Any
    llm: Any
    tool_executor: Any
    agent_runtime: Any

    def checkpoint(self, task_id: str) -> None: ...

    def task_context(self, task_id: str) -> str: ...

    async def maybe_research(
        self, task_id: str, agent_id: str | None, requires_web: bool
    ) -> tuple[str, bool]: ...

    async def research_context(self, task_id: str, agent_id: str | None) -> tuple[str, bool]: ...

    async def react_fallback(
        self, task_id: str, user_input: str, available: set[str]
    ) -> list[str]: ...

    async def decompose(self, task_id: str) -> TaskDecomposition: ...

    async def research_subtask(
        self,
        task_id: str,
        agent_id: str,
        subtask: SubTask,
        requires_web: bool,
    ) -> tuple[str, bool]: ...

    async def spawn_plan(self, round_number: int) -> Any: ...

    async def round_evaluation(self, round_number: int) -> Any: ...

    async def final_synthesis(
        self,
        task_id: str,
        workflow: WorkflowType,
        web_used: bool,
        context: str,
    ) -> FinalSynthesis: ...

    async def citation_check(self, task_id: str, synthesis: FinalSynthesis) -> bool: ...

    async def optional_critic(
        self, task_id: str, synthesis: FinalSynthesis, web_used: bool
    ) -> bool: ...

    async def supplemental_research(self, task_id: str, agent_id: str | None) -> bool: ...

    def persisted_evidence_context(self, task_id: str) -> str: ...

    def research_fallback_synthesis(
        self, task_id: str, user_input: str, error: Exception
    ) -> FinalSynthesis: ...

    def multi_agent_fallback_synthesis(
        self,
        task_id: str,
        user_input: str,
        outputs: list[SubAgentOutput],
        error: Exception,
    ) -> FinalSynthesis: ...

    async def finish(self, task_id: str, synthesis: FinalSynthesis, degraded: bool) -> None: ...

    async def execute_workflow(self, workflow: WorkflowType, context: WorkflowContext) -> None: ...


class WorkflowStrategy(Protocol):
    workflow_type: WorkflowType

    async def execute(self, context: WorkflowContext, runtime: WorkflowRuntime) -> None: ...
