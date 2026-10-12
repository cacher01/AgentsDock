from __future__ import annotations

from typing import TYPE_CHECKING

from app.core.enums import WorkflowType
from app.schemas.workflow import (
    FinalSynthesis,
    RoundEvaluation,
    SpawnPlan,
    SubAgentOutput,
    SubTask,
    TaskDecomposition,
)
from app.workflows.base import WorkflowContext

if TYPE_CHECKING:
    from app.orchestration.orchestrator import Orchestrator


class LegacyWorkflowRuntime:
    """Narrow adapter exposing existing orchestration services to strategies."""

    def __init__(self, orchestrator: Orchestrator) -> None:
        self._orchestrator = orchestrator
        self.settings = orchestrator.settings
        self.repository = orchestrator.repository
        self.event_service = orchestrator.event_service
        self.llm = orchestrator.llm
        self.tool_executor = orchestrator.tool_executor
        self.agent_runtime = orchestrator.agent_runtime

    def checkpoint(self, task_id: str) -> None:
        self._orchestrator._checkpoint(task_id)

    def task_context(self, task_id: str) -> str:
        return self._orchestrator._task_context(task_id)

    async def decompose(self, task_id: str) -> TaskDecomposition:
        return await self._orchestrator._decompose(task_id)

    async def research_subtask(
        self,
        task_id: str,
        agent_id: str,
        subtask: SubTask,
        requires_web: bool,
    ) -> tuple[str, bool]:
        return await self._orchestrator._research_subtask(task_id, agent_id, subtask, requires_web)

    async def spawn_plan(self, round_number: int) -> SpawnPlan:
        return await self._orchestrator._spawn_plan(round_number)

    async def round_evaluation(self, round_number: int) -> RoundEvaluation:
        return await self._orchestrator._round_evaluation(round_number)

    async def maybe_research(
        self, task_id: str, agent_id: str | None, requires_web: bool
    ) -> tuple[str, bool]:
        return await self._orchestrator._maybe_research(task_id, agent_id, requires_web)

    async def research_context(self, task_id: str, agent_id: str | None) -> tuple[str, bool]:
        return await self._orchestrator._research_context(task_id, agent_id)

    async def react_fallback(self, task_id: str, user_input: str, available: set[str]) -> list[str]:
        return await self._orchestrator._run_react_fallback(task_id, user_input, available)

    async def final_synthesis(
        self,
        task_id: str,
        workflow: WorkflowType,
        web_used: bool,
        context: str,
    ) -> FinalSynthesis:
        return await self._orchestrator._final_synthesis(task_id, workflow, web_used, context)

    async def citation_check(self, task_id: str, synthesis: FinalSynthesis) -> bool:
        return await self._orchestrator._run_citation_check(task_id, synthesis)

    async def optional_critic(
        self, task_id: str, synthesis: FinalSynthesis, web_used: bool
    ) -> bool:
        return await self._orchestrator._run_optional_critic(task_id, synthesis, web_used)

    async def supplemental_research(self, task_id: str, agent_id: str | None) -> bool:
        return await self._orchestrator._supplemental_research(task_id, agent_id)

    def persisted_evidence_context(self, task_id: str) -> str:
        return self._orchestrator._persisted_evidence_context(task_id)

    def research_fallback_synthesis(
        self, task_id: str, user_input: str, error: Exception
    ) -> FinalSynthesis:
        return self._orchestrator._research_fallback_synthesis(task_id, user_input, error)

    def multi_agent_fallback_synthesis(
        self,
        task_id: str,
        user_input: str,
        outputs: list[SubAgentOutput],
        error: Exception,
    ) -> FinalSynthesis:
        return self._orchestrator._multi_agent_fallback_synthesis(
            task_id, user_input, outputs, error
        )

    async def execute_workflow(self, workflow: WorkflowType, context: WorkflowContext) -> None:
        await self._orchestrator.workflow_strategies[workflow].execute(context, self)

    async def finish(self, task_id: str, synthesis: FinalSynthesis, degraded: bool) -> None:
        await self._orchestrator._finish(task_id, synthesis, degraded)
