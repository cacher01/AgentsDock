from __future__ import annotations

import asyncio
import os

import pytest

from app.domain.identity import WorkspaceRole, build_request_context
from app.persistence.postgres import (
    PostgresScopedTaskRepository,
    PostgresTaskScheduler,
    create_postgres_engine,
    create_session_factory,
)
from scripts.postgres_dev_reset import (
    reset_development_database,
    validate_dev_database_url,
)


def _seed(psycopg, database_url: str) -> None:
    with psycopg.connect(database_url, autocommit=True) as connection:
        connection.execute(
            """
            insert into users (id, email, display_name, password_hash)
            values ('queue_owner', 'queue@example.com', 'Owner', 'hash')
            """
        )
        connection.execute(
            """
            insert into workspaces (id, name, slug, created_by)
            values ('queue_workspace', 'Queue', 'queue-workspace', 'queue_owner')
            """
        )
        connection.execute(
            """
            insert into workspace_memberships (
                workspace_id, user_id, role, status, joined_at
            ) values (
                'queue_workspace', 'queue_owner', 'member', 'active', now()
            )
            """
        )


def test_scheduler_claims_distinct_tasks_and_tracks_leases() -> None:
    database_url = os.getenv("TEST_POSTGRES_URL")
    if not database_url:
        pytest.skip("TEST_POSTGRES_URL is not configured")
    psycopg = pytest.importorskip("psycopg")
    normalized = validate_dev_database_url(database_url)
    reset_development_database(normalized)
    _seed(psycopg, normalized)

    async def scenario() -> None:
        engine = create_postgres_engine(normalized)
        factory = create_session_factory(engine)
        tasks = PostgresScopedTaskRepository(factory)
        scheduler = PostgresTaskScheduler(factory, lease_seconds=30)
        context = build_request_context(
            request_id="queue_request",
            user_id="queue_owner",
            workspace_id="queue_workspace",
            role=WorkspaceRole.MEMBER,
        )
        try:
            first = await tasks.create_task(context, "first")
            second = await tasks.create_task(context, "second")
            await scheduler.submit(first.task_id)
            await scheduler.submit(second.task_id)

            claimed_first = await scheduler.claim("worker_1")
            claimed_second = await scheduler.claim("worker_2")
            assert claimed_first is not None
            assert claimed_second is not None
            assert claimed_first.task_id != claimed_second.task_id
            assert await scheduler.claim("worker_3") is None

            await scheduler.heartbeat(claimed_first.task_id, "worker_1")
            await scheduler.acknowledge(claimed_first.task_id, "worker_1")
            await scheduler.fail(
                claimed_second.task_id, "worker_2", "execution failed"
            )
        finally:
            await engine.dispose()

    asyncio.run(scenario())

    with psycopg.connect(normalized) as connection:
        failed = connection.execute(
            """
            select status, error_summary from tasks
            where status='failed'
            """
        ).fetchone()
        leases = connection.execute(
            "select count(*) from worker_leases"
        ).fetchone()[0]
    assert failed == ("failed", "execution failed")
    assert leases == 0
