from __future__ import annotations

import json
import uuid
from collections.abc import Mapping
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.application.ports.collaboration import CollaborationRepository
from app.domain.identity import Capability
from app.domain.models import CommentSnapshot, NotificationSnapshot, RequestContext


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _comment(row: Mapping[str, Any]) -> CommentSnapshot:
    return CommentSnapshot(
        comment_id=str(row["id"]),
        task_id=str(row["task_id"]),
        author_user_id=str(row["author_user_id"]),
        author_name=str(row["author_name"]),
        body=str(row["body"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _notification(row: Mapping[str, Any]) -> NotificationSnapshot:
    payload = row["payload"]
    if isinstance(payload, str):
        payload = json.loads(payload)
    return NotificationSnapshot(
        notification_id=str(row["id"]),
        user_id=str(row["user_id"]),
        notification_type=str(row["type"]),
        resource_type=str(row["resource_type"]),
        resource_id=str(row["resource_id"]),
        payload=payload or {},
        read_at=row["read_at"],
        created_at=row["created_at"],
    )


class PostgresCollaborationRepository(CollaborationRepository):
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def list_comments(self, context: RequestContext, task_id: str) -> list[CommentSnapshot]:
        async with self._session_factory() as session:
            await self._require_task_access(session, context, task_id, comment=False)
            rows = (
                (
                    await session.execute(
                        text(
                            """
                            select c.id, c.task_id, c.author_user_id,
                                   u.display_name as author_name, c.body,
                                   c.created_at, c.updated_at
                            from comments c
                            join users u on u.id=c.author_user_id
                            where c.task_id=:task_id and c.deleted_at is null
                            order by c.created_at asc
                            """
                        ),
                        {"task_id": task_id},
                    )
                )
                .mappings()
                .all()
            )
        return [_comment(row) for row in rows]

    async def create_comment(
        self,
        context: RequestContext,
        task_id: str,
        body: str,
        mentioned_user_ids: tuple[str, ...] = (),
    ) -> CommentSnapshot:
        if not context.can(Capability.COMMENT_CREATE.value):
            raise PermissionError("comment:create capability is required")
        normalized = body.strip()
        if not normalized:
            raise ValueError("comment body must not be empty")
        if len(normalized) > 4000:
            raise ValueError("comment body must not exceed 4000 characters")
        mentioned = tuple(dict.fromkeys(item for item in mentioned_user_ids if item))
        comment_id = _id("comment")
        async with self._session_factory() as session:
            async with session.begin():
                owner_user_id = await self._require_task_access(
                    session, context, task_id, comment=True
                )
                if mentioned:
                    rows = (
                        await session.execute(
                            text(
                                """
                                select user_id from workspace_memberships
                                where workspace_id=:workspace_id
                                  and user_id=any(:user_ids)
                                  and status='active'
                                """
                            ),
                            {
                                "workspace_id": context.workspace_id,
                                "user_ids": list(mentioned),
                            },
                        )
                    ).all()
                    valid = {str(row[0]) for row in rows}
                    invalid = set(mentioned) - valid
                    if invalid:
                        raise ValueError("mentioned user is not an active workspace member")
                row = (
                    (
                        await session.execute(
                            text(
                                """
                                insert into comments (id, task_id, author_user_id, body)
                                values (:id, :task_id, :author_user_id, :body)
                                returning id, task_id, author_user_id, body,
                                          created_at, updated_at
                                """
                            ),
                            {
                                "id": comment_id,
                                "task_id": task_id,
                                "author_user_id": context.user_id,
                                "body": normalized,
                            },
                        )
                    )
                    .mappings()
                    .one()
                )
                author_name = (
                    await session.execute(
                        text("select display_name from users where id=:user_id"),
                        {"user_id": context.user_id},
                    )
                ).scalar_one()
                recipients = set(mentioned)
                if owner_user_id != context.user_id:
                    recipients.add(owner_user_id)
                for user_id in recipients:
                    mention = user_id in mentioned
                    if mention:
                        await session.execute(
                            text(
                                """
                                insert into mentions (
                                    id, comment_id, mentioned_user_id
                                ) values (:id, :comment_id, :user_id)
                                on conflict (comment_id, mentioned_user_id) do nothing
                                """
                            ),
                            {
                                "id": _id("mention"),
                                "comment_id": comment_id,
                                "user_id": user_id,
                            },
                        )
                    await self._insert_notification(
                        session,
                        user_id=user_id,
                        notification_type="task_mention" if mention else "task_comment",
                        resource_type="task",
                        resource_id=task_id,
                        payload={
                            "comment_id": comment_id,
                            "author_user_id": context.user_id,
                            "author_name": str(author_name),
                        },
                    )
        return _comment({**row, "author_name": str(author_name)})

    async def list_notifications(
        self,
        context: RequestContext,
        *,
        unread_only: bool = False,
        limit: int = 50,
    ) -> list[NotificationSnapshot]:
        clause = "and read_at is null" if unread_only else ""
        async with self._session_factory() as session:
            rows = (
                (
                    await session.execute(
                        text(
                            f"""
                            select id, user_id, type, resource_type, resource_id,
                                   payload, read_at, created_at
                            from notifications
                            where user_id=:user_id {clause}
                            order by created_at desc
                            limit :limit
                            """
                        ),
                        {"user_id": context.user_id, "limit": max(1, min(limit, 200))},
                    )
                )
                .mappings()
                .all()
            )
        return [_notification(row) for row in rows]

    async def mark_notification_read(
        self, context: RequestContext, notification_id: str
    ) -> NotificationSnapshot:
        async with self._session_factory() as session:
            async with session.begin():
                row = (
                    (
                        await session.execute(
                            text(
                                """
                                update notifications set read_at=coalesce(read_at, now())
                                where id=:notification_id and user_id=:user_id
                                returning id, user_id, type, resource_type,
                                          resource_id, payload, read_at, created_at
                                """
                            ),
                            {
                                "notification_id": notification_id,
                                "user_id": context.user_id,
                            },
                        )
                    )
                    .mappings()
                    .first()
                )
        if row is None:
            raise ValueError("notification not found")
        return _notification(row)

    @staticmethod
    async def _require_task_access(
        session: AsyncSession,
        context: RequestContext,
        task_id: str,
        *,
        comment: bool,
    ) -> str:
        permission_clause = (
            "s.permission='comment'" if comment else "s.permission in ('read','comment')"
        )
        row = (
            (
                await session.execute(
                    text(
                        f"""
                        select t.owner_user_id
                        from tasks t
                        where t.id=:task_id and t.workspace_id=:workspace_id
                          and (
                            t.owner_user_id=:user_id
                            or exists (
                                select 1 from resource_shares s
                                where s.workspace_id=t.workspace_id
                                  and s.resource_type='task'
                                  and s.resource_id=t.id
                                  and {permission_clause}
                                  and (
                                    (s.grantee_type='user' and s.grantee_id=:user_id)
                                    or (s.grantee_type='workspace' and s.grantee_id=:workspace_id)
                                  )
                            )
                          )
                        """
                    ),
                    {
                        "task_id": task_id,
                        "workspace_id": context.workspace_id,
                        "user_id": context.user_id,
                    },
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            raise PermissionError("task comment access is not granted")
        return str(row["owner_user_id"])

    @staticmethod
    async def _insert_notification(
        session: AsyncSession,
        *,
        user_id: str,
        notification_type: str,
        resource_type: str,
        resource_id: str,
        payload: Mapping[str, Any],
    ) -> None:
        await session.execute(
            text(
                """
                insert into notifications (
                    id, user_id, type, resource_type, resource_id, payload
                ) values (
                    :id, :user_id, :type, :resource_type, :resource_id,
                    cast(:payload as jsonb)
                )
                """
            ),
            {
                "id": _id("notification"),
                "user_id": user_id,
                "type": notification_type,
                "resource_type": resource_type,
                "resource_id": resource_id,
                "payload": json.dumps(dict(payload), ensure_ascii=False),
            },
        )
