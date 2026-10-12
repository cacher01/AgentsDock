from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.application.ports import (
    RuntimeExecutionRepository,
    ScopedCheckpointStore,
)
from app.domain.models import (
    Checkpoint,
    ExecutionNodeSnapshot,
    NodeStatus,
    RunSnapshot,
    RunStatus,
)
from app.persistence.postgres.execution import (
    PostgresRuntimeExecutionRepository,
    PostgresScopedCheckpointStore,
    _CHECKPOINT_SELECT,
    _NODE_SELECT,
    _OWNER_TASK_SCOPE,
    _RUN_SELECT,
)


def test_postgres_execution_adapters_satisfy_runtime_protocols() -> None:
    factory = object()

    assert isinstance(
        PostgresRuntimeExecutionRepository(factory),
        RuntimeExecutionRepository,
    )
    assert isinstance(
        PostgresScopedCheckpointStore(factory),
        ScopedCheckpointStore,
    )


@pytest.mark.parametrize(
    "query",
    [_RUN_SELECT, _NODE_SELECT, _CHECKPOINT_SELECT],
)
def test_runtime_reads_enforce_owner_scope_inside_sql(query: str) -> None:
    normalized = " ".join(query.split()).lower()

    assert "t.workspace_id = :workspace_id" in normalized
    assert "t.owner_user_id = :user_id" in normalized


def test_owner_scope_fragment_has_no_shared_resource_bypass() -> None:
    normalized = " ".join(_OWNER_TASK_SCOPE.split()).lower()

    assert "workspace_id" in normalized
    assert "owner_user_id" in normalized
    assert "resource_shares" not in normalized


def test_execution_node_and_checkpoint_validate_recovery_identity() -> None:
    now = datetime.now(timezone.utc)

    with pytest.raises(ValueError, match="attempt"):
        RunSnapshot(
            run_id="run_1",
            task_id="task_1",
            attempt=0,
            status=RunStatus.QUEUED,
        )

    with pytest.raises(ValueError, match="attempt"):
        ExecutionNodeSnapshot(
            node_id="node_1",
            run_id="run_1",
            node_key="step",
            node_type="agent",
            status=NodeStatus.PENDING,
            attempt=0,
            created_at=now,
            updated_at=now,
        )

    checkpoint = Checkpoint(
        checkpoint_id="checkpoint_1",
        task_id="task_1",
        run_id="run_1",
        sequence=1,
        kind="planning",
        payload={"step": 1},
        created_at=now,
    )
    assert checkpoint.workflow_version == "legacy-v1"
