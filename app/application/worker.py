from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from contextlib import suppress

from app.domain.models import ScheduledTask


TaskRunner = Callable[[ScheduledTask], Awaitable[None]]


class TaskWorker:
    def __init__(
        self,
        *,
        scheduler,
        runner: TaskRunner,
        worker_id: str,
        poll_interval: float = 1.0,
        heartbeat_interval: float = 20.0,
    ) -> None:
        if not worker_id:
            raise ValueError("worker_id is required")
        if poll_interval <= 0 or heartbeat_interval <= 0:
            raise ValueError("worker intervals must be positive")
        self.scheduler = scheduler
        self.runner = runner
        self.worker_id = worker_id
        self.poll_interval = poll_interval
        self.heartbeat_interval = heartbeat_interval
        self.logger = logging.getLogger(__name__)

    async def run_forever(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            await self._requeue_expired()
            task = await self.scheduler.claim(self.worker_id)
            if task is None:
                await self._wait_or_stop(stop, self.poll_interval)
                continue
            await self.run_one(task)

    async def _requeue_expired(self) -> None:
        try:
            count = await self.scheduler.requeue_expired()
        except Exception:
            self.logger.exception(
                "expired task lease recovery failed",
                extra={"worker_id": self.worker_id},
            )
            return
        if count:
            self.logger.warning(
                "expired task leases requeued",
                extra={"worker_id": self.worker_id, "count": count},
            )

    async def run_one(self, task: ScheduledTask) -> None:
        heartbeat = asyncio.create_task(self._heartbeat(task.task_id))
        try:
            await self.runner(task)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.logger.exception(
                "task execution failed",
                extra={"task_id": task.task_id, "worker_id": self.worker_id},
            )
            await self.scheduler.fail(task.task_id, self.worker_id, str(exc))
        else:
            await self.scheduler.acknowledge(task.task_id, self.worker_id)
        finally:
            heartbeat.cancel()
            with suppress(asyncio.CancelledError):
                await heartbeat

    async def _heartbeat(self, task_id: str) -> None:
        while True:
            await asyncio.sleep(self.heartbeat_interval)
            try:
                await self.scheduler.heartbeat(task_id, self.worker_id)
            except Exception:
                self.logger.exception(
                    "worker heartbeat failed",
                    extra={"task_id": task_id, "worker_id": self.worker_id},
                )
                return

    @staticmethod
    async def _wait_or_stop(stop: asyncio.Event, timeout: float) -> None:
        try:
            await asyncio.wait_for(stop.wait(), timeout)
        except TimeoutError:
            pass
