import json
import re
from pathlib import Path

import pytest

from scripts.postgres_dev_reset import validate_dev_database_url
from scripts.postgres_migrate import discover_migrations, validate_migrations


ROOT = Path(__file__).parents[1]
MIGRATION_PATH = ROOT / "infra" / "migrations"
CONTRACT_PATH = ROOT / "contracts" / "database" / "platform-v1.json"


def _schema():
    sql = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(MIGRATION_PATH.glob("*.sql"))
    )
    tables = {
        match.group("name"): match.group("body")
        for match in re.finditer(
            r"create\s+table\s+(?P<name>[a-z_]+)\s*"
            r"\((?P<body>.*?)\);",
            sql,
            flags=re.IGNORECASE | re.DOTALL,
        )
    }
    return sql, tables


def test_database_contract_matches_created_tables_exactly():
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    _, tables = _schema()

    assert set(tables) == set(contract["tables"])
    assert len(tables) == 32


def test_workspace_and_private_resource_boundaries_are_structural():
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    _, tables = _schema()

    for table_name in contract["scoped_tables"]:
        assert re.search(r"\bworkspace_id\s+text\s+not\s+null\b", tables[table_name])
    for table_name in contract["private_resource_tables"]:
        assert re.search(r"\bowner_user_id\s+text\s+not\s+null\b", tables[table_name])


def test_private_resource_foreign_keys_cannot_cross_scope():
    _, tables = _schema()

    membership_reference = "references workspace_memberships(workspace_id, user_id)"
    for table_name in ("task_sessions", "tasks", "input_files", "artifacts"):
        assert membership_reference in tables[table_name]

    assert "references task_sessions(id, workspace_id, owner_user_id)" in tables["tasks"]
    for table_name in ("input_files", "artifacts"):
        assert "references tasks(id, workspace_id, owner_user_id)" in tables[table_name]


def test_secrets_and_session_tokens_are_hash_or_ciphertext_only():
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    _, tables = _schema()

    for table_name, columns in contract["sensitive_columns"].items():
        for column in columns:
            assert re.search(rf"\b{column}\b", tables[table_name])
    assert "api_key" not in tables["model_connections"].lower()
    assert "secret_plaintext" not in tables["model_connections"].lower()
    assert re.search(r"\btoken_hash\s+text\s+not\s+null\b", tables["auth_sessions"])


def test_runtime_idempotency_and_recovery_constraints_exist():
    sql, tables = _schema()

    assert "unique (task_id, attempt)" in tables["task_runs"].lower()
    assert "unique (run_id, sequence)" in tables["checkpoints"].lower()
    assert "lease_token text not null unique" in tables["worker_leases"].lower()
    assert "uq_tasks_idempotency" in sql
    assert "uq_tool_calls_idempotency" in sql
    assert "unique (task_id, logical_name, version)" in tables["artifacts"].lower()


def test_production_migrations_are_ordered_checksumed_and_non_destructive():
    migrations = discover_migrations()

    assert [item.version for item in migrations] == ["0001", "0002", "0003", "0004"]
    assert all(len(item.checksum) == 64 for item in migrations)
    validate_migrations(migrations)
    assert "requested_model_variant_id" in migrations[2].sql
    assert "workspace_budget_policies" in migrations[3].sql


@pytest.mark.parametrize(
    "database_url",
    [
        "sqlite:///orchestration.db",
        "mysql://localhost/platform_dev",
        "postgresql://localhost/platform",
        "postgresql://localhost/production",
    ],
)
def test_development_reset_rejects_non_dev_database(database_url):
    with pytest.raises(ValueError):
        validate_dev_database_url(database_url)


@pytest.mark.parametrize(
    "database_url",
    [
        "postgresql://localhost/platform_dev",
        "postgresql+psycopg://localhost/platform_test",
    ],
)
def test_development_reset_accepts_only_dev_or_test_database(database_url):
    assert validate_dev_database_url(database_url).startswith("postgresql://")
