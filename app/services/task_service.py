import asyncio
from typing import Any

from app.core.enums import EventType, TaskStatus
from app.db.repositories import Repository
from app.orchestration.orchestrator import Orchestrator
from app.services.event_service import EventService


class TaskService:
    def __init__(
        self,
        repository: Repository,
        event_service: EventService,
        orchestrator: Orchestrator,
        run_inline: bool = False,
    ) -> None:
        self.repository = repository
        self.event_service = event_service
        self.orchestrator = orchestrator
        self.run_inline = run_inline

    async def create_task(self, input_text: str, session_id: str | None = None) -> dict[str, Any]:
        task = self.repository.create_task(input_text, session_id=session_id)
        self.event_service.emit(
            task["task_id"],
            None,
            EventType.TASK_CREATED,
            {"input": input_text},
            "Task created",
        )
        self.repository.update_task_status(task["task_id"], TaskStatus.QUEUED)
        if self.run_inline:
            await self.orchestrator.run_task(task["task_id"])
        else:
            asyncio.create_task(self.orchestrator.run_task(task["task_id"]))
        loaded = self.repository.get_task(task["task_id"])
        return loaded or task

    def cancel_task(self, task_id: str) -> dict[str, Any]:
        task = self.repository.get_task(task_id)
        if task is None:
            raise ValueError(f"Task not found: {task_id}")
        if task["status"] in {
            TaskStatus.COMPLETED.value,
            TaskStatus.DEGRADED.value,
            TaskStatus.FAILED.value,
            TaskStatus.CANCELLED.value,
        }:
            return task
        self.repository.update_task_status(task_id, TaskStatus.CANCELLED)
        self.event_service.emit(
            task_id,
            None,
            EventType.TASK_CANCELLED,
            {},
            "Task cancelled",
        )
        return self.repository.get_task(task_id) or task
