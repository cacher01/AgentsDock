from __future__ import annotations

import uuid
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.models import ScheduledTask


class PostgresTaskScheduler:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        lease_seconds: int = 60,
    ) -> None:
        if lease_seconds < 10:
            raise ValueError("lease_seconds must be at least 10")
        self._session_factory = session_factory
        self._lease_seconds = lease_seconds

    async def submit(self, task_id: str) -> None:
        async with self._session_factory() as session:
            async with session.begin():
                result = await session.execute(
                    text(
                        """
                        update tasks set status='queued', updated_at=now()
                        where id=:task_id and status='created'
                        """
                    ),
                    {"task_id": task_id},
                )
                if result.rowcount != 1:
                    row = (
                        await session.execute(
                            text("select status from tasks where id=:task_id"),
                            {"task_id": task_id},
                        )
                    ).first()
                    if row is None:
                        raise ValueError("task not found")
                    if row[0] != "queued":
                        raise ValueError("task cannot be submitted from current state")

    async def cancel(self, task_id: str) -> None:
        async with self._session_factory() as session:
            async with session.begin():
                result = await session.execute(
                    text(
                        """
                        update tasks
                        set status='cancelled', updated_at=now(),
                            completed_at=coalesce(completed_at, now())
                        where id=:task_id
                          and status in ('created','queued','running')
                        """
                    ),
                    {"task_id": task_id},
                )
                if result.rowcount != 1:
                    raise ValueError("task cannot be cancelled")
                await session.execute(
                    text("delete from worker_leases where task_id=:task_id"),
                    {"task_id": task_id},
                )

    async def claim(self, worker_id: str) -> ScheduledTask | None:
        if not worker_id:
            raise ValueError("worker_id is required")
        async with self._session_factory() as session:
            async with session.begin():
                row = (
                    await session.execute(
                        text(
                            """
                            select t.id, t.workspace_id, t.owner_user_id,
                                   coalesce((
                                       select max(r.attempt)
                                       from task_runs r
                                       where r.task_id=t.id
                                   ), 0) + 1 as attempt
                            from tasks t
                            left join worker_leases l on l.task_id=t.id
                            where t.status='queued' and l.task_id is null
                            order by t.priority desc, t.created_at asc
                            for update of t skip locked
                            limit 1
                            """
                        )
                    )
                ).first()
                if row is None:
                    return None
                task_id = str(row[0])
                workspace_id = str(row[1])
                owner_user_id = str(row[2])
                attempt = int(row[3])
                lease_token = f"lease_{uuid.uuid4().hex}"
                await session.execute(
                    text(
                        """
                        insert into worker_leases (
                            task_id, worker_id, lease_token,
                            leased_until, heartbeat_at
                        ) values (
                            :task_id, :worker_id, :lease_token,
                            now() + make_interval(secs => :lease_seconds),
                            now()
                        )
                        """
                    ),
                    {
                        "task_id": task_id,
                        "worker_id": worker_id,
                        "lease_token": lease_token,
                        "lease_seconds": self._lease_seconds,
                    },
                )
                await session.execute(
                    text(
                        """
                        update tasks set status='running', updated_at=now()
                        where id=:task_id
                        """
                    ),
                    {"task_id": task_id},
                )
                return ScheduledTask(
                    task_id=task_id,
                    attempt=attempt,
                    workspace_id=workspace_id,
                    owner_user_id=owner_user_id,
                )

    async def heartbeat(self, task_id: str, worker_id: str) -> None:
        async with self._session_factory() as session:
            async with session.begin():
                result = await session.execute(
                    text(
                        """
                        update worker_leases
                        set heartbeat_at=now(),
                            leased_until=now() + make_interval(secs => :lease_seconds),
                            updated_at=now()
                        where task_id=:task_id and worker_id=:worker_id
                          and leased_until > now()
                        """
                    ),
                    {
                        "task_id": task_id,
                        "worker_id": worker_id,
                        "lease_seconds": self._lease_seconds,
                    },
                )
                if result.rowcount != 1:
                    raise ValueError("active worker lease not found")

    async def acknowledge(self, task_id: str, worker_id: str) -> None:
        await self._release(task_id, worker_id)

    async def fail(self, task_id: str, worker_id: str, reason: str) -> None:
        async with self._session_factory() as session:
            async with session.begin():
                owned = await self._require_lease(session, task_id, worker_id)
                if not owned:
                    raise ValueError("worker lease not found")
                await session.execute(
                    text(
                        """
                        update tasks
                        set status='failed', error_summary=:reason,
                            updated_at=now(), completed_at=now()
                        where id=:task_id
                        """
                    ),
                    {"task_id": task_id, "reason": reason[:2000]},
                )
                await session.execute(
                    text("delete from worker_leases where task_id=:task_id"),
                    {"task_id": task_id},
                )

    async def requeue_expired(self) -> int:
        async with self._session_factory() as session:
            async with session.begin():
                rows = (
                    await session.execute(
                        text(
                            """
                            delete from worker_leases
                            where leased_until <= now()
                            returning task_id
                            """
                        )
                    )
                ).all()
                if not rows:
                    return 0
                task_ids = [str(row[0]) for row in rows]
                await session.execute(
                    text(
                        """
                        update tasks set status='queued', updated_at=now(),
                            error_summary='worker lease expired; task requeued'
                        where id = any(:task_ids) and status='running'
                        """
                    ),
                    {"task_ids": task_ids},
                )
                return len(task_ids)

    async def _release(self, task_id: str, worker_id: str) -> None:
        async with self._session_factory() as session:
            async with session.begin():
                if not await self._require_lease(session, task_id, worker_id):
                    raise ValueError("worker lease not found")
                await session.execute(
                    text(
                        """
                        delete from worker_leases
                        where task_id=:task_id and worker_id=:worker_id
                        """
                    ),
                    {"task_id": task_id, "worker_id": worker_id},
                )

    @staticmethod
    async def _require_lease(session: AsyncSession, task_id: str, worker_id: str) -> bool:
        row = (
            await session.execute(
                text(
                    """
                    select 1 from worker_leases
                    where task_id=:task_id and worker_id=:worker_id
                    """
                ),
                {"task_id": task_id, "worker_id": worker_id},
            )
        ).first()
        return row is not None
