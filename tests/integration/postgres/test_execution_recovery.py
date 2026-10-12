from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone

import pytest

from app.core.enums import WorkflowType
from app.domain.identity import WorkspaceRole, build_request_context
from app.domain.models import (
    Checkpoint,
    ExecutionNodeSnapshot,
    NodeStatus,
    RunSnapshot,
    RunStatus,
)
from app.domain.transitions import InvalidStateTransition
from app.persistence.postgres import (
    PostgresRuntimeExecutionRepository,
    PostgresScopedCheckpointStore,
    PostgresScopedTaskRepository,
    create_postgres_engine,
    create_session_factory,
)
from scripts.postgres_dev_reset import (
    reset_development_database,
    validate_dev_database_url,
)


def _context(user_id: str, workspace_id: str):
    return build_request_context(
        request_id=f"request_{user_id}",
        user_id=user_id,
        workspace_id=workspace_id,
        role=WorkspaceRole.MEMBER,
    )


def _seed_owners(psycopg, database_url: str) -> None:
    with psycopg.connect(database_url, autocommit=True) as connection:
        connection.execute(
            """
            insert into users (id, email, display_name, password_hash)
            values
                ('owner_a', 'runtime-a@example.com', 'Owner A', 'hash'),
                ('owner_b', 'runtime-b@example.com', 'Owner B', 'hash')
            """
        )
        connection.execute(
            """
            insert into workspaces (id, name, slug, created_by)
            values
                ('workspace_a', 'Workspace A', 'runtime-workspace-a', 'owner_a'),
                ('workspace_b', 'Workspace B', 'runtime-workspace-b', 'owner_b')
            """
        )
        connection.execute(
            """
            insert into workspace_memberships (
                workspace_id, user_id, role, status, joined_at
            )
            values
                ('workspace_a', 'owner_a', 'member', 'active', now()),
                ('workspace_b', 'owner_b', 'member', 'active', now())
            """
        )


def test_execution_recovery_is_scoped_and_state_checked() -> None:
    database_url = os.getenv("TEST_POSTGRES_URL")
    if not database_url:
        pytest.skip("TEST_POSTGRES_URL is not configured")
    psycopg = pytest.importorskip("psycopg")
    normalized = validate_dev_database_url(database_url)
    reset_development_database(normalized)
    _seed_owners(psycopg, normalized)

    async def scenario() -> None:
        engine = create_postgres_engine(normalized)
        factory = create_session_factory(engine)
        tasks = PostgresScopedTaskRepository(factory)
        execution = PostgresRuntimeExecutionRepository(factory)
        checkpoints = PostgresScopedCheckpointStore(factory)
        owner = _context("owner_a", "workspace_a")
        outsider = _context("owner_b", "workspace_b")
        now = datetime.now(timezone.utc)

        try:
            task = await tasks.create_task(owner, "recoverable task")
            run = await execution.create_run(
                owner,
                RunSnapshot(
                    run_id="run_1",
                    task_id=task.task_id,
                    attempt=1,
                    status=RunStatus.QUEUED,
                    workflow=WorkflowType.DIRECT,
                ),
            )
            assert run.task_id == task.task_id
            assert await execution.get_run(outsider, run.run_id) is None

            running = await execution.update_run_status(
                owner, run.run_id, RunStatus.RUNNING
            )
            assert running.status == RunStatus.RUNNING
            assert running.started_at is not None
            with pytest.raises(InvalidStateTransition):
                await execution.update_run_status(
                    owner, run.run_id, RunStatus.QUEUED
                )

            node = await execution.create_node(
                owner,
                ExecutionNodeSnapshot(
                    node_id="node_1",
                    run_id=run.run_id,
                    node_key="answer",
                    node_type="agent",
                    status=NodeStatus.PENDING,
                    attempt=1,
                    created_at=now,
                    updated_at=now,
                ),
            )
            await execution.update_node_status(
                owner, node.node_id, NodeStatus.RUNNING
            )
            completed = await execution.update_node_status(
                owner,
                node.node_id,
                NodeStatus.COMPLETED,
                output_ref="result_1",
            )
            assert completed.output_ref == "result_1"
            assert await execution.list_nodes(outsider, run.run_id) == []

            checkpoint = Checkpoint(
                checkpoint_id="checkpoint_1",
                task_id=task.task_id,
                run_id=run.run_id,
                sequence=1,
                kind="answer",
                payload={"node_id": node.node_id},
                created_at=now,
                workflow_version="workflow-v1",
            )
            await checkpoints.save(owner, checkpoint)
            await checkpoints.save(owner, checkpoint)
            assert await checkpoints.list(owner, task.task_id, run.run_id) == [
                checkpoint
            ]
            assert (
                await checkpoints.latest(
                    outsider, task.task_id, run.run_id
                )
                is None
            )

            finished = await execution.update_run_status(
                owner, run.run_id, RunStatus.COMPLETED
            )
            assert finished.finished_at is not None
        finally:
            await engine.dispose()

    asyncio.run(scenario())
