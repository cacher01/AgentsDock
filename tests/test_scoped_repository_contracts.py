import inspect
from dataclasses import fields
from datetime import datetime, timezone
from decimal import Decimal

from app.application.ports import (
    GovernanceRepository,
    GovernanceTaskProjection,
    ResourceShareRepository,
    ScopedTaskRepository,
)
from app.core.enums import TaskStatus, WorkflowType


def test_scoped_task_repository_methods_require_request_context():
    for method_name in ("create_task", "get_task", "list_tasks"):
        signature = inspect.signature(
            getattr(ScopedTaskRepository, method_name)
        )
        assert "context" in signature.parameters


def test_share_repository_methods_require_context_and_resource():
    for method_name in ("list_grants", "add_grant", "remove_grant"):
        signature = inspect.signature(
            getattr(ResourceShareRepository, method_name)
        )
        assert "context" in signature.parameters
        assert "resource" in signature.parameters


def test_governance_repository_is_separate_from_content_repository():
    assert not hasattr(GovernanceRepository, "get_task")
    assert hasattr(GovernanceRepository, "get_task_metadata")
    assert hasattr(GovernanceRepository, "list_task_metadata")


def test_governance_projection_cannot_contain_private_content_fields():
    field_names = {item.name for item in fields(GovernanceTaskProjection)}
    forbidden = {
        "input",
        "input_text",
        "objective",
        "answer",
        "citations",
        "limitations",
        "filename",
        "content",
    }

    assert field_names.isdisjoint(forbidden)
    projection = GovernanceTaskProjection(
        task_id="task_1",
        workspace_id="workspace_1",
        owner_user_id="user_1",
        status=TaskStatus.COMPLETED,
        workflow=WorkflowType.DIRECT,
        token_estimate=10,
        cost=Decimal("0.01"),
        duration_ms=120,
        model_ids=("model_1",),
        tool_keys=("calculator",),
        error_category=None,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    assert projection.workspace_id == "workspace_1"