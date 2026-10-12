from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any

from app.core.enums import TaskStatus, WorkflowType
from app.db.repositories import Repository
from app.domain.models import (
    ArtifactSnapshot,
    Checkpoint,
    ModelRequest,
    ModelResponse,
    ScheduledTask,
    TaskSnapshot,
)
from app.llm.client import LLMClient
from app.services.artifact_service import ArtifactService


class SQLiteTaskRepositoryAdapter:
    def __init__(self, repository: Repository) -> None:
        self._repository = repository

    async def create_task(self, input_text: str, session_id: str | None = None) -> TaskSnapshot:
        return _task_snapshot(self._repository.create_task(input_text, session_id))

    async def get_task(self, task_id: str) -> TaskSnapshot | None:
        row = self._repository.get_task(task_id)
        return _task_snapshot(row) if row else None

    async def list_tasks(self, limit: int = 50) -> list[TaskSnapshot]:
        return [_task_snapshot(row) for row in self._repository.list_tasks(limit)]

    async def update_task_status(
        self, task_id: str, status: TaskStatus, error_summary: str = ""
    ) -> TaskSnapshot:
        self._repository.update_task_status(task_id, status, error_summary)
        return self._required_task(task_id)

    async def update_task_workflow(
        self, task_id: str, workflow: WorkflowType, objective: str
    ) -> TaskSnapshot:
        self._repository.update_task_workflow(task_id, workflow.value, objective)
        return self._required_task(task_id)

    async def increment_task_tokens(self, task_id: str, amount: int) -> None:
        self._repository.increment_task_tokens(task_id, amount)

    async def check_task_runtime(self, task_id: str, timeout_seconds: int) -> None:
        self._repository.check_task_runtime(task_id, timeout_seconds)

    def _required_task(self, task_id: str) -> TaskSnapshot:
        row = self._repository.get_task(task_id)
        if row is None:
            raise ValueError(f"Task not found: {task_id}")
        return _task_snapshot(row)


class LegacyModelClientAdapter:
    def __init__(self, client: LLMClient) -> None:
        self._client = client

    async def invoke(self, request: ModelRequest) -> ModelResponse:
        options: dict[str, Any] = {}
        if request.model is not None:
            options["model"] = request.model
        if request.temperature is not None:
            options["temperature"] = request.temperature
        if request.max_tokens is not None:
            options["max_tokens"] = request.max_tokens
        response = await self._client.chat(
            [dict(message) for message in request.messages], **options
        )
        return ModelResponse(
            content=response.content,
            token_estimate=response.token_estimate,
            model=request.model,
            raw=response.raw,
        )


class LocalArtifactStoreAdapter:
    def __init__(self, service: ArtifactService) -> None:
        self._service = service

    async def write(
        self,
        task_id: str,
        filename: str,
        format: str,
        *,
        content: Any = None,
        rows: Any = None,
    ) -> ArtifactSnapshot:
        row = self._service.write(task_id, filename, format, content=content, rows=rows)
        return _artifact_snapshot(row)

    async def read(self, artifact: ArtifactSnapshot) -> bytes:
        row = {
            "artifact_id": artifact.artifact_id,
            "task_id": artifact.task_id,
            "filename": artifact.filename,
            "media_type": artifact.media_type,
            "size_bytes": artifact.size_bytes,
            "relative_path": artifact.relative_path,
            "created_at": artifact.created_at.isoformat(),
        }
        return self._service.resolve_artifact_path(row).read_bytes()


class InMemoryCheckpointStore:
    def __init__(self) -> None:
        self._items: dict[tuple[str, str], list[Checkpoint]] = {}

    async def save(self, checkpoint: Checkpoint) -> None:
        key = (checkpoint.task_id, checkpoint.run_id)
        items = self._items.setdefault(key, [])
        if any(item.checkpoint_id == checkpoint.checkpoint_id for item in items):
            return
        if items and checkpoint.sequence <= items[-1].sequence:
            raise ValueError("checkpoint sequence must increase")
        items.append(checkpoint)

    async def latest(self, task_id: str, run_id: str) -> Checkpoint | None:
        items = self._items.get((task_id, run_id), [])
        return items[-1] if items else None

    async def list(self, task_id: str, run_id: str) -> list[Checkpoint]:
        return list(self._items.get((task_id, run_id), []))


class InlineTaskScheduler:
    def __init__(self, runner: Callable[[str], Awaitable[None]]) -> None:
        self._runner = runner
        self._queue: list[str] = []
        self._claimed: dict[str, str] = {}
        self._cancelled: set[str] = set()

    async def submit(self, task_id: str) -> None:
        if task_id not in self._queue and task_id not in self._claimed:
            self._queue.append(task_id)

    async def cancel(self, task_id: str) -> None:
        self._cancelled.add(task_id)
        if task_id in self._queue:
            self._queue.remove(task_id)

    async def claim(self, worker_id: str) -> ScheduledTask | None:
        while self._queue:
            task_id = self._queue.pop(0)
            if task_id in self._cancelled:
                continue
            self._claimed[task_id] = worker_id
            return ScheduledTask(task_id=task_id)
        return None

    async def acknowledge(self, task_id: str, worker_id: str) -> None:
        self._release(task_id, worker_id)

    async def fail(self, task_id: str, worker_id: str, reason: str) -> None:
        self._release(task_id, worker_id)

    async def run_claimed(self, task_id: str, worker_id: str) -> None:
        if self._claimed.get(task_id) != worker_id:
            raise ValueError("task is not claimed by this worker")
        await self._runner(task_id)

    def _release(self, task_id: str, worker_id: str) -> None:
        if self._claimed.get(task_id) != worker_id:
            raise ValueError("task is not claimed by this worker")
        del self._claimed[task_id]


def _task_snapshot(row: dict[str, Any]) -> TaskSnapshot:
    workflow = row.get("workflow")
    completed_at = row.get("completed_at")
    return TaskSnapshot(
        task_id=str(row["task_id"]),
        session_id=row.get("session_id"),
        input_text=str(row["input"]),
        objective=str(row.get("objective") or ""),
        workflow=WorkflowType(workflow) if workflow else None,
        status=TaskStatus(row["status"]),
        token_estimate=int(row.get("token_estimate") or 0),
        error_summary=str(row.get("error_summary") or ""),
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
        completed_at=datetime.fromisoformat(completed_at) if completed_at else None,
    )


def _artifact_snapshot(row: dict[str, Any]) -> ArtifactSnapshot:
    return ArtifactSnapshot(
        artifact_id=str(row["artifact_id"]),
        task_id=str(row["task_id"]),
        filename=str(row["filename"]),
        media_type=str(row["media_type"]),
        size_bytes=int(row["size_bytes"]),
        relative_path=str(row["relative_path"]),
        created_at=datetime.fromisoformat(row["created_at"]),
    )
