from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.domain.models import (
    Checkpoint,
    ExecutionNodeSnapshot,
    NodeStatus,
    RequestContext,
    RunSnapshot,
    RunStatus,
)


@runtime_checkable
class RuntimeExecutionRepository(Protocol):
    async def create_run(self, context: RequestContext, run: RunSnapshot) -> RunSnapshot: ...

    async def get_run(self, context: RequestContext, run_id: str) -> RunSnapshot | None: ...

    async def update_run_status(
        self,
        context: RequestContext,
        run_id: str,
        status: RunStatus,
        error_summary: str = "",
    ) -> RunSnapshot: ...

    async def create_node(
        self, context: RequestContext, node: ExecutionNodeSnapshot
    ) -> ExecutionNodeSnapshot: ...

    async def list_nodes(
        self, context: RequestContext, run_id: str
    ) -> list[ExecutionNodeSnapshot]: ...

    async def update_node_status(
        self,
        context: RequestContext,
        node_id: str,
        status: NodeStatus,
        *,
        output_ref: str | None = None,
    ) -> ExecutionNodeSnapshot: ...


@runtime_checkable
class ScopedCheckpointStore(Protocol):
    async def save(self, context: RequestContext, checkpoint: Checkpoint) -> None: ...

    async def latest(
        self, context: RequestContext, task_id: str, run_id: str
    ) -> Checkpoint | None: ...

    async def list(
        self, context: RequestContext, task_id: str, run_id: str
    ) -> list[Checkpoint]: ...

    async def latest_for_task(self, context: RequestContext, task_id: str) -> Checkpoint | None: ...
