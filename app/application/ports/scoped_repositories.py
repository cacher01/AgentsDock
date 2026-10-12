from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol, runtime_checkable

from app.core.enums import TaskStatus, WorkflowType
from app.domain.identity import ResourceRef, ShareGrant
from app.domain.models import RequestContext, TaskSnapshot


@dataclass(frozen=True, slots=True)
class GovernanceTaskProjection:
    task_id: str
    workspace_id: str
    owner_user_id: str
    status: TaskStatus
    workflow: WorkflowType | None
    token_estimate: int
    cost: Decimal
    duration_ms: int | None
    model_ids: tuple[str, ...]
    tool_keys: tuple[str, ...]
    error_category: str | None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None


@runtime_checkable
class ScopedTaskRepository(Protocol):
    async def create_task(
        self,
        context: RequestContext,
        input_text: str,
        session_id: str | None = None,
        idempotency_key: str | None = None,
        requested_model_variant_id: str | None = None,
    ) -> TaskSnapshot: ...

    async def get_task(self, context: RequestContext, task_id: str) -> TaskSnapshot | None: ...

    async def list_tasks(self, context: RequestContext, limit: int = 50) -> list[TaskSnapshot]: ...

    async def update_execution(
        self,
        context: RequestContext,
        task_id: str,
        *,
        status: TaskStatus,
        objective: str = "",
        workflow: WorkflowType | None = None,
        token_estimate: int = 0,
        error_summary: str = "",
    ) -> TaskSnapshot: ...


@runtime_checkable
class ResourceShareRepository(Protocol):
    async def list_grants(
        self, context: RequestContext, resource: ResourceRef
    ) -> list[ShareGrant]: ...

    async def add_grant(
        self, context: RequestContext, resource: ResourceRef, grant: ShareGrant
    ) -> ShareGrant: ...

    async def remove_grant(
        self, context: RequestContext, resource: ResourceRef, grant: ShareGrant
    ) -> None: ...


@runtime_checkable
class GovernanceRepository(Protocol):
    async def get_task_metadata(
        self, context: RequestContext, task_id: str
    ) -> GovernanceTaskProjection | None: ...

    async def list_task_metadata(
        self, context: RequestContext, limit: int = 50
    ) -> list[GovernanceTaskProjection]: ...
