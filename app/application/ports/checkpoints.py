from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.domain.models import Checkpoint


@runtime_checkable
class CheckpointStore(Protocol):
    async def save(self, checkpoint: Checkpoint) -> None: ...

    async def latest(self, task_id: str, run_id: str) -> Checkpoint | None: ...

    async def list(self, task_id: str, run_id: str) -> list[Checkpoint]: ...
