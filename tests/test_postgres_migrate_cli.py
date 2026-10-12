from scripts.postgres_migrate import build_parser


def test_migration_cli_prefers_platform_database_url(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://legacy.example/legacy")
    monkeypatch.setenv(
        "PLATFORM_DATABASE_URL",
        "postgresql://platform.example/platform",
    )

    args = build_parser().parse_args([])

    assert args.database_url == "postgresql://platform.example/platform"
