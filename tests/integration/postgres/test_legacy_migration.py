from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

from app.db.database import init_database
from scripts.migrate_sqlite_to_postgres import (
    apply_migration_plan,
    build_migration_plan,
)
from scripts.postgres_dev_reset import (
    reset_development_database,
    validate_dev_database_url,
)


NOW = "2026-01-02T03:04:05+00:00"


def _seed_legacy_database(database_path: Path, artifact_root: Path) -> None:
    connection = init_database(str(database_path))
    connection.execute(
        "insert into sessions values (?, ?, ?, ?, ?)",
        ("session_1", "Legacy session", "active", NOW, NOW),
    )
    connection.execute(
        """
        insert into tasks (
            task_id, session_id, input, objective, workflow, status,
            token_estimate, error_summary, created_at, updated_at, completed_at
        ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "task_1", "session_1", "Input", "Objective", "direct",
            "completed", 10, "", NOW, NOW, NOW,
        ),
    )
    connection.execute(
        """
        insert into events (
            event_id, task_id, agent_id, type, timestamp,
            sequence, payload, summary
        ) values (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        ("event_1", "task_1", None, "task.completed", NOW, 1, "{}", "done"),
    )
    connection.execute(
        "insert into results values (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "result_1", "task_1", "Answer", "[]", "[]",
            0.9, "direct", NOW,
        ),
    )
    artifact = artifact_root / "task_1" / "answer.txt"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("Answer", encoding="utf-8")
    connection.execute(
        "insert into artifacts values (?, ?, ?, ?, ?, ?, ?)",
        (
            "artifact_1", "task_1", "answer.txt", "text/plain",
            artifact.stat().st_size, "task_1/answer.txt", NOW,
        ),
    )
    connection.commit()
    connection.close()


def test_legacy_migration_is_relational_and_idempotent(
    tmp_path: Path,
) -> None:
    database_url = os.getenv("TEST_POSTGRES_URL")
    if not database_url:
        pytest.skip("TEST_POSTGRES_URL is not configured")
    psycopg = pytest.importorskip("psycopg")
    normalized = validate_dev_database_url(database_url)
    reset_development_database(normalized)

    source = tmp_path / "legacy.db"
    artifact_root = tmp_path / "artifacts"
    _seed_legacy_database(source, artifact_root)
    source_checksum = hashlib.sha256(source.read_bytes()).hexdigest()
    plan = build_migration_plan(source, artifact_root=artifact_root)

    first = apply_migration_plan(plan, normalized)
    second = apply_migration_plan(plan, normalized)
    plan.rows["results"][0]["id"] = "result_conflict"
    conflict = apply_migration_plan(plan, normalized)

    assert first.conflicts == {}
    assert first.inserted["tasks"] == 1
    assert first.inserted["artifacts"] == 1
    assert second.existing["tasks"] == 1
    assert second.existing["artifacts"] == 1
    assert second.conflicts == {}
    assert conflict.conflicts["results"] == 1
    assert hashlib.sha256(source.read_bytes()).hexdigest() == source_checksum

    with psycopg.connect(normalized) as connection:
        task = connection.execute(
            """
            select task_session_id, workspace_id, owner_user_id
            from tasks where id = 'task_1'
            """
        ).fetchone()
        event = connection.execute(
            "select run_id from events where id = 'event_1'"
        ).fetchone()
        result = connection.execute(
            """
            select task_id, completion_state
            from results where id = 'result_1'
            """
        ).fetchone()
        artifact = connection.execute(
            """
            select task_id, version, sha256
            from artifacts where id = 'artifact_1'
            """
        ).fetchone()

    assert task == ("session_1", "workspace_legacy", "user_legacy_migration")
    assert event == ("run_legacy_task_1",)
    assert result == ("task_1", "complete")
    assert artifact == (
        "task_1",
        1,
        hashlib.sha256(b"Answer").hexdigest(),
    )
