import json
import os
from pathlib import Path

import pytest

from scripts.postgres_dev_reset import (
    reset_development_database,
    validate_dev_database_url,
)


ROOT = Path(__file__).parents[3]
CONTRACT = json.loads(
    (ROOT / "contracts" / "database" / "platform-v1.json").read_text(encoding="utf-8")
)


def test_platform_schema_applies_to_postgres():
    database_url = os.getenv("TEST_POSTGRES_URL")
    if not database_url:
        pytest.skip("TEST_POSTGRES_URL is not configured")
    psycopg = pytest.importorskip("psycopg")
    normalized = validate_dev_database_url(database_url)

    applied = reset_development_database(normalized)

    assert applied == [
        "0001_platform_v1.sql",
        "0002_seed_builtin_tools.sql",
        "0003_task_model_selection.sql",
        "0004_workspace_soft_budgets.sql",
    ]
    with psycopg.connect(normalized) as connection:
        rows = connection.execute(
            """
            select table_name
            from information_schema.tables
            where table_schema = 'public'
            """
        ).fetchall()
        migrations = connection.execute(
            "select version, name from schema_migrations order by version"
        ).fetchall()
    actual_tables = {str(row[0]) for row in rows}
    assert set(CONTRACT["tables"]).issubset(actual_tables)
    assert migrations == [
        ("0001", "0001_platform_v1.sql"),
        ("0002", "0002_seed_builtin_tools.sql"),
        ("0003", "0003_task_model_selection.sql"),
        ("0004", "0004_workspace_soft_budgets.sql"),
    ]
