from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.domain.models import CommentSnapshot, NotificationSnapshot, RequestContext


@runtime_checkable
class CollaborationRepository(Protocol):
    async def list_comments(
        self, context: RequestContext, task_id: str
    ) -> list[CommentSnapshot]: ...

    async def create_comment(
        self,
        context: RequestContext,
        task_id: str,
        body: str,
        mentioned_user_ids: tuple[str, ...] = (),
    ) -> CommentSnapshot: ...

    async def list_notifications(
        self,
        context: RequestContext,
        *,
        unread_only: bool = False,
        limit: int = 50,
    ) -> list[NotificationSnapshot]: ...

    async def mark_notification_read(
        self, context: RequestContext, notification_id: str
    ) -> NotificationSnapshot: ...
