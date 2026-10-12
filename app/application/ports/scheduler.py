from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.domain.models import ScheduledTask


@runtime_checkable
class TaskScheduler(Protocol):
    async def submit(self, task_id: str) -> None: ...

    async def cancel(self, task_id: str) -> None: ...

    async def claim(self, worker_id: str) -> ScheduledTask | None: ...

    async def acknowledge(self, task_id: str, worker_id: str) -> None: ...

    async def fail(self, task_id: str, worker_id: str, reason: str) -> None: ...

    async def requeue_expired(self) -> int: ...
