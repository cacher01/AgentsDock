from __future__ import annotations

import argparse
import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


ROOT = Path(__file__).parents[1]
DEFAULT_MIGRATION_DIR = ROOT / "infra" / "migrations"
MIGRATION_PATTERN = re.compile(r"^(?P<version>\d{4})_[a-z0-9_]+\.sql$")


@dataclass(frozen=True, slots=True)
class Migration:
    version: str
    name: str
    path: Path
    sql: str
    checksum: str


def discover_migrations(directory: Path = DEFAULT_MIGRATION_DIR) -> list[Migration]:
    migrations = []
    for path in sorted(directory.glob("*.sql")):
        match = MIGRATION_PATTERN.fullmatch(path.name)
        if match is None:
            continue
        sql = path.read_text(encoding="utf-8")
        migrations.append(
            Migration(
                version=match.group("version"),
                name=path.name,
                path=path,
                sql=sql,
                checksum=hashlib.sha256(sql.encode("utf-8")).hexdigest(),
            )
        )
    validate_migrations(migrations)
    return migrations


def validate_migrations(migrations: Iterable[Migration]) -> None:
    items = list(migrations)
    versions = [item.version for item in items]
    if versions != sorted(versions) or len(versions) != len(set(versions)):
        raise ValueError("migration versions must be unique and ordered")
    for item in items:
        normalized = re.sub(r"--.*?$", "", item.sql, flags=re.MULTILINE).lower()
        is_schema_migration = "create table" in normalized or "alter table" in normalized
        is_seed_migration = "insert into" in normalized
        if not (is_schema_migration or is_seed_migration):
            raise ValueError(f"{item.name} contains neither schema nor seed changes")
        if re.search(r"\bdrop\s+(table|schema|database)\b", normalized):
            raise ValueError(f"{item.name} contains destructive production SQL")


def require_postgres_url(database_url: str) -> str:
    if not database_url.startswith(("postgresql://", "postgresql+psycopg://")):
        raise ValueError("DATABASE_URL must use PostgreSQL")
    return database_url.replace("postgresql+psycopg://", "postgresql://", 1)


def apply_migrations(database_url: str, *, dry_run: bool = False) -> list[str]:
    migrations = discover_migrations()
    if dry_run:
        return [f"{item.version} {item.name} {item.checksum}" for item in migrations]

    try:
        import psycopg
    except ImportError as exc:
        raise RuntimeError(
            "PostgreSQL support is not installed; install the 'postgres' extra"
        ) from exc

    applied_now = []
    with psycopg.connect(require_postgres_url(database_url), autocommit=True) as conn:
        with conn.transaction():
            conn.execute(
                """
                create table if not exists schema_migrations (
                    version text primary key,
                    name text not null,
                    checksum text not null,
                    applied_at timestamptz not null default now()
                )
                """
            )
        rows = conn.execute(
            "select version, checksum from schema_migrations order by version"
        ).fetchall()
        applied = {str(row[0]): str(row[1]) for row in rows}
        for migration in migrations:
            previous_checksum = applied.get(migration.version)
            if previous_checksum is not None:
                if previous_checksum != migration.checksum:
                    raise RuntimeError(
                        f"checksum mismatch for applied migration {migration.version}"
                    )
                continue
            with conn.transaction():
                conn.execute(migration.sql)
                conn.execute(
                    """
                    insert into schema_migrations (version, name, checksum)
                    values (%s, %s, %s)
                    """,
                    (migration.version, migration.name, migration.checksum),
                )
            applied_now.append(migration.name)
    return applied_now


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate or apply versioned PostgreSQL migrations."
    )
    parser.add_argument(
        "--database-url",
        default=os.getenv("PLATFORM_DATABASE_URL", os.getenv("DATABASE_URL", "")),
        help=("PostgreSQL URL; defaults to PLATFORM_DATABASE_URL, then DATABASE_URL."),
    )
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="Validate local migration ordering and safety without connecting.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print ordered migration names and checksums without connecting.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.validate_only:
        migrations = discover_migrations()
        print(f"validated {len(migrations)} migration(s)")
        return 0
    if args.dry_run:
        for line in apply_migrations(args.database_url, dry_run=True):
            print(line)
        return 0
    if not args.database_url:
        raise SystemExit("DATABASE_URL is required when applying migrations")
    applied = apply_migrations(args.database_url)
    print(f"applied {len(applied)} migration(s)")
    for name in applied:
        print(name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
