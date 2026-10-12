from __future__ import annotations

from typing import Any, Mapping, Protocol, runtime_checkable

from app.domain.models import (
    AgentRunSnapshot,
    EvidenceSnapshot,
    RequestContext,
    ResultSnapshot,
    RuntimeEventSnapshot,
    ToolCallSnapshot,
)


@runtime_checkable
class RuntimeFactRepository(Protocol):
    async def create_agent_run(
        self, context: RequestContext, agent: AgentRunSnapshot
    ) -> AgentRunSnapshot: ...

    async def update_agent_status(
        self,
        context: RequestContext,
        agent_run_id: str,
        status: str,
    ) -> AgentRunSnapshot: ...

    async def list_agent_runs(
        self, context: RequestContext, task_id: str
    ) -> list[AgentRunSnapshot]: ...

    async def append_event(
        self,
        context: RequestContext,
        *,
        task_id: str,
        event_type: str,
        payload: Mapping[str, Any],
        summary: str = "",
        run_id: str | None = None,
        agent_run_id: str | None = None,
    ) -> RuntimeEventSnapshot: ...

    async def list_events(
        self, context: RequestContext, task_id: str
    ) -> list[RuntimeEventSnapshot]: ...

    async def save_tool_call(
        self, context: RequestContext, call: ToolCallSnapshot
    ) -> ToolCallSnapshot: ...

    async def list_tool_calls(
        self, context: RequestContext, task_id: str
    ) -> list[ToolCallSnapshot]: ...

    async def save_evidence(
        self, context: RequestContext, evidence: EvidenceSnapshot
    ) -> EvidenceSnapshot: ...

    async def list_evidence(
        self, context: RequestContext, task_id: str
    ) -> list[EvidenceSnapshot]: ...

    async def save_result(
        self, context: RequestContext, result: ResultSnapshot
    ) -> ResultSnapshot: ...

    async def get_result(self, context: RequestContext, task_id: str) -> ResultSnapshot | None: ...
