from __future__ import annotations

import asyncio
import hmac
import json
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

from app.api.schemas import TaskCreateRequest
from app.core.config import Settings
from app.documents.ingestion import DocumentIngestionService
from app.domain.identity import (
    GranteeType,
    ResourceRef,
    ResourceType,
    ShareGrant,
    SharePermission,
    WorkspaceRole,
    build_request_context,
)
from app.persistence.provider import PlatformRepositoryProvider


class TaskShareRequest(BaseModel):
    grantee_type: GranteeType
    grantee_id: str
    permission: SharePermission = SharePermission.READ


class TaskCommentRequest(BaseModel):
    body: str
    mentioned_user_ids: list[str] = Field(default_factory=list)


async def _read_limited_body(request: Request, max_bytes: int) -> bytes:
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            declared_size = int(content_length)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid Content-Length") from exc
        if declared_size < 0:
            raise HTTPException(status_code=400, detail="Invalid Content-Length")
        if declared_size > max_bytes:
            raise HTTPException(status_code=413, detail="File exceeds configured size limit")

    content = bytearray()
    async for chunk in request.stream():
        if len(content) + len(chunk) > max_bytes:
            raise HTTPException(status_code=413, detail="File exceeds configured size limit")
        content.extend(chunk)
    return bytes(content)


def _task_resource(task) -> ResourceRef:
    if not task.workspace_id or not task.owner_user_id:
        raise ValueError("task scope is incomplete")
    return ResourceRef(
        resource_type=ResourceType.TASK,
        resource_id=task.task_id,
        workspace_id=task.workspace_id,
        owner_user_id=task.owner_user_id,
    )


def register_platform_routes(
    app: FastAPI,
    *,
    provider: PlatformRepositoryProvider,
    settings: Settings,
) -> None:
    async def request_context(
        x_internal_token: str = Header(default="", alias="X-Internal-Token"),
        x_request_id: str = Header(default="", alias="X-Request-ID"),
        x_user_id: str = Header(default="", alias="X-User-ID"),
        x_workspace_id: str = Header(default="", alias="X-Workspace-ID"),
        x_workspace_role: str = Header(default="", alias="X-Workspace-Role"),
    ):
        expected = settings.model_gateway_internal_token
        if not expected or not hmac.compare_digest(x_internal_token, expected):
            raise HTTPException(status_code=401, detail="Invalid internal token")
        if not x_request_id or not x_user_id or not x_workspace_id:
            raise HTTPException(status_code=400, detail="Missing trusted context")
        try:
            role = WorkspaceRole(x_workspace_role)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid workspace role") from exc
        return build_request_context(
            request_id=x_request_id,
            user_id=x_user_id,
            workspace_id=x_workspace_id,
            role=role,
        )

    def repositories():
        if not provider.enabled:
            raise HTTPException(
                status_code=503,
                detail="Platform repositories are disabled",
            )
        return provider.get()

    @app.post("/internal/v1/platform/files")
    async def upload_platform_file(
        request: Request,
        x_filename: str = Header(default="", alias="X-Filename"),
        context=Depends(request_context),
    ):
        if not x_filename:
            raise HTTPException(status_code=400, detail="X-Filename is required")
        bundle = repositories()
        service = DocumentIngestionService(
            root=settings.input_file_root,
            max_file_bytes=settings.input_file_max_bytes,
            assets=bundle.assets,
            documents=bundle.documents,
        )
        media_type = request.headers.get("content-type", "application/octet-stream")
        try:
            service.validate_metadata(x_filename, media_type)
            content = await _read_limited_body(request, settings.input_file_max_bytes)
            return await service.ingest(
                context,
                filename=x_filename,
                media_type=media_type,
                content=content,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/internal/v1/platform/files")
    async def list_platform_files(
        context=Depends(request_context),
    ):
        return {"items": await repositories().assets.list_input_files(context)}

    @app.post("/internal/v1/platform/tasks")
    async def create_platform_task(
        request: TaskCreateRequest,
        context=Depends(request_context),
    ):
        bundle = repositories()
        try:
            task = await bundle.tasks.create_task(
                context,
                request.input,
                session_id=request.session_id,
                idempotency_key=context.request_id,
                requested_model_variant_id=request.model_variant_id,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if task.status.value == "created":
            try:
                await bundle.assets.attach_input_files(
                    context, task.task_id, request.input_file_ids
                )
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            await bundle.scheduler.submit(task.task_id)
            task = await bundle.tasks.get_task(context, task.task_id)
        return task

    @app.get("/internal/v1/platform/tasks")
    async def list_platform_tasks(
        limit: int = 50,
        context=Depends(request_context),
    ):
        bundle = repositories()
        return {"items": await bundle.tasks.list_tasks(context, max(1, min(limit, 200)))}

    @app.get("/internal/v1/platform/tasks/{task_id}")
    async def get_platform_task(
        task_id: str,
        context=Depends(request_context),
    ):
        task = await repositories().tasks.get_task(context, task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="Task not found")
        return task

    @app.post("/internal/v1/platform/tasks/{task_id}/cancel")
    async def cancel_platform_task(
        task_id: str,
        context=Depends(request_context),
    ):
        bundle = repositories()
        task = await bundle.tasks.get_task(context, task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="Task not found")
        if task.owner_user_id != context.user_id:
            raise HTTPException(status_code=403, detail="Only the task owner can cancel")
        await bundle.scheduler.cancel(task_id)
        return await bundle.tasks.get_task(context, task_id)

    @app.get("/internal/v1/platform/tasks/{task_id}/events")
    async def list_platform_events(
        task_id: str,
        context=Depends(request_context),
    ):
        bundle = repositories()
        if await bundle.tasks.get_task(context, task_id) is None:
            raise HTTPException(status_code=404, detail="Task not found")
        return {"items": await bundle.facts.list_events(context, task_id)}

    @app.get("/internal/v1/platform/tasks/{task_id}/stream")
    async def stream_platform_events(
        task_id: str,
        context=Depends(request_context),
    ):
        bundle = repositories()
        if await bundle.tasks.get_task(context, task_id) is None:
            raise HTTPException(status_code=404, detail="Task not found")

        async def generator():
            sequence = 0
            while True:
                events = await bundle.facts.list_events(context, task_id)
                for event in events:
                    if event.sequence <= sequence:
                        continue
                    sequence = event.sequence
                    data = {
                        "event_id": event.event_id,
                        "task_id": event.task_id,
                        "agent_id": event.agent_run_id,
                        "type": event.event_type,
                        "timestamp": event.created_at.isoformat(),
                        "sequence": event.sequence,
                        "payload": event.payload,
                        "summary": event.summary,
                    }
                    yield (f"event: event\ndata: {json.dumps(data, ensure_ascii=False)}\n\n")
                task = await bundle.tasks.get_task(context, task_id)
                if task is None or task.status.value in {
                    "completed",
                    "degraded",
                    "failed",
                    "cancelled",
                }:
                    break
                await asyncio.sleep(1)

        return StreamingResponse(generator(), media_type="text/event-stream")

    @app.get("/internal/v1/platform/tasks/{task_id}/result")
    async def get_platform_result(
        task_id: str,
        context=Depends(request_context),
    ):
        result = await repositories().facts.get_result(context, task_id)
        if result is None:
            raise HTTPException(status_code=404, detail="Result not found")
        return result

    @app.get("/internal/v1/platform/tasks/{task_id}/replay")
    async def replay_platform_task(
        task_id: str,
        context=Depends(request_context),
    ):
        bundle = repositories()
        task = await bundle.tasks.get_task(context, task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="Task not found")
        return {
            "task": task,
            "events": await bundle.facts.list_events(context, task_id),
            "agents": await bundle.facts.list_agent_runs(context, task_id),
            "tool_calls": await bundle.facts.list_tool_calls(context, task_id),
            "evidence": await bundle.facts.list_evidence(context, task_id),
            "result": await bundle.facts.get_result(context, task_id),
            "artifacts": await bundle.assets.list_artifacts(context, task_id),
            "comments": await bundle.collaboration.list_comments(context, task_id),
        }

    @app.get("/internal/v1/platform/tasks/{task_id}/artifacts")
    async def list_platform_artifacts(
        task_id: str,
        context=Depends(request_context),
    ):
        bundle = repositories()
        if await bundle.tasks.get_task(context, task_id) is None:
            raise HTTPException(status_code=404, detail="Task not found")
        return {"items": await bundle.assets.list_artifacts(context, task_id)}

    @app.get("/internal/v1/platform/tasks/{task_id}/artifacts/{artifact_id}/download")
    async def download_platform_artifact(
        task_id: str,
        artifact_id: str,
        context=Depends(request_context),
    ):
        artifact = await repositories().assets.get_artifact(context, artifact_id)
        if artifact is None or artifact.task_id != task_id:
            raise HTTPException(status_code=404, detail="Artifact not found")
        root = Path(settings.artifact_root).resolve()
        path = (root / artifact.relative_path).resolve()
        if root not in path.parents or not path.is_file():
            raise HTTPException(status_code=404, detail="Artifact file missing")
        return FileResponse(
            path,
            media_type=artifact.media_type,
            filename=artifact.filename,
        )

    @app.get("/internal/v1/platform/tasks/{task_id}/shares")
    async def list_task_shares(
        task_id: str,
        context=Depends(request_context),
    ):
        bundle = repositories()
        task = await bundle.tasks.get_task(context, task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="Task not found")
        try:
            grants = await bundle.shares.list_grants(context, _task_resource(task))
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        return {"items": grants}

    @app.put("/internal/v1/platform/tasks/{task_id}/shares")
    async def put_task_share(
        task_id: str,
        request: TaskShareRequest,
        context=Depends(request_context),
    ):
        bundle = repositories()
        task = await bundle.tasks.get_task(context, task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="Task not found")
        grant = ShareGrant(
            workspace_id=context.workspace_id,
            resource_type=ResourceType.TASK,
            resource_id=task_id,
            grantee_type=request.grantee_type,
            grantee_id=request.grantee_id,
            permission=request.permission,
            created_by=context.user_id,
        )
        try:
            return await bundle.shares.add_grant(context, _task_resource(task), grant)
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.delete("/internal/v1/platform/tasks/{task_id}/shares")
    async def delete_task_share(
        task_id: str,
        request: TaskShareRequest,
        context=Depends(request_context),
    ):
        bundle = repositories()
        task = await bundle.tasks.get_task(context, task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="Task not found")
        grant = ShareGrant(
            workspace_id=context.workspace_id,
            resource_type=ResourceType.TASK,
            resource_id=task_id,
            grantee_type=request.grantee_type,
            grantee_id=request.grantee_id,
            permission=request.permission,
            created_by=context.user_id,
        )
        try:
            await bundle.shares.remove_grant(context, _task_resource(task), grant)
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        return {"status": "removed"}

    @app.get("/internal/v1/platform/tasks/{task_id}/comments")
    async def list_task_comments(
        task_id: str,
        context=Depends(request_context),
    ):
        try:
            items = await repositories().collaboration.list_comments(context, task_id)
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        return {"items": items}

    @app.post("/internal/v1/platform/tasks/{task_id}/comments")
    async def create_task_comment(
        task_id: str,
        request: TaskCommentRequest,
        context=Depends(request_context),
    ):
        try:
            return await repositories().collaboration.create_comment(
                context,
                task_id,
                request.body,
                tuple(request.mentioned_user_ids),
            )
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/internal/v1/platform/notifications")
    async def list_notifications(
        unread_only: bool = False,
        limit: int = 50,
        context=Depends(request_context),
    ):
        items = await repositories().collaboration.list_notifications(
            context,
            unread_only=unread_only,
            limit=limit,
        )
        return {"items": items}

    @app.post("/internal/v1/platform/notifications/{notification_id}/read")
    async def mark_notification_read(
        notification_id: str,
        context=Depends(request_context),
    ):
        try:
            return await repositories().collaboration.mark_notification_read(
                context, notification_id
            )
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/internal/v1/platform/governance/tasks")
    async def list_governance_tasks(
        limit: int = 50,
        context=Depends(request_context),
    ):
        try:
            items = await repositories().governance.list_task_metadata(
                context, max(1, min(limit, 200))
            )
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        return {"items": items}
