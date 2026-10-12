import re

from app.application.ports import (
    GovernanceRepository,
    ResourceShareRepository,
    ScopedTaskRepository,
)
from app.persistence.postgres.connection import normalize_async_postgres_url
from app.persistence.postgres.repositories import (
    PostgresGovernanceRepository,
    PostgresResourceShareRepository,
    PostgresScopedTaskRepository,
    _GOVERNANCE_COLUMNS,
    _TASK_VISIBILITY,
)


def test_postgres_adapters_satisfy_scoped_protocols():
    factory = object()

    assert isinstance(PostgresScopedTaskRepository(factory), ScopedTaskRepository)
    assert isinstance(
        PostgresResourceShareRepository(factory), ResourceShareRepository
    )
    assert isinstance(
        PostgresGovernanceRepository(factory), GovernanceRepository
    )


def test_async_postgres_url_is_explicit_and_rejects_other_databases():
    assert normalize_async_postgres_url(
        "postgresql://localhost/platform"
    ) == "postgresql+psycopg://localhost/platform"
    assert normalize_async_postgres_url(
        "postgresql+psycopg://localhost/platform"
    ) == "postgresql+psycopg://localhost/platform"

    try:
        normalize_async_postgres_url("sqlite:///orchestration.db")
    except ValueError as exc:
        assert "PostgreSQL" in str(exc)
    else:
        raise AssertionError("SQLite URL must be rejected")


def test_task_visibility_is_enforced_inside_sql():
    normalized = " ".join(_TASK_VISIBILITY.split()).lower()

    assert "t.workspace_id = :workspace_id" in normalized
    assert "t.owner_user_id = :user_id" in normalized
    assert ":can_read_owned" in normalized
    assert ":can_read_shared" in normalized
    assert "from resource_shares" in normalized
    assert "s.permission in ('read', 'comment')" in normalized


def test_governance_select_list_contains_no_private_content_columns():
    selected_words = set(re.findall(r"\b[a-z_]+\b", _GOVERNANCE_COLUMNS.lower()))
    forbidden = {
        "input",
        "objective",
        "answer",
        "citations",
        "limitations",
        "filename",
        "content",
        "payload",
    }

    assert selected_words.isdisjoint(forbidden)