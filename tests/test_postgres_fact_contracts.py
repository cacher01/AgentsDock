from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.application.ports import RuntimeFactRepository
from app.core.enums import WorkflowType
from app.domain.models import (
    CompletionState,
    ResultSnapshot,
    RuntimeEventSnapshot,
)
from app.persistence.postgres.facts import (
    PostgresRuntimeFactRepository,
    _OWNER_SCOPE,
)


def test_postgres_fact_adapter_satisfies_runtime_protocol() -> None:
    assert isinstance(
        PostgresRuntimeFactRepository(object()),
        RuntimeFactRepository,
    )


def test_runtime_fact_scope_requires_workspace_and_owner() -> None:
    normalized = " ".join(_OWNER_SCOPE.split()).lower()

    assert "t.workspace_id = :workspace_id" in normalized
    assert "t.owner_user_id = :user_id" in normalized
    assert "resource_shares" not in normalized


def test_runtime_event_requires_positive_sequence() -> None:
    with pytest.raises(ValueError, match="sequence"):
        RuntimeEventSnapshot(
            event_id="event_1",
            task_id="task_1",
            sequence=0,
            event_type="started",
            payload={},
            summary="",
            created_at=datetime.now(timezone.utc),
        )


def test_result_confidence_is_bounded() -> None:
    with pytest.raises(ValueError, match="confidence"):
        ResultSnapshot(
            result_id="result_1",
            task_id="task_1",
            answer="answer",
            completion_state=CompletionState.COMPLETE,
            citations=(),
            limitations=(),
            confidence=1.1,
            used_workflow=WorkflowType.DIRECT,
            created_at=datetime.now(timezone.utc),
        )
