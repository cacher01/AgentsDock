from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone

import pytest

from app.domain.identity import WorkspaceRole, build_request_context
from app.domain.models import ArtifactSnapshot, InputFileSnapshot, LLMCallSnapshot
from app.persistence.postgres import (
    PostgresRuntimeAssetRepository,
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


def _seed(psycopg, database_url: str) -> None:
    with psycopg.connect(database_url, autocommit=True) as connection:
        connection.execute(
            """
            insert into users (id, email, display_name, password_hash)
            values
                ('asset_owner', 'asset-owner@example.com', 'Owner', 'hash'),
                ('asset_other', 'asset-other@example.com', 'Other', 'hash')
            """
        )
        connection.execute(
            """
            insert into workspaces (id, name, slug, created_by)
            values
                ('asset_workspace', 'Assets', 'assets-workspace', 'asset_owner'),
                ('asset_other_ws', 'Other', 'assets-other', 'asset_other')
            """
        )
        connection.execute(
            """
            insert into workspace_memberships (
                workspace_id, user_id, role, status, joined_at
            ) values
                ('asset_workspace', 'asset_owner', 'member', 'active', now()),
                ('asset_other_ws', 'asset_other', 'member', 'active', now())
            """
        )


def test_runtime_assets_are_versioned_and_owner_scoped() -> None:
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
        assets = PostgresRuntimeAssetRepository(factory)
        owner = _context("asset_owner", "asset_workspace")
        outsider = _context("asset_other", "asset_other_ws")
        now = datetime.now(timezone.utc)

        try:
            task = await tasks.create_task(owner, "asset task")
            input_file = await assets.save_input_file(
                owner,
                InputFileSnapshot(
                    input_file_id="input_1",
                    workspace_id=owner.workspace_id,
                    owner_user_id=owner.user_id,
                    task_id=task.task_id,
                    object_key="inputs/report.pdf",
                    filename="report.pdf",
                    media_type="application/pdf",
                    size_bytes=128,
                    sha256="input-sha",
                    parse_status="pending",
                    parse_error="",
                    created_at=now,
                ),
            )
            parsed = await assets.update_parse_status(
                owner, input_file.input_file_id, "completed"
            )
            assert parsed.parse_status == "completed"

            artifact = await assets.save_artifact(
                owner,
                ArtifactSnapshot(
                    artifact_id="artifact_1",
                    task_id=task.task_id,
                    workspace_id=owner.workspace_id,
                    owner_user_id=owner.user_id,
                    filename="report.md",
                    logical_name="report",
                    media_type="text/markdown",
                    size_bytes=42,
                    relative_path="artifacts/report-v1.md",
                    object_key="artifacts/report-v1.md",
                    sha256="artifact-sha",
                    version=1,
                    created_at=now,
                ),
            )
            assert artifact.version == 1

            call = await assets.record_llm_call(
                owner,
                LLMCallSnapshot(
                    llm_call_id="llm_1",
                    task_id=task.task_id,
                    status="completed",
                    request_tokens=100,
                    response_tokens=40,
                    cost=0.001,
                    latency_ms=25,
                    created_at=now,
                ),
            )
            assert call.request_tokens == 100

            assert await assets.list_input_files(outsider, task.task_id) == []
            assert await assets.get_artifact(outsider, artifact.artifact_id) is None
            assert await assets.list_llm_calls(outsider, task.task_id) == []
        finally:
            await engine.dispose()

    asyncio.run(scenario())
