from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.models import (
    ArtifactSnapshot,
    InputFileSnapshot,
    LLMCallSnapshot,
    RequestContext,
)


_OWNER_SCOPE = "t.workspace_id = :workspace_id and t.owner_user_id = :user_id"
_TASK_READ_SCOPE = """
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
_PARSE_STATUSES = {"pending", "processing", "completed", "failed"}


def _scope(context: RequestContext) -> dict[str, str]:
    return {"workspace_id": context.workspace_id, "user_id": context.user_id}


def _input_file(row: Mapping[str, Any]) -> InputFileSnapshot:
    return InputFileSnapshot(
        input_file_id=str(row["id"]),
        workspace_id=str(row["workspace_id"]),
        owner_user_id=str(row["owner_user_id"]),
        task_id=row["task_id"],
        object_key=str(row["object_key"]),
        filename=str(row["filename"]),
        media_type=str(row["media_type"]),
        size_bytes=int(row["size_bytes"]),
        sha256=str(row["sha256"]),
        parse_status=str(row["parse_status"]),
        parse_error=str(row["parse_error"] or ""),
        created_at=row["created_at"],
    )


def _artifact(row: Mapping[str, Any]) -> ArtifactSnapshot:
    return ArtifactSnapshot(
        artifact_id=str(row["id"]),
        task_id=str(row["task_id"]),
        filename=str(row["filename"]),
        media_type=str(row["media_type"]),
        size_bytes=int(row["size_bytes"]),
        relative_path=str(row["object_key"]),
        created_at=row["created_at"],
        workspace_id=str(row["workspace_id"]),
        owner_user_id=str(row["owner_user_id"]),
        object_key=str(row["object_key"]),
        logical_name=str(row["logical_name"]),
        sha256=str(row["sha256"]),
        version=int(row["version"]),
    )


def _llm_call(row: Mapping[str, Any]) -> LLMCallSnapshot:
    return LLMCallSnapshot(
        llm_call_id=str(row["id"]),
        task_id=str(row["task_id"]),
        agent_run_id=row["agent_run_id"],
        model_variant_id=row["model_variant_id"],
        request_tokens=int(row["request_tokens"]),
        response_tokens=int(row["response_tokens"]),
        cost=float(row["cost"]),
        latency_ms=row["latency_ms"],
        status=str(row["status"]),
        fallback_from=row["fallback_from"],
        error_category=row["error_category"],
        created_at=row["created_at"],
    )


class PostgresRuntimeAssetRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def save_input_file(
        self, context: RequestContext, file: InputFileSnapshot
    ) -> InputFileSnapshot:
        self._validate_file_scope(context, file)
        if file.parse_status not in _PARSE_STATUSES:
            raise ValueError("unsupported parse status")
        async with self._session_factory() as session:
            async with session.begin():
                if not await self._active_member(session, context):
                    raise PermissionError("active workspace membership required")
                if file.task_id is not None and not await self._owns_task(
                    session, context, file.task_id
                ):
                    raise ValueError("task not found in owner scope")
                await session.execute(
                    text(
                        """
                        insert into input_files (
                            id, workspace_id, owner_user_id, task_id,
                            object_key, filename, media_type, size_bytes,
                            sha256, parse_status, parse_error, created_at
                        ) values (
                            :id, :workspace_id, :owner_user_id, :task_id,
                            :object_key, :filename, :media_type, :size_bytes,
                            :sha256, :parse_status, :parse_error, :created_at
                        )
                        on conflict (id) do nothing
                        """
                    ),
                    {
                        "id": file.input_file_id,
                        "workspace_id": file.workspace_id,
                        "owner_user_id": file.owner_user_id,
                        "task_id": file.task_id,
                        "object_key": file.object_key,
                        "filename": file.filename,
                        "media_type": file.media_type,
                        "size_bytes": file.size_bytes,
                        "sha256": file.sha256,
                        "parse_status": file.parse_status,
                        "parse_error": file.parse_error,
                        "created_at": file.created_at,
                    },
                )
                loaded = await self._load_input_file(session, context, file.input_file_id)
                if loaded is None:
                    raise ValueError("input file id conflicts outside owner scope")
                if loaded.object_key != file.object_key or loaded.task_id != file.task_id:
                    raise ValueError("input file id conflicts with existing file")
                return loaded

    async def update_parse_status(
        self,
        context: RequestContext,
        input_file_id: str,
        status: str,
        error: str = "",
    ) -> InputFileSnapshot:
        if status not in _PARSE_STATUSES:
            raise ValueError("unsupported parse status")
        async with self._session_factory() as session:
            async with session.begin():
                result = await session.execute(
                    text(
                        """
                        update input_files
                        set parse_status=:status, parse_error=:error
                        where id=:id and workspace_id=:workspace_id
                          and owner_user_id=:user_id
                        """
                    ),
                    {
                        "id": input_file_id,
                        "status": status,
                        "error": error,
                        **_scope(context),
                    },
                )
                if result.rowcount != 1:
                    raise ValueError("input file not found in owner scope")
                loaded = await self._load_input_file(session, context, input_file_id)
                assert loaded is not None
                return loaded

    async def list_input_files(
        self, context: RequestContext, task_id: str | None = None
    ) -> list[InputFileSnapshot]:
        clause = "and task_id=:task_id" if task_id is not None else ""
        async with self._session_factory() as session:
            rows = (
                (
                    await session.execute(
                        text(
                            f"""
                        select id, workspace_id, owner_user_id, task_id,
                               object_key, filename, media_type, size_bytes,
                               sha256, parse_status, parse_error, created_at
                        from input_files
                        where workspace_id=:workspace_id
                          and owner_user_id=:user_id
                          {clause}
                        order by created_at, id
                        """
                        ),
                        {"task_id": task_id, **_scope(context)},
                    )
                )
                .mappings()
                .all()
            )
            return [_input_file(row) for row in rows]

    async def attach_input_files(
        self,
        context: RequestContext,
        task_id: str,
        input_file_ids: list[str],
    ) -> None:
        unique_ids = list(dict.fromkeys(input_file_ids))
        if not unique_ids:
            return
        async with self._session_factory() as session:
            async with session.begin():
                owns_task = await self._owns_task(session, context, task_id)
                if not owns_task:
                    raise ValueError("task not found in owner scope")
                result = await session.execute(
                    text(
                        """
                        update input_files
                        set task_id=:task_id
                        where id = any(:file_ids)
                          and workspace_id=:workspace_id
                          and owner_user_id=:user_id
                          and (task_id is null or task_id=:task_id)
                        """
                    ),
                    {
                        "task_id": task_id,
                        "file_ids": unique_ids,
                        **_scope(context),
                    },
                )
                if result.rowcount != len(unique_ids):
                    raise ValueError(
                        "one or more input files are missing, owned by another "
                        "user, or attached to another task"
                    )

    async def save_artifact(
        self, context: RequestContext, artifact: ArtifactSnapshot
    ) -> ArtifactSnapshot:
        self._validate_artifact_scope(context, artifact)
        if artifact.object_key is None or artifact.sha256 is None:
            raise ValueError("artifact object_key and sha256 are required")
        async with self._session_factory() as session:
            async with session.begin():
                if not await self._owns_task(session, context, artifact.task_id):
                    raise ValueError("task not found in owner scope")
                await session.execute(
                    text(
                        """
                        insert into artifacts (
                            id, workspace_id, owner_user_id, task_id,
                            object_key, logical_name, filename, media_type,
                            size_bytes, sha256, version, created_at
                        ) values (
                            :id, :workspace_id, :owner_user_id, :task_id,
                            :object_key, :logical_name, :filename, :media_type,
                            :size_bytes, :sha256, :version, :created_at
                        )
                        on conflict (id) do nothing
                        """
                    ),
                    {
                        "id": artifact.artifact_id,
                        "workspace_id": context.workspace_id,
                        "owner_user_id": context.user_id,
                        "task_id": artifact.task_id,
                        "object_key": artifact.object_key,
                        "logical_name": (artifact.logical_name or artifact.filename),
                        "filename": artifact.filename,
                        "media_type": artifact.media_type,
                        "size_bytes": artifact.size_bytes,
                        "sha256": artifact.sha256,
                        "version": artifact.version,
                        "created_at": artifact.created_at,
                    },
                )
                loaded = await self._load_artifact(session, context, artifact.artifact_id)
                if loaded is None:
                    raise ValueError("artifact id conflicts outside owner scope")
                if loaded.task_id != artifact.task_id or loaded.object_key != artifact.object_key:
                    raise ValueError("artifact id conflicts with existing artifact")
                return loaded

    async def get_artifact(
        self, context: RequestContext, artifact_id: str
    ) -> ArtifactSnapshot | None:
        async with self._session_factory() as session:
            return await self._read_artifact(session, context, artifact_id)

    async def list_artifacts(self, context: RequestContext, task_id: str) -> list[ArtifactSnapshot]:
        async with self._session_factory() as session:
            rows = (
                (
                    await session.execute(
                        text(
                            f"""
                        select a.id, a.workspace_id, a.owner_user_id, a.task_id,
                               a.object_key, a.logical_name, a.filename,
                               a.media_type, a.size_bytes, a.sha256, a.version,
                               a.created_at
                        from artifacts a
                        join tasks t on t.id=a.task_id
                        where a.task_id=:task_id
                          and {_TASK_READ_SCOPE}
                        order by a.logical_name, a.version, a.created_at
                        """
                        ),
                        {"task_id": task_id, **_scope(context)},
                    )
                )
                .mappings()
                .all()
            )
            return [_artifact(row) for row in rows]

    async def record_llm_call(
        self, context: RequestContext, call: LLMCallSnapshot
    ) -> LLMCallSnapshot:
        async with self._session_factory() as session:
            async with session.begin():
                if not await self._owns_task(session, context, call.task_id):
                    raise ValueError("task not found in owner scope")
                if call.agent_run_id is not None:
                    await self._validate_agent(session, call.task_id, call.agent_run_id)
                await self._validate_models(
                    session,
                    context,
                    call.model_variant_id,
                    call.fallback_from,
                )
                await session.execute(
                    text(
                        """
                        insert into llm_calls (
                            id, task_id, agent_run_id, model_variant_id,
                            request_tokens, response_tokens, cost,
                            latency_ms, status, fallback_from,
                            error_category, created_at
                        ) values (
                            :id, :task_id, :agent_run_id, :model_variant_id,
                            :request_tokens, :response_tokens, :cost,
                            :latency_ms, :status, :fallback_from,
                            :error_category, :created_at
                        )
                        on conflict (id) do nothing
                        """
                    ),
                    {
                        "id": call.llm_call_id,
                        "task_id": call.task_id,
                        "agent_run_id": call.agent_run_id,
                        "model_variant_id": call.model_variant_id,
                        "request_tokens": call.request_tokens,
                        "response_tokens": call.response_tokens,
                        "cost": call.cost,
                        "latency_ms": call.latency_ms,
                        "status": call.status,
                        "fallback_from": call.fallback_from,
                        "error_category": call.error_category,
                        "created_at": call.created_at,
                    },
                )
                loaded = await self._load_llm_call(session, context, call.llm_call_id)
                if loaded is None:
                    raise ValueError("LLM call id conflicts outside owner scope")
                if loaded.task_id != call.task_id:
                    raise ValueError("LLM call id conflicts with existing call")
                return loaded

    async def list_llm_calls(self, context: RequestContext, task_id: str) -> list[LLMCallSnapshot]:
        async with self._session_factory() as session:
            rows = (
                (
                    await session.execute(
                        text(
                            f"""
                        select c.id, c.task_id, c.agent_run_id,
                               c.model_variant_id, c.request_tokens,
                               c.response_tokens, c.cost, c.latency_ms,
                               c.status, c.fallback_from, c.error_category,
                               c.created_at
                        from llm_calls c join tasks t on t.id=c.task_id
                        where t.id=:task_id and {_OWNER_SCOPE}
                        order by c.created_at, c.id
                        """
                        ),
                        {"task_id": task_id, **_scope(context)},
                    )
                )
                .mappings()
                .all()
            )
            return [_llm_call(row) for row in rows]

    async def _load_input_file(
        self, session: AsyncSession, context: RequestContext, file_id: str
    ) -> InputFileSnapshot | None:
        row = (
            (
                await session.execute(
                    text(
                        """
                    select id, workspace_id, owner_user_id, task_id,
                           object_key, filename, media_type, size_bytes,
                           sha256, parse_status, parse_error, created_at
                    from input_files
                    where id=:id and workspace_id=:workspace_id
                      and owner_user_id=:user_id
                    """
                    ),
                    {"id": file_id, **_scope(context)},
                )
            )
            .mappings()
            .first()
        )
        return _input_file(row) if row else None

    async def _read_artifact(
        self,
        session: AsyncSession,
        context: RequestContext,
        artifact_id: str,
    ) -> ArtifactSnapshot | None:
        row = (
            (
                await session.execute(
                    text(
                        f"""
                    select a.id, a.workspace_id, a.owner_user_id, a.task_id,
                           a.object_key, a.logical_name, a.filename,
                           a.media_type, a.size_bytes, a.sha256, a.version,
                           a.created_at
                    from artifacts a
                    join tasks t on t.id=a.task_id
                    where a.id=:id and {_TASK_READ_SCOPE}
                    """
                    ),
                    {"id": artifact_id, **_scope(context)},
                )
            )
            .mappings()
            .first()
        )
        return _artifact(row) if row else None

    async def _load_artifact(
        self, session: AsyncSession, context: RequestContext, artifact_id: str
    ) -> ArtifactSnapshot | None:
        row = (
            (
                await session.execute(
                    text(
                        """
                    select id, workspace_id, owner_user_id, task_id,
                           object_key, logical_name, filename, media_type,
                           size_bytes, sha256, version, created_at
                    from artifacts
                    where id=:id and workspace_id=:workspace_id
                      and owner_user_id=:user_id
                    """
                    ),
                    {"id": artifact_id, **_scope(context)},
                )
            )
            .mappings()
            .first()
        )
        return _artifact(row) if row else None

    async def _load_llm_call(
        self, session: AsyncSession, context: RequestContext, call_id: str
    ) -> LLMCallSnapshot | None:
        row = (
            (
                await session.execute(
                    text(
                        f"""
                    select c.id, c.task_id, c.agent_run_id,
                           c.model_variant_id, c.request_tokens,
                           c.response_tokens, c.cost, c.latency_ms,
                           c.status, c.fallback_from, c.error_category,
                           c.created_at
                    from llm_calls c join tasks t on t.id=c.task_id
                    where c.id=:id and {_OWNER_SCOPE}
                    """
                    ),
                    {"id": call_id, **_scope(context)},
                )
            )
            .mappings()
            .first()
        )
        return _llm_call(row) if row else None

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

    @staticmethod
    async def _active_member(session: AsyncSession, context: RequestContext) -> bool:
        row = (
            await session.execute(
                text(
                    """
                    select 1 from workspace_memberships
                    where workspace_id=:workspace_id and user_id=:user_id
                      and status='active'
                    """
                ),
                _scope(context),
            )
        ).first()
        return row is not None

    @staticmethod
    async def _validate_agent(session: AsyncSession, task_id: str, agent_id: str) -> None:
        row = (
            await session.execute(
                text(
                    """
                    select 1 from agent_runs a join task_runs r on r.id=a.run_id
                    where a.id=:agent_id and r.task_id=:task_id
                    """
                ),
                {"agent_id": agent_id, "task_id": task_id},
            )
        ).first()
        if row is None:
            raise ValueError("agent run does not belong to task")

    @staticmethod
    async def _validate_models(
        session: AsyncSession,
        context: RequestContext,
        model_id: str | None,
        fallback_id: str | None,
    ) -> None:
        for value in {model_id, fallback_id} - {None}:
            row = (
                await session.execute(
                    text(
                        """
                        select 1 from model_variants v
                        join model_connections c on c.id=v.connection_id
                        where v.id=:model_id and c.workspace_id=:workspace_id
                        """
                    ),
                    {"model_id": value, "workspace_id": context.workspace_id},
                )
            ).first()
            if row is None:
                raise ValueError("model variant is outside workspace scope")

    @staticmethod
    def _validate_file_scope(context: RequestContext, file: InputFileSnapshot) -> None:
        if file.workspace_id != context.workspace_id or file.owner_user_id != context.user_id:
            raise PermissionError("input file scope does not match actor")

    @staticmethod
    def _validate_artifact_scope(context: RequestContext, artifact: ArtifactSnapshot) -> None:
        if (
            artifact.workspace_id != context.workspace_id
            or artifact.owner_user_id != context.user_id
        ):
            raise PermissionError("artifact scope does not match actor")
