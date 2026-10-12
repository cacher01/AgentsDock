from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.application.ports import RuntimeAssetRepository
from app.domain.models import ArtifactSnapshot, LLMCallSnapshot
from app.persistence.postgres.assets import (
    PostgresRuntimeAssetRepository,
    _OWNER_SCOPE,
)


def test_postgres_asset_adapter_satisfies_runtime_protocol() -> None:
    assert isinstance(
        PostgresRuntimeAssetRepository(object()),
        RuntimeAssetRepository,
    )


def test_runtime_asset_scope_requires_workspace_and_owner() -> None:
    normalized = " ".join(_OWNER_SCOPE.split()).lower()
    assert "t.workspace_id = :workspace_id" in normalized
    assert "t.owner_user_id = :user_id" in normalized


def test_asset_and_llm_usage_reject_invalid_measurements() -> None:
    now = datetime.now(timezone.utc)
    with pytest.raises(ValueError, match="size"):
        ArtifactSnapshot(
            artifact_id="artifact_1",
            task_id="task_1",
            filename="report.txt",
            media_type="text/plain",
            size_bytes=-1,
            relative_path="task/report.txt",
            created_at=now,
        )
    with pytest.raises(ValueError, match="token"):
        LLMCallSnapshot(
            llm_call_id="llm_1",
            task_id="task_1",
            status="completed",
            request_tokens=-1,
            response_tokens=1,
            cost=0,
            created_at=now,
        )
