from __future__ import annotations

import argparse
import os
from urllib.parse import urlsplit

try:
    from scripts.postgres_migrate import apply_migrations, require_postgres_url
except ModuleNotFoundError:
    from postgres_migrate import apply_migrations, require_postgres_url


ALLOWED_DATABASE_SUFFIXES = ("_dev", "_test")


def validate_dev_database_url(database_url: str) -> str:
    normalized = require_postgres_url(database_url)
    database_name = urlsplit(normalized).path.lstrip("/")
    if not database_name.endswith(ALLOWED_DATABASE_SUFFIXES):
        raise ValueError("development reset requires a database ending in _dev or _test")
    return normalized


def reset_development_database(database_url: str) -> list[str]:
    try:
        import psycopg
    except ImportError as exc:
        raise RuntimeError(
            "PostgreSQL support is not installed; install the 'postgres' extra"
        ) from exc

    normalized = validate_dev_database_url(database_url)
    with psycopg.connect(normalized, autocommit=True) as conn:
        with conn.transaction():
            conn.execute("drop schema public cascade")
            conn.execute("create schema public")
    return apply_migrations(normalized)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Destructively reset a development/test PostgreSQL database."
    )
    parser.add_argument(
        "--database-url",
        default=os.getenv("DATABASE_URL", ""),
        help="Database name must end in _dev or _test.",
    )
    parser.add_argument(
        "--confirm-development-reset",
        action="store_true",
        help="Required confirmation for the destructive reset.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if not args.confirm_development_reset:
        raise SystemExit("--confirm-development-reset is required")
    if not args.database_url:
        raise SystemExit("DATABASE_URL is required")
    applied = reset_development_database(args.database_url)
    print(f"reset development database and applied {len(applied)} migration(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
