from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

from app.db.database import init_database
from scripts.migrate_sqlite_to_postgres import build_migration_plan


NOW = "2026-01-02T03:04:05+00:00"


def _seed_legacy_database(database_path: Path, artifact_root: Path) -> None:
    connection = init_database(str(database_path))
    connection.execute(
        "insert into sessions values (?, ?, ?, ?, ?)",
        ("session_1", "Migration session", "active", NOW, NOW),
    )
    connection.execute(
        """
        insert into tasks (
            task_id, session_id, input, objective, workflow, status,
            token_estimate, error_summary, created_at, updated_at, completed_at
        ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "task_1", "session_1", "Analyze the report",
            "Produce a concise answer", "research", "completed", 321, "",
            NOW, NOW, NOW,
        ),
    )
    connection.execute(
        """
        insert into agents (
            agent_id, task_id, name, template, goal, context_brief,
            allowed_tools, status, created_at, updated_at
        ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "agent_1", "task_1", "Researcher", "researcher",
            "Collect evidence", "legacy context", "{invalid-json",
            "completed", NOW, NOW,
        ),
    )
    connection.executemany(
        """
        insert into events (
            event_id, task_id, agent_id, type, timestamp,
            sequence, payload, summary
        ) values (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                "event_1", "task_1", "agent_1", "agent.completed",
                NOW, 1, '{"ok": true}', "done",
            ),
            (
                "event_2", "task_1", "missing_agent", "legacy.event",
                NOW, 2, "invalid-json", "redacted link",
            ),
        ],
    )
    connection.execute(
        "insert into messages values (?, ?, ?, ?, ?, ?, ?)",
        (
            "message_1", "task_1", "agent_1", None, "status",
            '{"text": "done"}', NOW,
        ),
    )
    connection.execute(
        """
        insert into tool_calls (
            tool_call_id, task_id, agent_id, tool_name, arguments,
            status, result_summary, error, created_at, completed_at
        ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "tool_call_1", "task_1", "agent_1", "web_search",
            '{"query": "example"}', "completed", "result reference", "",
            NOW, NOW,
        ),
    )
    connection.execute(
        "insert into evidence values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "evidence_1", "task_1", "Source", "https://example.com",
            "quoted text", "example", 1, "web", "summary", NOW,
        ),
    )
    connection.execute(
        "insert into results values (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "result_1", "task_1", "Final answer", '["evidence_1"]',
            '["legacy limitation"]', 0.875, "research", NOW,
        ),
    )

    first = artifact_root / "task_1" / "report.txt"
    second = artifact_root / "task_1" / "revised-report.txt"
    first.parent.mkdir(parents=True)
    first.write_text("version one", encoding="utf-8")
    second.write_text("version two", encoding="utf-8")
    connection.executemany(
        "insert into artifacts values (?, ?, ?, ?, ?, ?, ?)",
        [
            (
                "artifact_1", "task_1", "report.txt", "text/plain", 0,
                "task_1/report.txt", NOW,
            ),
            (
                "artifact_2", "task_1", "report.txt", "text/plain", 0,
                "task_1/revised-report.txt", NOW,
            ),
            (
                "artifact_missing", "task_1", "missing.txt", "text/plain", 0,
                "task_1/missing.txt", NOW,
            ),
        ],
    )
    connection.commit()
    connection.close()


def test_build_migration_plan_preserves_relations_and_validates_files(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "legacy.db"
    artifact_root = tmp_path / "artifacts"
    _seed_legacy_database(database_path, artifact_root)
    checksum_before = hashlib.sha256(database_path.read_bytes()).hexdigest()

    plan = build_migration_plan(database_path, artifact_root=artifact_root)

    assert plan.source_checksum == checksum_before
    assert hashlib.sha256(database_path.read_bytes()).hexdigest() == checksum_before
    assert len(plan.rows["tasks"]) == 1
    assert plan.rows["tasks"][0]["task_session_id"] == "session_1"
    assert plan.rows["task_runs"][0]["id"] == "run_legacy_task_1"
    assert plan.rows["agent_runs"][0]["run_id"] == "run_legacy_task_1"
    assert '"allowed_tools": []' in plan.rows["agent_runs"][0]["capability_snapshot"]
    assert plan.rows["events"][0]["agent_run_id"] == "agent_1"
    assert plan.rows["events"][1]["agent_run_id"] is None
    assert plan.rows["events"][1]["payload"] == "{}"
    assert plan.rows["results"][0]["completion_state"] == "complete"

    artifacts = plan.rows["artifacts"]
    assert [artifact["version"] for artifact in artifacts] == [1, 2]
    assert artifacts[0]["size_bytes"] == len(b"version one")
    assert artifacts[0]["sha256"] == hashlib.sha256(b"version one").hexdigest()
    assert plan.skipped["artifact_files_missing_or_unsafe"] == 1
    assert plan.skipped["legacy_messages_without_target_table"] == 1


def test_build_migration_plan_does_not_open_source_for_writing(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "legacy.db"
    artifact_root = tmp_path / "artifacts"
    _seed_legacy_database(database_path, artifact_root)
    with sqlite3.connect(database_path) as connection:
        original_count = connection.execute("select count(*) from tasks").fetchone()[0]

    build_migration_plan(database_path, artifact_root=artifact_root)

    with sqlite3.connect(database_path) as connection:
        final_count = connection.execute("select count(*) from tasks").fetchone()[0]
    assert final_count == original_count
