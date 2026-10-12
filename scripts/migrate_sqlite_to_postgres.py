from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    from scripts.postgres_migrate import require_postgres_url
except ModuleNotFoundError:
    from postgres_migrate import require_postgres_url


DEFAULT_WORKSPACE_ID = "workspace_legacy"
DEFAULT_USER_ID = "user_legacy_migration"


@dataclass(slots=True)
class MigrationPlan:
    source_checksum: str = ""
    rows: dict[str, list[dict[str, Any]]] = field(default_factory=lambda: defaultdict(list))
    skipped: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    warnings: list[str] = field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        return {
            "source_checksum": self.source_checksum,
            "planned": {table: len(items) for table, items in sorted(self.rows.items())},
            "skipped": dict(sorted(self.skipped.items())),
            "warnings": list(self.warnings),
        }


@dataclass(slots=True)
class ApplyReport:
    inserted: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    existing: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    conflicts: dict[str, int] = field(default_factory=lambda: defaultdict(int))

    def summary(self) -> dict[str, Any]:
        return {
            "inserted": dict(sorted(self.inserted.items())),
            "existing": dict(sorted(self.existing.items())),
            "conflicts": dict(sorted(self.conflicts.items())),
        }


def _read_rows(connection: sqlite3.Connection, table: str) -> list[dict[str, Any]]:
    return [dict(row) for row in connection.execute(f"select * from {table}")]


def _json_text(value: Any, default: Any) -> str:
    if value in (None, ""):
        return json.dumps(default, ensure_ascii=False)
    if isinstance(value, str):
        try:
            return json.dumps(json.loads(value), ensure_ascii=False)
        except json.JSONDecodeError:
            return json.dumps(default, ensure_ascii=False)
    return json.dumps(value, ensure_ascii=False)


def _run_status(task_status: str) -> str:
    if task_status in {"created", "queued"}:
        return "queued"
    if task_status in {
        "running",
        "completed",
        "degraded",
        "failed",
        "cancelled",
    }:
        return task_status
    return "failed"


def _completion_state(task_status: str) -> str:
    if task_status == "completed":
        return "complete"
    if task_status == "degraded":
        return "degraded"
    return "failed"


def _legacy_run_id(task_id: str) -> str:
    return f"run_legacy_{task_id}"


def _safe_artifact_file(artifact_root: Path, relative_path: str) -> Path | None:
    root = artifact_root.resolve()
    candidate = (root / relative_path).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    if not candidate.is_file() or candidate.is_symlink():
        return None
    return candidate


def build_migration_plan(
    sqlite_path: Path,
    *,
    artifact_root: Path,
    workspace_id: str = DEFAULT_WORKSPACE_ID,
    user_id: str = DEFAULT_USER_ID,
) -> MigrationPlan:
    if not sqlite_path.is_file():
        raise FileNotFoundError(sqlite_path)

    plan = MigrationPlan(source_checksum=hashlib.sha256(sqlite_path.read_bytes()).hexdigest())
    uri = f"{sqlite_path.resolve().as_uri()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as connection:
        connection.row_factory = sqlite3.Row
        sessions = _read_rows(connection, "sessions")
        tasks = _read_rows(connection, "tasks")
        agents = _read_rows(connection, "agents")
        events = _read_rows(connection, "events")
        tool_calls = _read_rows(connection, "tool_calls")
        evidence = _read_rows(connection, "evidence")
        results = _read_rows(connection, "results")
        artifacts = _read_rows(connection, "artifacts")
        message_count = connection.execute("select count(*) from messages").fetchone()[0]

    plan.rows["users"].append(
        {
            "id": user_id,
            "email": f"{user_id}@migration.invalid",
            "display_name": "Legacy migration owner",
            "password_hash": "!legacy-migration-no-login!",
        }
    )
    plan.rows["workspaces"].append(
        {
            "id": workspace_id,
            "name": "Legacy workspace",
            "slug": workspace_id.replace("_", "-"),
            "created_by": user_id,
        }
    )
    plan.rows["workspace_memberships"].append(
        {
            "workspace_id": workspace_id,
            "user_id": user_id,
            "role": "admin",
            "status": "active",
        }
    )

    session_ids = {str(item["session_id"]) for item in sessions}
    for item in sessions:
        plan.rows["task_sessions"].append(
            {
                "id": str(item["session_id"]),
                "workspace_id": workspace_id,
                "owner_user_id": user_id,
                "title": str(item["title"]),
                "status": (
                    item["status"] if item["status"] in {"active", "archived"} else "active"
                ),
                "created_at": item["created_at"],
                "updated_at": item["updated_at"],
            }
        )

    task_by_id = {str(item["task_id"]): item for item in tasks}
    for item in tasks:
        task_id = str(item["task_id"])
        session_id = item.get("session_id")
        if session_id and str(session_id) not in session_ids:
            plan.skipped["dangling_task_session_links"] += 1
            session_id = None
        plan.rows["tasks"].append(
            {
                "id": task_id,
                "workspace_id": workspace_id,
                "owner_user_id": user_id,
                "task_session_id": session_id,
                "input": str(item["input"]),
                "objective": str(item.get("objective") or ""),
                "workflow": item.get("workflow"),
                "status": str(item["status"]),
                "token_estimate": int(item.get("token_estimate") or 0),
                "error_summary": str(item.get("error_summary") or ""),
                "created_at": item["created_at"],
                "updated_at": item["updated_at"],
                "completed_at": item.get("completed_at"),
            }
        )
        plan.rows["task_runs"].append(
            {
                "id": _legacy_run_id(task_id),
                "task_id": task_id,
                "attempt": 1,
                "status": _run_status(str(item["status"])),
                "started_at": item["created_at"],
                "finished_at": item.get("completed_at"),
                "error_summary": str(item.get("error_summary") or ""),
                "created_at": item["created_at"],
            }
        )

    agent_ids = set()
    for item in agents:
        task_id = str(item["task_id"])
        if task_id not in task_by_id:
            plan.skipped["agents_without_task"] += 1
            continue
        agent_id = str(item["agent_id"])
        agent_ids.add(agent_id)
        plan.rows["agent_runs"].append(
            {
                "id": agent_id,
                "run_id": _legacy_run_id(task_id),
                "role": str(item["template"]),
                "goal": str(item["goal"]),
                "capability_snapshot": json.dumps(
                    {
                        "allowed_tools": json.loads(_json_text(item.get("allowed_tools"), [])),
                        "name": item["name"],
                        "context_brief": item["context_brief"],
                    },
                    ensure_ascii=False,
                ),
                "status": str(item["status"]),
                "created_at": item["created_at"],
                "updated_at": item["updated_at"],
            }
        )

    for item in events:
        task_id = str(item["task_id"])
        if task_id not in task_by_id:
            plan.skipped["events_without_task"] += 1
            continue
        agent_id = item.get("agent_id")
        plan.rows["events"].append(
            {
                "id": str(item["event_id"]),
                "task_id": task_id,
                "run_id": _legacy_run_id(task_id),
                "agent_run_id": agent_id if agent_id in agent_ids else None,
                "sequence": int(item["sequence"]),
                "type": str(item["type"]),
                "payload": _json_text(item.get("payload"), {}),
                "summary": str(item.get("summary") or ""),
                "created_at": item["timestamp"],
            }
        )

    for item in tool_calls:
        task_id = str(item["task_id"])
        if task_id not in task_by_id:
            plan.skipped["tool_calls_without_task"] += 1
            continue
        agent_id = item.get("agent_id")
        plan.rows["tool_calls"].append(
            {
                "id": str(item["tool_call_id"]),
                "task_id": task_id,
                "agent_run_id": agent_id if agent_id in agent_ids else None,
                "tool_key": str(item["tool_name"]),
                "status": str(item["status"]),
                "arguments": _json_text(item.get("arguments"), {}),
                "result_ref": (str(item["result_summary"]) if item.get("result_summary") else None),
                "error_summary": str(item.get("error") or ""),
                "created_at": item["created_at"],
                "completed_at": item.get("completed_at"),
            }
        )

    for item in evidence:
        task_id = str(item["task_id"])
        if task_id not in task_by_id:
            plan.skipped["evidence_without_task"] += 1
            continue
        source_ref = str(item.get("url") or item.get("source") or "")
        checksum = hashlib.sha256(
            (
                source_ref
                + "\n"
                + str(item.get("snippet") or "")
                + "\n"
                + str(item.get("summary") or "")
            ).encode("utf-8")
        ).hexdigest()
        plan.rows["evidence"].append(
            {
                "id": str(item["evidence_id"]),
                "task_id": task_id,
                "source_type": str(item.get("source_type") or "web"),
                "source_ref": source_ref,
                "locator": "{}",
                "title": str(item["title"]),
                "snippet": str(item.get("summary") or item.get("snippet") or ""),
                "checksum": checksum,
                "rank": int(item.get("rank") or 0),
                "created_at": item["created_at"],
            }
        )

    for item in results:
        task_id = str(item["task_id"])
        task = task_by_id.get(task_id)
        if task is None:
            plan.skipped["results_without_task"] += 1
            continue
        plan.rows["results"].append(
            {
                "id": str(item["result_id"]),
                "task_id": task_id,
                "answer": str(item["answer"]),
                "completion_state": _completion_state(str(task["status"])),
                "citations": _json_text(item.get("citations"), []),
                "limitations": _json_text(item.get("limitations"), []),
                "confidence": float(item["confidence"]),
                "used_workflow": str(item["used_workflow"]),
                "created_at": item["created_at"],
            }
        )

    versions: dict[tuple[str, str], int] = defaultdict(int)
    for item in artifacts:
        task_id = str(item["task_id"])
        if task_id not in task_by_id:
            plan.skipped["artifacts_without_task"] += 1
            continue
        relative_path = str(item["relative_path"])
        source_path = _safe_artifact_file(artifact_root, relative_path)
        if source_path is None:
            plan.skipped["artifact_files_missing_or_unsafe"] += 1
            plan.warnings.append(
                f"artifact skipped because file is missing or unsafe: {item['artifact_id']}"
            )
            continue
        logical_name = str(item["filename"])
        key = (task_id, logical_name)
        versions[key] += 1
        plan.rows["artifacts"].append(
            {
                "id": str(item["artifact_id"]),
                "workspace_id": workspace_id,
                "owner_user_id": user_id,
                "task_id": task_id,
                "object_key": f"legacy/{relative_path.replace(chr(92), '/')}",
                "logical_name": logical_name,
                "filename": logical_name,
                "media_type": str(item["media_type"]),
                "size_bytes": source_path.stat().st_size,
                "sha256": hashlib.sha256(source_path.read_bytes()).hexdigest(),
                "version": versions[key],
                "created_at": item["created_at"],
            }
        )

    if message_count:
        plan.skipped["legacy_messages_without_target_table"] = int(message_count)
        plan.warnings.append(
            "legacy messages are not migrated because platform v1 has no standalone messages table"
        )
    return plan


_INSERT_SQL = {
    "users": """
        insert into users (id, email, display_name, password_hash)
        values (%(id)s, %(email)s, %(display_name)s, %(password_hash)s)
        on conflict do nothing returning id
    """,
    "workspaces": """
        insert into workspaces (id, name, slug, created_by)
        values (%(id)s, %(name)s, %(slug)s, %(created_by)s)
        on conflict do nothing returning id
    """,
    "workspace_memberships": """
        insert into workspace_memberships (
            workspace_id, user_id, role, status, joined_at
        )
        values (
            %(workspace_id)s, %(user_id)s, %(role)s, %(status)s, now()
        )
        on conflict do nothing returning user_id
    """,
    "task_sessions": """
        insert into task_sessions (
            id, workspace_id, owner_user_id, title, status,
            created_at, updated_at
        )
        values (
            %(id)s, %(workspace_id)s, %(owner_user_id)s, %(title)s,
            %(status)s, %(created_at)s, %(updated_at)s
        )
        on conflict do nothing returning id
    """,
    "tasks": """
        insert into tasks (
            id, workspace_id, owner_user_id, task_session_id, input,
            objective, workflow, status, token_estimate, error_summary,
            created_at, updated_at, completed_at
        )
        values (
            %(id)s, %(workspace_id)s, %(owner_user_id)s,
            %(task_session_id)s, %(input)s, %(objective)s, %(workflow)s,
            %(status)s, %(token_estimate)s, %(error_summary)s,
            %(created_at)s, %(updated_at)s, %(completed_at)s
        )
        on conflict do nothing returning id
    """,
    "task_runs": """
        insert into task_runs (
            id, task_id, attempt, status, started_at, finished_at,
            error_summary, created_at
        )
        values (
            %(id)s, %(task_id)s, %(attempt)s, %(status)s,
            %(started_at)s, %(finished_at)s, %(error_summary)s,
            %(created_at)s
        )
        on conflict do nothing returning id
    """,
    "agent_runs": """
        insert into agent_runs (
            id, run_id, role, goal, capability_snapshot, status,
            created_at, updated_at
        )
        values (
            %(id)s, %(run_id)s, %(role)s, %(goal)s,
            %(capability_snapshot)s::jsonb, %(status)s,
            %(created_at)s, %(updated_at)s
        )
        on conflict do nothing returning id
    """,
    "events": """
        insert into events (
            id, task_id, run_id, agent_run_id, sequence, type,
            payload, summary, created_at
        )
        values (
            %(id)s, %(task_id)s, %(run_id)s, %(agent_run_id)s,
            %(sequence)s, %(type)s, %(payload)s::jsonb,
            %(summary)s, %(created_at)s
        )
        on conflict do nothing returning id
    """,
    "tool_calls": """
        insert into tool_calls (
            id, task_id, agent_run_id, tool_key, status, arguments,
            result_ref, error_summary, created_at, completed_at
        )
        values (
            %(id)s, %(task_id)s, %(agent_run_id)s, %(tool_key)s,
            %(status)s, %(arguments)s::jsonb, %(result_ref)s,
            %(error_summary)s, %(created_at)s, %(completed_at)s
        )
        on conflict do nothing returning id
    """,
    "evidence": """
        insert into evidence (
            id, task_id, source_type, source_ref, locator, title,
            snippet, checksum, rank, created_at
        )
        values (
            %(id)s, %(task_id)s, %(source_type)s, %(source_ref)s,
            %(locator)s::jsonb, %(title)s, %(snippet)s, %(checksum)s,
            %(rank)s, %(created_at)s
        )
        on conflict do nothing returning id
    """,
    "results": """
        insert into results (
            id, task_id, answer, completion_state, citations,
            limitations, confidence, used_workflow, created_at
        )
        values (
            %(id)s, %(task_id)s, %(answer)s, %(completion_state)s,
            %(citations)s::jsonb, %(limitations)s::jsonb,
            %(confidence)s, %(used_workflow)s, %(created_at)s
        )
        on conflict do nothing returning id
    """,
    "artifacts": """
        insert into artifacts (
            id, workspace_id, owner_user_id, task_id, object_key,
            logical_name, filename, media_type, size_bytes, sha256,
            version, created_at
        )
        values (
            %(id)s, %(workspace_id)s, %(owner_user_id)s, %(task_id)s,
            %(object_key)s, %(logical_name)s, %(filename)s,
            %(media_type)s, %(size_bytes)s, %(sha256)s,
            %(version)s, %(created_at)s
        )
        on conflict do nothing returning id
    """,
}

_INSERT_ORDER = (
    "users",
    "workspaces",
    "workspace_memberships",
    "task_sessions",
    "tasks",
    "task_runs",
    "agent_runs",
    "events",
    "tool_calls",
    "evidence",
    "results",
    "artifacts",
)

_PRIMARY_KEYS = {
    "users": ("id",),
    "workspaces": ("id",),
    "workspace_memberships": ("workspace_id", "user_id"),
    "task_sessions": ("id",),
    "tasks": ("id",),
    "task_runs": ("id",),
    "agent_runs": ("id",),
    "events": ("id",),
    "tool_calls": ("id",),
    "evidence": ("id",),
    "results": ("id",),
    "artifacts": ("id",),
}


def _primary_key_exists(connection: Any, table: str, row: dict[str, Any]) -> bool:
    keys = _PRIMARY_KEYS[table]
    predicates = " and ".join(f"{key} = %({key})s" for key in keys)
    result = connection.execute(f"select 1 from {table} where {predicates}", row).fetchone()
    return result is not None


def apply_migration_plan(plan: MigrationPlan, database_url: str) -> ApplyReport:
    try:
        import psycopg
    except ImportError as exc:
        raise RuntimeError(
            "PostgreSQL support is not installed; install the 'postgres' extra"
        ) from exc

    report = ApplyReport()
    normalized_url = require_postgres_url(database_url)
    with psycopg.connect(normalized_url, autocommit=True) as connection:
        migration = connection.execute(
            "select 1 from schema_migrations where version = '0001'"
        ).fetchone()
        if migration is None:
            raise RuntimeError("platform migration 0001 must be applied first")
        with connection.transaction():
            for table in _INSERT_ORDER:
                statement = _INSERT_SQL[table]
                for row in plan.rows.get(table, []):
                    try:
                        with connection.transaction():
                            inserted = connection.execute(statement, row).fetchone()
                            if inserted is None:
                                if _primary_key_exists(connection, table, row):
                                    report.existing[table] += 1
                                else:
                                    report.conflicts[table] += 1
                            else:
                                report.inserted[table] += 1
                    except psycopg.IntegrityError:
                        report.conflicts[table] += 1
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Migrate legacy SQLite runtime data to PostgreSQL."
    )
    parser.add_argument("--sqlite-path", type=Path, required=True)
    parser.add_argument(
        "--artifact-root",
        type=Path,
        default=Path(os.getenv("ARTIFACT_ROOT", "artifacts")),
    )
    parser.add_argument(
        "--database-url",
        default=os.getenv("PLATFORM_DATABASE_URL", os.getenv("DATABASE_URL", "")),
    )
    parser.add_argument("--workspace-id", default=DEFAULT_WORKSPACE_ID)
    parser.add_argument("--user-id", default=DEFAULT_USER_ID)
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    plan = build_migration_plan(
        args.sqlite_path,
        artifact_root=args.artifact_root,
        workspace_id=args.workspace_id,
        user_id=args.user_id,
    )
    output = {"plan": plan.summary()}
    if not args.dry_run:
        if not args.database_url:
            raise SystemExit("DATABASE_URL is required unless --dry-run is used")
        output["apply"] = apply_migration_plan(plan, args.database_url).summary()
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
