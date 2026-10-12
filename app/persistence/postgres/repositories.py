from __future__ import annotations

import json
import uuid
from collections.abc import Mapping
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.application.authorization import AuthorizationPolicy
from app.application.ports.scoped_repositories import GovernanceTaskProjection
from app.core.enums import TaskStatus, WorkflowType
from app.domain.identity import (
    AccessKind,
    Capability,
    GranteeType,
    MembershipStatus,
    ResourceRef,
    ResourceType,
    ShareGrant,
    SharePermission,
    WorkspaceMembership,
    WorkspaceRole,
)
from app.domain.models import RequestContext, TaskSnapshot


_TASK_COLUMNS = """
t.id, t.workspace_id, t.owner_user_id, t.task_session_id,
t.requested_model_variant_id, t.input, t.objective, t.workflow, t.status, t.token_estimate,
t.error_summary, t.created_at, t.updated_at, t.completed_at
"""

_TASK_VISIBILITY = """
t.workspace_id = :workspace_id
and (
    (
        t.owner_user_id = :user_id
        and :can_read_owned
    )
    or (
        :can_read_shared
        and exists (
            select 1
            from resource_shares s
            where s.workspace_id = t.workspace_id
              and s.resource_type = 'task'
              and s.resource_id = t.id
              and s.permission in ('read', 'comment')
              and (
                  (s.grantee_type = 'user' and s.grantee_id = :user_id)
                  or (
                      s.grantee_type = 'workspace'
                      and s.grantee_id = :workspace_id
                  )
              )
        )
    )
)
"""

_GOVERNANCE_COLUMNS = """
t.id, t.workspace_id, t.owner_user_id, t.status, t.workflow,
t.token_estimate, t.created_at, t.updated_at, t.completed_at,
coalesce(llm.cost, 0) as cost,
llm.model_ids,
llm.error_category,
tools.tool_keys,
(
    extract(
        epoch from (coalesce(t.completed_at, t.updated_at) - t.created_at)
    ) * 1000
)::bigint as duration_ms
"""

_GOVERNANCE_JOINS = """
left join lateral (
    select
        coalesce(sum(c.cost), 0) as cost,
        coalesce(
            array_agg(distinct c.model_variant_id)
                filter (where c.model_variant_id is not null),
            array[]::text[]
        ) as model_ids,
        max(c.error_category)
            filter (where c.error_category is not null) as error_category
    from llm_calls c
    where c.task_id = t.id
) llm on true
left join lateral (
    select coalesce(
        array_agg(distinct c.tool_key),
        array[]::text[]
    ) as tool_keys
    from tool_calls c
    where c.task_id = t.id
) tools on true
"""

_RESOURCE_TABLES = {
    ResourceType.TASK: "tasks",
    ResourceType.INPUT_FILE: "input_files",
    ResourceType.ARTIFACT: "artifacts",
}


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _task_from_row(row: Mapping[str, Any]) -> TaskSnapshot:
    workflow = row["workflow"]
    return TaskSnapshot(
        task_id=str(row["id"]),
        session_id=row["task_session_id"],
        input_text=str(row["input"]),
        objective=str(row["objective"]),
        workflow=WorkflowType(workflow) if workflow else None,
        status=TaskStatus(row["status"]),
        token_estimate=int(row["token_estimate"]),
        error_summary=str(row["error_summary"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        completed_at=row["completed_at"],
        workspace_id=str(row["workspace_id"]),
        owner_user_id=str(row["owner_user_id"]),
        requested_model_variant_id=row["requested_model_variant_id"],
    )


def _governance_from_row(
    row: Mapping[str, Any],
) -> GovernanceTaskProjection:
    workflow = row["workflow"]
    return GovernanceTaskProjection(
        task_id=str(row["id"]),
        workspace_id=str(row["workspace_id"]),
        owner_user_id=str(row["owner_user_id"]),
        status=TaskStatus(row["status"]),
        workflow=WorkflowType(workflow) if workflow else None,
        token_estimate=int(row["token_estimate"]),
        cost=Decimal(row["cost"]),
        duration_ms=(int(row["duration_ms"]) if row["duration_ms"] is not None else None),
        model_ids=tuple(row["model_ids"] or ()),
        tool_keys=tuple(row["tool_keys"] or ()),
        error_category=row["error_category"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        completed_at=row["completed_at"],
    )


class PostgresScopedTaskRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def create_task(
        self,
        context: RequestContext,
        input_text: str,
        session_id: str | None = None,
        idempotency_key: str | None = None,
        requested_model_variant_id: str | None = None,
    ) -> TaskSnapshot:
        if not context.can(Capability.TASK_CREATE.value):
            raise PermissionError("task:create capability is required")
        if not input_text.strip():
            raise ValueError("task input must not be empty")

        task_id = _id("task")
        if idempotency_key:
            statement = text(
                f"""
                insert into tasks (
                    id, workspace_id, owner_user_id, task_session_id,
                    requested_model_variant_id, input, status, idempotency_key
                )
                values (
                    :task_id, :workspace_id, :user_id, :session_id,
                    :requested_model_variant_id, :input, 'created', :idempotency_key
                )
                on conflict (
                    workspace_id, owner_user_id, idempotency_key
                ) where idempotency_key is not null
                do nothing
                returning {_TASK_COLUMNS.replace("t.", "")}
                """
            )
        else:
            statement = text(
                f"""
                insert into tasks (
                    id, workspace_id, owner_user_id, task_session_id,
                    requested_model_variant_id, input, status, idempotency_key
                )
                values (
                    :task_id, :workspace_id, :user_id, :session_id,
                    :requested_model_variant_id, :input, 'created', null
                )
                returning {_TASK_COLUMNS.replace("t.", "")}
                """
            )
        params = {
            "task_id": task_id,
            "workspace_id": context.workspace_id,
            "user_id": context.user_id,
            "session_id": session_id,
            "input": input_text,
            "requested_model_variant_id": requested_model_variant_id,
            "idempotency_key": idempotency_key,
        }
        async with self._session_factory() as session:
            async with session.begin():
                if requested_model_variant_id:
                    model = (
                        await session.execute(
                            text(
                                """
                                select 1 from model_variants v
                                join model_connections c on c.id=v.connection_id
                                where v.id=:variant_id
                                  and c.workspace_id=:workspace_id
                                  and c.status='active' and v.enabled=true
                                """
                            ),
                            {
                                "variant_id": requested_model_variant_id,
                                "workspace_id": context.workspace_id,
                            },
                        )
                    ).first()
                    if model is None:
                        raise ValueError("model variant is not enabled for this workspace")
                row = (await session.execute(statement, params)).mappings().first()
                if row is None and idempotency_key:
                    row = (
                        (
                            await session.execute(
                                text(
                                    f"""
                                select {_TASK_COLUMNS}
                                from tasks t
                                where t.workspace_id = :workspace_id
                                  and t.owner_user_id = :user_id
                                  and t.idempotency_key = :idempotency_key
                                """
                                ),
                                params,
                            )
                        )
                        .mappings()
                        .one()
                    )
        return _task_from_row(row)

    async def get_task(self, context: RequestContext, task_id: str) -> TaskSnapshot | None:
        params = self._visibility_params(context)
        params["task_id"] = task_id
        async with self._session_factory() as session:
            row = (
                (
                    await session.execute(
                        text(
                            f"""
                        select {_TASK_COLUMNS}
                        from tasks t
                        where t.id = :task_id
                          and {_TASK_VISIBILITY}
                        """
                        ),
                        params,
                    )
                )
                .mappings()
                .first()
            )
        return _task_from_row(row) if row else None

    async def list_tasks(self, context: RequestContext, limit: int = 50) -> list[TaskSnapshot]:
        params = self._visibility_params(context)
        params["limit"] = max(1, min(limit, 200))
        async with self._session_factory() as session:
            rows = (
                (
                    await session.execute(
                        text(
                            f"""
                        select {_TASK_COLUMNS}
                        from tasks t
                        where {_TASK_VISIBILITY}
                        order by t.created_at desc
                        limit :limit
                        """
                        ),
                        params,
                    )
                )
                .mappings()
                .all()
            )
        return [_task_from_row(row) for row in rows]

    async def update_execution(
        self,
        context: RequestContext,
        task_id: str,
        *,
        status: TaskStatus,
        objective: str = "",
        workflow: WorkflowType | None = None,
        token_estimate: int = 0,
        error_summary: str = "",
    ) -> TaskSnapshot:
        if token_estimate < 0:
            raise ValueError("token estimate cannot be negative")
        completed = status in {
            TaskStatus.COMPLETED,
            TaskStatus.DEGRADED,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
        }
        async with self._session_factory() as session:
            async with session.begin():
                row = (
                    (
                        await session.execute(
                            text(
                                f"""
                            update tasks t
                            set status=:status,
                                objective=:objective,
                                workflow=:workflow,
                                token_estimate=:token_estimate,
                                error_summary=:error_summary,
                                updated_at=now(),
                                completed_at=case
                                    when :completed then coalesce(completed_at, now())
                                    else completed_at
                                end
                            where t.id=:task_id
                              and t.workspace_id=:workspace_id
                              and t.owner_user_id=:user_id
                            returning {_TASK_COLUMNS.replace("t.", "")}
                            """
                            ),
                            {
                                "task_id": task_id,
                                "workspace_id": context.workspace_id,
                                "user_id": context.user_id,
                                "status": status.value,
                                "objective": objective,
                                "workflow": workflow.value if workflow else None,
                                "token_estimate": token_estimate,
                                "error_summary": error_summary[:2000],
                                "completed": completed,
                            },
                        )
                    )
                    .mappings()
                    .first()
                )
        if row is None:
            raise ValueError("task not found in owner scope")
        return _task_from_row(row)

    @staticmethod
    def _visibility_params(context: RequestContext) -> dict[str, Any]:
        return {
            "workspace_id": context.workspace_id,
            "user_id": context.user_id,
            "can_read_owned": context.can(Capability.RESOURCE_READ_OWNED.value),
            "can_read_shared": context.can(Capability.RESOURCE_READ_SHARED.value),
        }


class PostgresGovernanceRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def get_task_metadata(
        self, context: RequestContext, task_id: str
    ) -> GovernanceTaskProjection | None:
        self._require_governance(context)
        async with self._session_factory() as session:
            row = (
                (
                    await session.execute(
                        text(
                            f"""
                        select {_GOVERNANCE_COLUMNS}
                        from tasks t
                        {_GOVERNANCE_JOINS}
                        where t.id = :task_id
                          and t.workspace_id = :workspace_id
                        """
                        ),
                        {
                            "task_id": task_id,
                            "workspace_id": context.workspace_id,
                        },
                    )
                )
                .mappings()
                .first()
            )
        return _governance_from_row(row) if row else None

    async def list_task_metadata(
        self, context: RequestContext, limit: int = 50
    ) -> list[GovernanceTaskProjection]:
        self._require_governance(context)
        async with self._session_factory() as session:
            rows = (
                (
                    await session.execute(
                        text(
                            f"""
                        select {_GOVERNANCE_COLUMNS}
                        from tasks t
                        {_GOVERNANCE_JOINS}
                        where t.workspace_id = :workspace_id
                        order by t.created_at desc
                        limit :limit
                        """
                        ),
                        {
                            "workspace_id": context.workspace_id,
                            "limit": max(1, min(limit, 200)),
                        },
                    )
                )
                .mappings()
                .all()
            )
        return [_governance_from_row(row) for row in rows]

    @staticmethod
    def _require_governance(context: RequestContext) -> None:
        if context.role != WorkspaceRole.ADMIN.value or not context.can(
            Capability.GOVERNANCE_VIEW.value
        ):
            raise PermissionError("administrator governance capability is required")


class PostgresResourceShareRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory
        self._policy = AuthorizationPolicy()

    async def list_grants(self, context: RequestContext, resource: ResourceRef) -> list[ShareGrant]:
        async with self._session_factory() as session:
            async with session.begin():
                actual = await self._load_resource(session, resource)
                self._require_share_owner(context, actual)
                rows = (
                    (
                        await session.execute(
                            text(
                                """
                            select
                                workspace_id, resource_type, resource_id,
                                grantee_type, grantee_id, permission, created_by
                            from resource_shares
                            where workspace_id = :workspace_id
                              and resource_type = :resource_type
                              and resource_id = :resource_id
                            order by created_at asc
                            """
                            ),
                            self._resource_params(actual),
                        )
                    )
                    .mappings()
                    .all()
                )
        return [self._grant_from_row(row) for row in rows]

    async def add_grant(
        self,
        context: RequestContext,
        resource: ResourceRef,
        grant: ShareGrant,
    ) -> ShareGrant:
        self._validate_grant(context, resource, grant)
        async with self._session_factory() as session:
            async with session.begin():
                actual = await self._load_resource(session, resource)
                self._require_share_owner(context, actual)
                await self._validate_grantee(session, actual, grant)
                params = {
                    **self._resource_params(actual),
                    "share_id": _id("share"),
                    "grantee_type": grant.grantee_type.value,
                    "grantee_id": grant.grantee_id,
                    "permission": grant.permission.value,
                    "created_by": context.user_id,
                }
                await session.execute(
                    text(
                        """
                        delete from resource_shares
                        where workspace_id = :workspace_id
                          and resource_type = :resource_type
                          and resource_id = :resource_id
                          and grantee_type = :grantee_type
                          and grantee_id = :grantee_id
                        """
                    ),
                    params,
                )
                await session.execute(
                    text(
                        """
                        insert into resource_shares (
                            id, workspace_id, resource_type, resource_id,
                            grantee_type, grantee_id, permission, created_by
                        )
                        values (
                            :share_id, :workspace_id, :resource_type,
                            :resource_id, :grantee_type, :grantee_id,
                            :permission, :created_by
                        )
                        """
                    ),
                    params,
                )
                if grant.grantee_type == GranteeType.USER:
                    recipients = [grant.grantee_id]
                else:
                    rows = (
                        await session.execute(
                            text(
                                """
                                select user_id from workspace_memberships
                                where workspace_id=:workspace_id
                                  and status='active' and user_id<>:created_by
                                """
                            ),
                            params,
                        )
                    ).all()
                    recipients = [str(row[0]) for row in rows]
                for recipient in recipients:
                    if recipient == context.user_id:
                        continue
                    await session.execute(
                        text(
                            """
                            insert into notifications (
                                id, user_id, type, resource_type,
                                resource_id, payload
                            ) values (
                                :notification_id, :user_id, 'resource_shared',
                                :resource_type, :resource_id,
                                cast(:payload as jsonb)
                            )
                            """
                        ),
                        {
                            **params,
                            "notification_id": _id("notification"),
                            "user_id": recipient,
                            "payload": json.dumps(
                                {
                                    "shared_by": context.user_id,
                                    "permission": grant.permission.value,
                                }
                            ),
                        },
                    )
        return grant

    async def remove_grant(
        self,
        context: RequestContext,
        resource: ResourceRef,
        grant: ShareGrant,
    ) -> None:
        self._validate_grant(context, resource, grant)
        async with self._session_factory() as session:
            async with session.begin():
                actual = await self._load_resource(session, resource)
                self._require_share_owner(context, actual)
                await session.execute(
                    text(
                        """
                        delete from resource_shares
                        where workspace_id = :workspace_id
                          and resource_type = :resource_type
                          and resource_id = :resource_id
                          and grantee_type = :grantee_type
                          and grantee_id = :grantee_id
                          and permission = :permission
                        """
                    ),
                    {
                        **self._resource_params(actual),
                        "grantee_type": grant.grantee_type.value,
                        "grantee_id": grant.grantee_id,
                        "permission": grant.permission.value,
                    },
                )

    async def _load_resource(self, session: AsyncSession, requested: ResourceRef) -> ResourceRef:
        table = _RESOURCE_TABLES[requested.resource_type]
        row = (
            (
                await session.execute(
                    text(
                        f"""
                    select id, workspace_id, owner_user_id
                    from {table}
                    where id = :resource_id
                      and workspace_id = :workspace_id
                    """
                    ),
                    {
                        "resource_id": requested.resource_id,
                        "workspace_id": requested.workspace_id,
                    },
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            raise ValueError("resource not found")
        actual = ResourceRef(
            resource_type=requested.resource_type,
            resource_id=str(row["id"]),
            workspace_id=str(row["workspace_id"]),
            owner_user_id=str(row["owner_user_id"]),
        )
        if actual != requested:
            raise PermissionError("resource ownership does not match")
        return actual

    def _require_share_owner(self, context: RequestContext, resource: ResourceRef) -> None:
        membership = WorkspaceMembership(
            workspace_id=context.workspace_id,
            user_id=context.user_id,
            role=WorkspaceRole(context.role),
            status=MembershipStatus.ACTIVE,
        )
        self._policy.require(
            context=context,
            membership=membership,
            resource=resource,
            access_kind=AccessKind.MANAGE_SHARE,
        )

    @staticmethod
    async def _validate_grantee(
        session: AsyncSession,
        resource: ResourceRef,
        grant: ShareGrant,
    ) -> None:
        if grant.grantee_type == GranteeType.WORKSPACE:
            if grant.grantee_id != resource.workspace_id:
                raise ValueError("workspace grant must target the same workspace")
            return
        exists = (
            await session.execute(
                text(
                    """
                    select 1
                    from workspace_memberships
                    where workspace_id = :workspace_id
                      and user_id = :user_id
                      and status = 'active'
                    """
                ),
                {
                    "workspace_id": resource.workspace_id,
                    "user_id": grant.grantee_id,
                },
            )
        ).first()
        if exists is None:
            raise ValueError("share grantee is not an active workspace member")

    @staticmethod
    def _validate_grant(
        context: RequestContext,
        resource: ResourceRef,
        grant: ShareGrant,
    ) -> None:
        if (
            grant.workspace_id != resource.workspace_id
            or grant.resource_type != resource.resource_type
            or grant.resource_id != resource.resource_id
            or grant.created_by != context.user_id
        ):
            raise ValueError("share grant does not match resource and actor")

    @staticmethod
    def _resource_params(resource: ResourceRef) -> dict[str, str]:
        return {
            "workspace_id": resource.workspace_id,
            "resource_type": resource.resource_type.value,
            "resource_id": resource.resource_id,
        }

    @staticmethod
    def _grant_from_row(row: Mapping[str, Any]) -> ShareGrant:
        return ShareGrant(
            workspace_id=str(row["workspace_id"]),
            resource_type=ResourceType(row["resource_type"]),
            resource_id=str(row["resource_id"]),
            grantee_type=GranteeType(row["grantee_type"]),
            grantee_id=str(row["grantee_id"]),
            permission=SharePermission(row["permission"]),
            created_by=str(row["created_by"]),
        )
