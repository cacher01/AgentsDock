from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.models import (
    Checkpoint,
    ExecutionNodeSnapshot,
    NodeStatus,
    RequestContext,
    RunSnapshot,
    RunStatus,
)
from app.domain.transitions import ensure_node_transition, ensure_run_transition


_OWNER_TASK_SCOPE = """
t.workspace_id = :workspace_id
and t.owner_user_id = :user_id
"""

_RUN_SELECT = f"""
select
    r.id, r.task_id, r.attempt, r.status, t.workflow,
    r.started_at, r.finished_at, r.error_summary
from task_runs r
join tasks t on t.id = r.task_id
where r.id = :run_id
  and {_OWNER_TASK_SCOPE}
"""

_NODE_SELECT = f"""
select
    n.id, n.run_id, n.node_key, n.node_type, n.status, n.attempt,
    n.input_ref, n.output_ref, n.created_at, n.updated_at
from execution_nodes n
join task_runs r on r.id = n.run_id
join tasks t on t.id = r.task_id
where n.id = :node_id
  and {_OWNER_TASK_SCOPE}
"""

_CHECKPOINT_SELECT = f"""
select
    c.id, r.task_id, c.run_id, c.sequence, c.phase,
    c.state_json, c.workflow_version, c.created_at
from checkpoints c
join task_runs r on r.id = c.run_id
join tasks t on t.id = r.task_id
where c.run_id = :run_id
  and r.task_id = :task_id
  and {_OWNER_TASK_SCOPE}
"""


def _scope(context: RequestContext) -> dict[str, str]:
    return {
        "workspace_id": context.workspace_id,
        "user_id": context.user_id,
    }


def _run_from_row(row: Mapping[str, Any]) -> RunSnapshot:
    from app.core.enums import WorkflowType

    workflow = row["workflow"]
    return RunSnapshot(
        run_id=str(row["id"]),
        task_id=str(row["task_id"]),
        attempt=int(row["attempt"]),
        status=RunStatus(row["status"]),
        workflow=WorkflowType(workflow) if workflow else None,
        started_at=row["started_at"],
        finished_at=row["finished_at"],
        error_summary=str(row["error_summary"] or ""),
    )


def _node_from_row(row: Mapping[str, Any]) -> ExecutionNodeSnapshot:
    return ExecutionNodeSnapshot(
        node_id=str(row["id"]),
        run_id=str(row["run_id"]),
        node_key=str(row["node_key"]),
        node_type=str(row["node_type"]),
        status=NodeStatus(row["status"]),
        attempt=int(row["attempt"]),
        input_ref=row["input_ref"],
        output_ref=row["output_ref"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _checkpoint_from_row(row: Mapping[str, Any]) -> Checkpoint:
    return Checkpoint(
        checkpoint_id=str(row["id"]),
        task_id=str(row["task_id"]),
        run_id=str(row["run_id"]),
        sequence=int(row["sequence"]),
        kind=str(row["phase"]),
        payload=dict(row["state_json"]),
        created_at=row["created_at"],
        workflow_version=str(row["workflow_version"]),
    )


class PostgresRuntimeExecutionRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def create_run(self, context: RequestContext, run: RunSnapshot) -> RunSnapshot:
        if run.attempt < 1:
            raise ValueError("run attempt must be positive")
        async with self._session_factory() as session:
            async with session.begin():
                await session.execute(
                    text(
                        f"""
                        insert into task_runs (
                            id, task_id, attempt, status, started_at,
                            finished_at, error_summary
                        )
                        select
                            :run_id, t.id, :attempt, :status, :started_at,
                            :finished_at, :error_summary
                        from tasks t
                        where t.id = :task_id
                          and {_OWNER_TASK_SCOPE}
                        on conflict (id) do nothing
                        """
                    ),
                    {
                        **_scope(context),
                        "run_id": run.run_id,
                        "task_id": run.task_id,
                        "attempt": run.attempt,
                        "status": run.status.value,
                        "started_at": run.started_at,
                        "finished_at": run.finished_at,
                        "error_summary": run.error_summary,
                    },
                )
                loaded = await self._load_run(session, context, run.run_id)
                if loaded is None:
                    raise ValueError("task or run is outside the owner scope")
                if loaded.task_id != run.task_id or loaded.attempt != run.attempt:
                    raise ValueError("run id conflicts with an existing run")
                return loaded

    async def get_run(self, context: RequestContext, run_id: str) -> RunSnapshot | None:
        async with self._session_factory() as session:
            return await self._load_run(session, context, run_id)

    async def update_run_status(
        self,
        context: RequestContext,
        run_id: str,
        status: RunStatus,
        error_summary: str = "",
    ) -> RunSnapshot:
        async with self._session_factory() as session:
            async with session.begin():
                current = await self._load_run(session, context, run_id, for_update=True)
                if current is None:
                    raise ValueError("run not found in owner scope")
                ensure_run_transition(current.status, status)
                await session.execute(
                    text(
                        """
                        update task_runs
                        set status = :status,
                            started_at = case
                                when :status = 'running'
                                then coalesce(started_at, now())
                                else started_at
                            end,
                            finished_at = case
                                when :status in (
                                    'completed', 'degraded', 'failed',
                                    'cancelled'
                                )
                                then coalesce(finished_at, now())
                                else finished_at
                            end,
                            error_summary = :error_summary
                        where id = :run_id
                        """
                    ),
                    {
                        "run_id": run_id,
                        "status": status.value,
                        "error_summary": error_summary,
                    },
                )
                updated = await self._load_run(session, context, run_id)
                assert updated is not None
                return updated

    async def create_node(
        self, context: RequestContext, node: ExecutionNodeSnapshot
    ) -> ExecutionNodeSnapshot:
        async with self._session_factory() as session:
            async with session.begin():
                await session.execute(
                    text(
                        f"""
                        insert into execution_nodes (
                            id, run_id, node_key, node_type, status, attempt,
                            input_ref, output_ref, created_at, updated_at
                        )
                        select
                            :node_id, r.id, :node_key, :node_type, :status,
                            :attempt, :input_ref, :output_ref,
                            :created_at, :updated_at
                        from task_runs r
                        join tasks t on t.id = r.task_id
                        where r.id = :run_id
                          and {_OWNER_TASK_SCOPE}
                        on conflict (run_id, node_key, attempt) do nothing
                        """
                    ),
                    {
                        **_scope(context),
                        "node_id": node.node_id,
                        "run_id": node.run_id,
                        "node_key": node.node_key,
                        "node_type": node.node_type,
                        "status": node.status.value,
                        "attempt": node.attempt,
                        "input_ref": node.input_ref,
                        "output_ref": node.output_ref,
                        "created_at": node.created_at,
                        "updated_at": node.updated_at,
                    },
                )
                loaded = await self._load_node(session, context, node.node_id)
                if loaded is not None and (
                    loaded.run_id != node.run_id
                    or loaded.node_key != node.node_key
                    or loaded.attempt != node.attempt
                ):
                    raise ValueError("node id conflicts with an existing execution node")
                if loaded is None:
                    loaded = await self._load_node_by_key(
                        session,
                        context,
                        node.run_id,
                        node.node_key,
                        node.attempt,
                    )
                if loaded is None:
                    raise ValueError("run is outside the owner scope")
                return loaded

    async def list_nodes(self, context: RequestContext, run_id: str) -> list[ExecutionNodeSnapshot]:
        async with self._session_factory() as session:
            rows = (
                (
                    await session.execute(
                        text(
                            f"""
                        select
                            n.id, n.run_id, n.node_key, n.node_type,
                            n.status, n.attempt, n.input_ref, n.output_ref,
                            n.created_at, n.updated_at
                        from execution_nodes n
                        join task_runs r on r.id = n.run_id
                        join tasks t on t.id = r.task_id
                        where n.run_id = :run_id
                          and {_OWNER_TASK_SCOPE}
                        order by n.created_at, n.node_key, n.attempt
                        """
                        ),
                        {"run_id": run_id, **_scope(context)},
                    )
                )
                .mappings()
                .all()
            )
            return [_node_from_row(row) for row in rows]

    async def update_node_status(
        self,
        context: RequestContext,
        node_id: str,
        status: NodeStatus,
        *,
        output_ref: str | None = None,
    ) -> ExecutionNodeSnapshot:
        async with self._session_factory() as session:
            async with session.begin():
                current = await self._load_node(session, context, node_id, for_update=True)
                if current is None:
                    raise ValueError("node not found in owner scope")
                ensure_node_transition(current.status, status)
                await session.execute(
                    text(
                        """
                        update execution_nodes
                        set status = :status,
                            output_ref = coalesce(:output_ref, output_ref),
                            updated_at = now()
                        where id = :node_id
                        """
                    ),
                    {
                        "node_id": node_id,
                        "status": status.value,
                        "output_ref": output_ref,
                    },
                )
                updated = await self._load_node(session, context, node_id)
                assert updated is not None
                return updated

    async def _load_run(
        self,
        session: AsyncSession,
        context: RequestContext,
        run_id: str,
        *,
        for_update: bool = False,
    ) -> RunSnapshot | None:
        query = _RUN_SELECT + (" for update of r" if for_update else "")
        row = (
            (
                await session.execute(
                    text(query),
                    {"run_id": run_id, **_scope(context)},
                )
            )
            .mappings()
            .first()
        )
        return _run_from_row(row) if row else None

    async def _load_node(
        self,
        session: AsyncSession,
        context: RequestContext,
        node_id: str,
        *,
        for_update: bool = False,
    ) -> ExecutionNodeSnapshot | None:
        query = _NODE_SELECT + (" for update of n" if for_update else "")
        row = (
            (
                await session.execute(
                    text(query),
                    {"node_id": node_id, **_scope(context)},
                )
            )
            .mappings()
            .first()
        )
        return _node_from_row(row) if row else None

    async def _load_node_by_key(
        self,
        session: AsyncSession,
        context: RequestContext,
        run_id: str,
        node_key: str,
        attempt: int,
    ) -> ExecutionNodeSnapshot | None:
        row = (
            (
                await session.execute(
                    text(
                        f"""
                    select
                        n.id, n.run_id, n.node_key, n.node_type,
                        n.status, n.attempt, n.input_ref, n.output_ref,
                        n.created_at, n.updated_at
                    from execution_nodes n
                    join task_runs r on r.id = n.run_id
                    join tasks t on t.id = r.task_id
                    where n.run_id = :run_id
                      and n.node_key = :node_key
                      and n.attempt = :attempt
                      and {_OWNER_TASK_SCOPE}
                    """
                    ),
                    {
                        "run_id": run_id,
                        "node_key": node_key,
                        "attempt": attempt,
                        **_scope(context),
                    },
                )
            )
            .mappings()
            .first()
        )
        return _node_from_row(row) if row else None


class PostgresScopedCheckpointStore:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def save(self, context: RequestContext, checkpoint: Checkpoint) -> None:
        async with self._session_factory() as session:
            async with session.begin():
                result = await session.execute(
                    text(
                        f"""
                        insert into checkpoints (
                            id, run_id, sequence, workflow_version,
                            phase, state_json, created_at
                        )
                        select
                            :checkpoint_id, r.id, :sequence,
                            :workflow_version, :phase,
                            cast(:state_json as jsonb), :created_at
                        from task_runs r
                        join tasks t on t.id = r.task_id
                        where r.id = :run_id
                          and r.task_id = :task_id
                          and {_OWNER_TASK_SCOPE}
                        on conflict (id) do nothing
                        returning id
                        """
                    ),
                    {
                        **_scope(context),
                        "checkpoint_id": checkpoint.checkpoint_id,
                        "task_id": checkpoint.task_id,
                        "run_id": checkpoint.run_id,
                        "sequence": checkpoint.sequence,
                        "workflow_version": checkpoint.workflow_version,
                        "phase": checkpoint.kind,
                        "state_json": json.dumps(checkpoint.payload, ensure_ascii=False),
                        "created_at": checkpoint.created_at,
                    },
                )
                if result.first() is not None:
                    return
                existing = await self._load_by_id(session, context, checkpoint.checkpoint_id)
                if existing is None:
                    raise ValueError("task, run, or checkpoint is outside the owner scope")
                if (
                    existing.task_id != checkpoint.task_id
                    or existing.run_id != checkpoint.run_id
                    or existing.sequence != checkpoint.sequence
                ):
                    raise ValueError("checkpoint id conflicts with an existing checkpoint")

    async def latest(self, context: RequestContext, task_id: str, run_id: str) -> Checkpoint | None:
        async with self._session_factory() as session:
            row = (
                (
                    await session.execute(
                        text(_CHECKPOINT_SELECT + " order by c.sequence desc limit 1"),
                        {
                            "task_id": task_id,
                            "run_id": run_id,
                            **_scope(context),
                        },
                    )
                )
                .mappings()
                .first()
            )
            return _checkpoint_from_row(row) if row else None

    async def list(self, context: RequestContext, task_id: str, run_id: str) -> list[Checkpoint]:
        async with self._session_factory() as session:
            rows = (
                (
                    await session.execute(
                        text(_CHECKPOINT_SELECT + " order by c.sequence"),
                        {
                            "task_id": task_id,
                            "run_id": run_id,
                            **_scope(context),
                        },
                    )
                )
                .mappings()
                .all()
            )
            return [_checkpoint_from_row(row) for row in rows]

    async def latest_for_task(self, context: RequestContext, task_id: str) -> Checkpoint | None:
        async with self._session_factory() as session:
            row = (
                (
                    await session.execute(
                        text(
                            f"""
                            select
                                c.id, r.task_id, c.run_id, c.sequence, c.phase,
                                c.state_json, c.workflow_version, c.created_at
                            from checkpoints c
                            join task_runs r on r.id = c.run_id
                            join tasks t on t.id = r.task_id
                            where r.task_id = :task_id
                              and {_OWNER_TASK_SCOPE}
                            order by r.attempt desc, c.sequence desc
                            limit 1
                            """
                        ),
                        {"task_id": task_id, **_scope(context)},
                    )
                )
                .mappings()
                .first()
            )
        return _checkpoint_from_row(row) if row else None

    async def _load_by_id(
        self,
        session: AsyncSession,
        context: RequestContext,
        checkpoint_id: str,
    ) -> Checkpoint | None:
        row = (
            (
                await session.execute(
                    text(
                        f"""
                    select
                        c.id, r.task_id, c.run_id, c.sequence, c.phase,
                        c.state_json, c.workflow_version, c.created_at
                    from checkpoints c
                    join task_runs r on r.id = c.run_id
                    join tasks t on t.id = r.task_id
                    where c.id = :checkpoint_id
                      and {_OWNER_TASK_SCOPE}
                    """
                    ),
                    {
                        "checkpoint_id": checkpoint_id,
                        **_scope(context),
                    },
                )
            )
            .mappings()
            .first()
        )
        return _checkpoint_from_row(row) if row else None
