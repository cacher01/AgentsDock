from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.core.enums import TaskStatus, WorkflowType
from app.domain.models import TaskSnapshot


@runtime_checkable
class TaskRepository(Protocol):
    async def create_task(self, input_text: str, session_id: str | None = None) -> TaskSnapshot: ...

    async def get_task(self, task_id: str) -> TaskSnapshot | None: ...

    async def list_tasks(self, limit: int = 50) -> list[TaskSnapshot]: ...

    async def update_task_status(
        self, task_id: str, status: TaskStatus, error_summary: str = ""
    ) -> TaskSnapshot: ...

    async def update_task_workflow(
        self, task_id: str, workflow: WorkflowType, objective: str
    ) -> TaskSnapshot: ...

    async def increment_task_tokens(self, task_id: str, amount: int) -> None: ...

    async def check_task_runtime(self, task_id: str, timeout_seconds: int) -> None: ...
