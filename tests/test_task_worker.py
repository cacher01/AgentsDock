from __future__ import annotations

import asyncio

from app.application.worker import TaskWorker
from app.domain.models import ScheduledTask


class FakeScheduler:
    def __init__(self):
        self.acked = []
        self.failed = []
        self.heartbeats = []

    async def acknowledge(self, task_id, worker_id):
        self.acked.append((task_id, worker_id))

    async def fail(self, task_id, worker_id, reason):
        self.failed.append((task_id, worker_id, reason))

    async def heartbeat(self, task_id, worker_id):
        self.heartbeats.append((task_id, worker_id))


def test_worker_acknowledges_successful_execution() -> None:
    scheduler = FakeScheduler()
    executed = []

    async def runner(task):
        executed.append(task.task_id)

    worker = TaskWorker(
        scheduler=scheduler,
        runner=runner,
        worker_id="worker_1",
        heartbeat_interval=60,
    )
    asyncio.run(worker.run_one(ScheduledTask(task_id="task_1")))

    assert executed == ["task_1"]
    assert scheduler.acked == [("task_1", "worker_1")]
    assert scheduler.failed == []


def test_worker_records_execution_failure() -> None:
    scheduler = FakeScheduler()

    async def runner(task):
        raise RuntimeError("model unavailable")

    worker = TaskWorker(
        scheduler=scheduler,
        runner=runner,
        worker_id="worker_1",
        heartbeat_interval=60,
    )
    asyncio.run(worker.run_one(ScheduledTask(task_id="task_1")))

    assert scheduler.acked == []
    assert scheduler.failed == [("task_1", "worker_1", "model unavailable")]


def test_worker_requeues_expired_leases_before_claiming() -> None:
    calls = []
    stop = asyncio.Event()

    class Scheduler(FakeScheduler):
        async def requeue_expired(self):
            calls.append("requeue")
            return 1

        async def claim(self, worker_id):
            calls.append(("claim", worker_id))
            stop.set()
            return None

    async def runner(_task):
        raise AssertionError("no task should be returned")

    worker = TaskWorker(
        scheduler=Scheduler(),
        runner=runner,
        worker_id="worker_1",
        poll_interval=0.001,
        heartbeat_interval=60,
    )
    asyncio.run(worker.run_forever(stop))

    assert calls == ["requeue", ("claim", "worker_1")]
