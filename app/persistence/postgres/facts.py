from __future__ import annotations

import json
import uuid
from collections.abc import Mapping
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.enums import WorkflowType
from app.domain.models import (
    AgentRunSnapshot,
    CompletionState,
    EvidenceSnapshot,
    RequestContext,
    ResultSnapshot,
    RuntimeEventSnapshot,
    ToolCallSnapshot,
)


_OWNER_SCOPE = "t.workspace_id = :workspace_id and t.owner_user_id = :user_id"
_READ_SCOPE = """
t.workspace_id = :workspace_id
and (
    t.owner_user_id = :user_id
    or exists (
        select 1 from resource_shares s
        where s.workspace_id=t.workspace_id
          and s.resource_type='task'
          and s.resource_id=t.id
          and s.permission in ('read', 'comment')
          and (
              (s.grantee_type='user' and s.grantee_id=:user_id)
              or (
                  s.grantee_type='workspace'
                  and s.grantee_id=:workspace_id
              )
          )
    )
)
"""
_AGENT_STATUSES = {"created", "running", "completed", "blocked", "failed", "cancelled"}


def _scope(context: RequestContext) -> dict[str, str]:
    return {"workspace_id": context.workspace_id, "user_id": context.user_id}


def _agent(row: Mapping[str, Any]) -> AgentRunSnapshot:
    return AgentRunSnapshot(
        agent_run_id=str(row["id"]),
        run_id=str(row["run_id"]),
        role=str(row["role"]),
        goal=str(row["goal"]),
        status=str(row["status"]),
        capability_snapshot=dict(row["capability_snapshot"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _event(row: Mapping[str, Any]) -> RuntimeEventSnapshot:
    return RuntimeEventSnapshot(
        event_id=str(row["id"]),
        task_id=str(row["task_id"]),
        run_id=row["run_id"],
        agent_run_id=row["agent_run_id"],
        sequence=int(row["sequence"]),
        event_type=str(row["type"]),
        payload=dict(row["payload"]),
        summary=str(row["summary"] or ""),
        created_at=row["created_at"],
    )


def _tool(row: Mapping[str, Any]) -> ToolCallSnapshot:
    return ToolCallSnapshot(
        tool_call_id=str(row["id"]),
        task_id=str(row["task_id"]),
        agent_run_id=row["agent_run_id"],
        tool_key=str(row["tool_key"]),
        status=str(row["status"]),
        arguments=dict(row["arguments"]),
        result_ref=row["result_ref"],
        error_summary=str(row["error_summary"] or ""),
        created_at=row["created_at"],
        completed_at=row["completed_at"],
    )


def _evidence(row: Mapping[str, Any]) -> EvidenceSnapshot:
    return EvidenceSnapshot(
        evidence_id=str(row["id"]),
        task_id=str(row["task_id"]),
        source_type=str(row["source_type"]),
        source_ref=str(row["source_ref"]),
        locator=dict(row["locator"]),
        title=str(row["title"]),
        snippet=str(row["snippet"]),
        checksum=row["checksum"],
        rank=row["rank"],
        created_at=row["created_at"],
    )


def _result(row: Mapping[str, Any]) -> ResultSnapshot:
    return ResultSnapshot(
        result_id=str(row["id"]),
        task_id=str(row["task_id"]),
        answer=str(row["answer"]),
        completion_state=CompletionState(row["completion_state"]),
        citations=tuple(row["citations"]),
        limitations=tuple(str(item) for item in row["limitations"]),
        confidence=float(row["confidence"]) if row["confidence"] is not None else None,
        used_workflow=WorkflowType(row["used_workflow"]),
        created_at=row["created_at"],
    )


class PostgresRuntimeFactRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def create_agent_run(
        self, context: RequestContext, agent: AgentRunSnapshot
    ) -> AgentRunSnapshot:
        if agent.status not in _AGENT_STATUSES:
            raise ValueError("unsupported agent status")
        async with self._session_factory() as session:
            async with session.begin():
                await session.execute(
                    text(
                        f"""
                        insert into agent_runs (
                            id, run_id, role, goal, capability_snapshot,
                            status, created_at, updated_at
                        )
                        select :id, r.id, :role, :goal, cast(:capabilities as jsonb),
                               :status, :created_at, :updated_at
                        from task_runs r join tasks t on t.id = r.task_id
                        where r.id = :run_id and {_OWNER_SCOPE}
                        on conflict (id) do nothing
                        """
                    ),
                    {
                        **_scope(context),
                        "id": agent.agent_run_id,
                        "run_id": agent.run_id,
                        "role": agent.role,
                        "goal": agent.goal,
                        "capabilities": json.dumps(agent.capability_snapshot),
                        "status": agent.status,
                        "created_at": agent.created_at,
                        "updated_at": agent.updated_at,
                    },
                )
                loaded = await self._load_agent(session, context, agent.agent_run_id)
                if loaded is None:
                    raise ValueError("run is outside the owner scope")
                if loaded.run_id != agent.run_id:
                    raise ValueError("agent id conflicts with an existing agent run")
                return loaded

    async def update_agent_status(
        self, context: RequestContext, agent_run_id: str, status: str
    ) -> AgentRunSnapshot:
        if status not in _AGENT_STATUSES:
            raise ValueError("unsupported agent status")
        async with self._session_factory() as session:
            async with session.begin():
                current = await self._load_agent(session, context, agent_run_id, for_update=True)
                if current is None:
                    raise ValueError("agent run not found in owner scope")
                await session.execute(
                    text("update agent_runs set status=:status, updated_at=now() where id=:id"),
                    {"id": agent_run_id, "status": status},
                )
                loaded = await self._load_agent(session, context, agent_run_id)
                assert loaded is not None
                return loaded

    async def list_agent_runs(
        self, context: RequestContext, task_id: str
    ) -> list[AgentRunSnapshot]:
        async with self._session_factory() as session:
            rows = (
                (
                    await session.execute(
                        text(
                            f"""
                        select a.id, a.run_id, a.role, a.goal,
                               a.capability_snapshot, a.status,
                               a.created_at, a.updated_at
                        from agent_runs a
                        join task_runs r on r.id=a.run_id
                        join tasks t on t.id=r.task_id
                        where t.id=:task_id and {_READ_SCOPE}
                        order by a.created_at, a.id
                        """
                        ),
                        {"task_id": task_id, **_scope(context)},
                    )
                )
                .mappings()
                .all()
            )
            return [_agent(row) for row in rows]

    async def append_event(
        self,
        context: RequestContext,
        *,
        task_id: str,
        event_type: str,
        payload: Mapping[str, Any],
        summary: str = "",
        run_id: str | None = None,
        agent_run_id: str | None = None,
    ) -> RuntimeEventSnapshot:
        async with self._session_factory() as session:
            async with session.begin():
                task = (
                    await session.execute(
                        text(
                            f"select t.id from tasks t where t.id=:task_id "
                            f"and {_OWNER_SCOPE} for update"
                        ),
                        {"task_id": task_id, **_scope(context)},
                    )
                ).first()
                if task is None:
                    raise ValueError("task not found in owner scope")
                await self._validate_links(session, task_id, run_id, agent_run_id)
                sequence = int(
                    (
                        await session.execute(
                            text(
                                "select coalesce(max(sequence), 0) + 1 "
                                "from events where task_id=:task_id"
                            ),
                            {"task_id": task_id},
                        )
                    ).scalar_one()
                )
                row = (
                    (
                        await session.execute(
                            text(
                                """
                            insert into events (
                                id, task_id, run_id, agent_run_id,
                                sequence, type, payload, summary
                            ) values (
                                :id, :task_id, :run_id, :agent_run_id,
                                :sequence, :type, cast(:payload as jsonb), :summary
                            )
                            returning id, task_id, run_id, agent_run_id,
                                      sequence, type, payload, summary, created_at
                            """
                            ),
                            {
                                "id": f"event_{uuid.uuid4().hex}",
                                "task_id": task_id,
                                "run_id": run_id,
                                "agent_run_id": agent_run_id,
                                "sequence": sequence,
                                "type": event_type,
                                "payload": json.dumps(payload, ensure_ascii=False),
                                "summary": summary,
                            },
                        )
                    )
                    .mappings()
                    .one()
                )
                return _event(row)

    async def list_events(
        self, context: RequestContext, task_id: str
    ) -> list[RuntimeEventSnapshot]:
        async with self._session_factory() as session:
            rows = (
                (
                    await session.execute(
                        text(
                            f"""
                        select e.id, e.task_id, e.run_id, e.agent_run_id,
                               e.sequence, e.type, e.payload, e.summary, e.created_at
                        from events e join tasks t on t.id=e.task_id
                        where t.id=:task_id and {_READ_SCOPE}
                        order by e.sequence
                        """
                        ),
                        {"task_id": task_id, **_scope(context)},
                    )
                )
                .mappings()
                .all()
            )
            return [_event(row) for row in rows]

    async def save_tool_call(
        self, context: RequestContext, call: ToolCallSnapshot
    ) -> ToolCallSnapshot:
        async with self._session_factory() as session:
            async with session.begin():
                if not await self._owns_task(session, context, call.task_id):
                    raise ValueError("task not found in owner scope")
                if call.agent_run_id:
                    await self._validate_agent(session, call.task_id, call.agent_run_id)
                await session.execute(
                    text(
                        """
                        insert into tool_calls (
                            id, task_id, agent_run_id, tool_key, status,
                            arguments, result_ref, error_summary,
                            created_at, completed_at
                        ) values (
                            :id, :task_id, :agent_run_id, :tool_key, :status,
                            cast(:arguments as jsonb), :result_ref, :error_summary,
                            :created_at, :completed_at
                        )
                        on conflict (id) do update set
                            status=excluded.status,
                            result_ref=excluded.result_ref,
                            error_summary=excluded.error_summary,
                            completed_at=excluded.completed_at
                        """
                    ),
                    {
                        "id": call.tool_call_id,
                        "task_id": call.task_id,
                        "agent_run_id": call.agent_run_id,
                        "tool_key": call.tool_key,
                        "status": call.status,
                        "arguments": json.dumps(call.arguments, ensure_ascii=False),
                        "result_ref": call.result_ref,
                        "error_summary": call.error_summary,
                        "created_at": call.created_at,
                        "completed_at": call.completed_at,
                    },
                )
                loaded = await self._load_tool(session, context, call.tool_call_id)
                assert loaded is not None
                return loaded

    async def list_tool_calls(
        self, context: RequestContext, task_id: str
    ) -> list[ToolCallSnapshot]:
        async with self._session_factory() as session:
            rows = (
                (
                    await session.execute(
                        text(
                            f"""
                        select c.id, c.task_id, c.agent_run_id, c.tool_key,
                               c.status, c.arguments, c.result_ref,
                               c.error_summary, c.created_at, c.completed_at
                        from tool_calls c join tasks t on t.id=c.task_id
                        where t.id=:task_id and {_READ_SCOPE}
                        order by c.created_at, c.id
                        """
                        ),
                        {"task_id": task_id, **_scope(context)},
                    )
                )
                .mappings()
                .all()
            )
            return [_tool(row) for row in rows]

    async def save_evidence(
        self, context: RequestContext, evidence: EvidenceSnapshot
    ) -> EvidenceSnapshot:
        async with self._session_factory() as session:
            async with session.begin():
                await session.execute(
                    text(
                        f"""
                        insert into evidence (
                            id, task_id, source_type, source_ref, locator,
                            title, snippet, checksum, rank, created_at
                        )
                        select :id, t.id, :source_type, :source_ref,
                               cast(:locator as jsonb), :title, :snippet,
                               :checksum, :rank, :created_at
                        from tasks t where t.id=:task_id and {_OWNER_SCOPE}
                        on conflict (id) do nothing
                        """
                    ),
                    {
                        **_scope(context),
                        "id": evidence.evidence_id,
                        "task_id": evidence.task_id,
                        "source_type": evidence.source_type,
                        "source_ref": evidence.source_ref,
                        "locator": json.dumps(evidence.locator, ensure_ascii=False),
                        "title": evidence.title,
                        "snippet": evidence.snippet,
                        "checksum": evidence.checksum,
                        "rank": evidence.rank,
                        "created_at": evidence.created_at,
                    },
                )
                loaded = await self._load_evidence(session, context, evidence.evidence_id)
                if loaded is None:
                    raise ValueError("task is outside the owner scope")
                if loaded.task_id != evidence.task_id:
                    raise ValueError("evidence id conflicts with existing evidence")
                return loaded

    async def list_evidence(self, context: RequestContext, task_id: str) -> list[EvidenceSnapshot]:
        async with self._session_factory() as session:
            rows = (
                (
                    await session.execute(
                        text(
                            f"""
                        select e.id, e.task_id, e.source_type, e.source_ref,
                               e.locator, e.title, e.snippet, e.checksum,
                               e.rank, e.created_at
                        from evidence e join tasks t on t.id=e.task_id
                        where t.id=:task_id and {_READ_SCOPE}
                        order by e.rank nulls last, e.created_at, e.id
                        """
                        ),
                        {"task_id": task_id, **_scope(context)},
                    )
                )
                .mappings()
                .all()
            )
            return [_evidence(row) for row in rows]

    async def save_result(self, context: RequestContext, result: ResultSnapshot) -> ResultSnapshot:
        async with self._session_factory() as session:
            async with session.begin():
                row = (
                    (
                        await session.execute(
                            text(
                                f"""
                            insert into results (
                                id, task_id, answer, completion_state,
                                citations, limitations, confidence,
                                used_workflow, created_at
                            )
                            select :id, t.id, :answer, :completion_state,
                                   cast(:citations as jsonb),
                                   cast(:limitations as jsonb), :confidence,
                                   :workflow, :created_at
                            from tasks t where t.id=:task_id and {_OWNER_SCOPE}
                            on conflict (task_id) do update set
                                answer=excluded.answer,
                                completion_state=excluded.completion_state,
                                citations=excluded.citations,
                                limitations=excluded.limitations,
                                confidence=excluded.confidence,
                                used_workflow=excluded.used_workflow
                            returning id, task_id, answer, completion_state,
                                      citations, limitations, confidence,
                                      used_workflow, created_at
                            """
                            ),
                            {
                                **_scope(context),
                                "id": result.result_id,
                                "task_id": result.task_id,
                                "answer": result.answer,
                                "completion_state": result.completion_state.value,
                                "citations": json.dumps(result.citations),
                                "limitations": json.dumps(result.limitations),
                                "confidence": result.confidence,
                                "workflow": result.used_workflow.value,
                                "created_at": result.created_at,
                            },
                        )
                    )
                    .mappings()
                    .first()
                )
                if row is None:
                    raise ValueError("task is outside the owner scope")
                return _result(row)

    async def get_result(self, context: RequestContext, task_id: str) -> ResultSnapshot | None:
        async with self._session_factory() as session:
            row = (
                (
                    await session.execute(
                        text(
                            f"""
                        select r.id, r.task_id, r.answer, r.completion_state,
                               r.citations, r.limitations, r.confidence,
                               r.used_workflow, r.created_at
                        from results r join tasks t on t.id=r.task_id
                        where t.id=:task_id and {_READ_SCOPE}
                        """
                        ),
                        {"task_id": task_id, **_scope(context)},
                    )
                )
                .mappings()
                .first()
            )
            return _result(row) if row else None

    async def _load_agent(
        self,
        session: AsyncSession,
        context: RequestContext,
        agent_id: str,
        *,
        for_update: bool = False,
    ) -> AgentRunSnapshot | None:
        query = f"""
            select a.id, a.run_id, a.role, a.goal, a.capability_snapshot,
                   a.status, a.created_at, a.updated_at
            from agent_runs a
            join task_runs r on r.id=a.run_id
            join tasks t on t.id=r.task_id
            where a.id=:id and {_OWNER_SCOPE}
        """
        if for_update:
            query += " for update of a"
        row = (
            (await session.execute(text(query), {"id": agent_id, **_scope(context)}))
            .mappings()
            .first()
        )
        return _agent(row) if row else None

    async def _load_tool(
        self, session: AsyncSession, context: RequestContext, call_id: str
    ) -> ToolCallSnapshot | None:
        row = (
            (
                await session.execute(
                    text(
                        f"""
                    select c.id, c.task_id, c.agent_run_id, c.tool_key,
                           c.status, c.arguments, c.result_ref,
                           c.error_summary, c.created_at, c.completed_at
                    from tool_calls c join tasks t on t.id=c.task_id
                    where c.id=:id and {_OWNER_SCOPE}
                    """
                    ),
                    {"id": call_id, **_scope(context)},
                )
            )
            .mappings()
            .first()
        )
        return _tool(row) if row else None

    async def _load_evidence(
        self, session: AsyncSession, context: RequestContext, evidence_id: str
    ) -> EvidenceSnapshot | None:
        row = (
            (
                await session.execute(
                    text(
                        f"""
                    select e.id, e.task_id, e.source_type, e.source_ref,
                           e.locator, e.title, e.snippet, e.checksum,
                           e.rank, e.created_at
                    from evidence e join tasks t on t.id=e.task_id
                    where e.id=:id and {_OWNER_SCOPE}
                    """
                    ),
                    {"id": evidence_id, **_scope(context)},
                )
            )
            .mappings()
            .first()
        )
        return _evidence(row) if row else None

    async def _owns_task(
        self, session: AsyncSession, context: RequestContext, task_id: str
    ) -> bool:
        row = (
            await session.execute(
                text(f"select 1 from tasks t where t.id=:task_id and {_OWNER_SCOPE}"),
                {"task_id": task_id, **_scope(context)},
            )
        ).first()
        return row is not None

    async def _validate_links(
        self,
        session: AsyncSession,
        task_id: str,
        run_id: str | None,
        agent_id: str | None,
    ) -> None:
        if run_id is not None:
            row = (
                await session.execute(
                    text("select 1 from task_runs where id=:run_id and task_id=:task_id"),
                    {"run_id": run_id, "task_id": task_id},
                )
            ).first()
            if row is None:
                raise ValueError("event run does not belong to task")
        if agent_id is not None:
            await self._validate_agent(session, task_id, agent_id)

    @staticmethod
    async def _validate_agent(session: AsyncSession, task_id: str, agent_id: str) -> None:
        row = (
            await session.execute(
                text(
                    """
                    select 1 from agent_runs a
                    join task_runs r on r.id=a.run_id
                    where a.id=:agent_id and r.task_id=:task_id
                    """
                ),
                {"agent_id": agent_id, "task_id": task_id},
            )
        ).first()
        if row is None:
            raise ValueError("agent run does not belong to task")
