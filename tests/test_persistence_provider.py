from __future__ import annotations

import asyncio

import pytest

from app.application.ports.runtime_assets import RuntimeAssetRepository
from app.application.ports.runtime_facts import RuntimeFactRepository
from app.application.ports.scheduler import TaskScheduler
from app.application.ports.tool_governance import (
    RuntimeToolGovernanceRepository,
)
from app.application.ports.runtime_repositories import (
    RuntimeExecutionRepository,
    ScopedCheckpointStore,
)
from app.application.ports.scoped_repositories import (
    GovernanceRepository,
    ResourceShareRepository,
    ScopedTaskRepository,
)
from app.core.config import Settings
from app.persistence.provider import create_platform_repository_provider


def test_platform_repository_provider_is_disabled_by_default() -> None:
    provider = create_platform_repository_provider(Settings())

    assert provider.enabled is False
    with pytest.raises(RuntimeError, match="disabled"):
        provider.get()


def test_settings_reject_invalid_platform_repository_configuration() -> None:
    with pytest.raises(ValueError, match="unsupported"):
        Settings(platform_repository_backend="sqlite")

    with pytest.raises(ValueError, match="PLATFORM_DATABASE_URL"):
        Settings(platform_repository_backend="postgres")


def test_postgres_platform_repository_provider_is_lazy_and_cached() -> None:
    pytest.importorskip("sqlalchemy")
    pytest.importorskip("psycopg")
    settings = Settings(
        platform_repository_backend="postgres",
        platform_database_url=("postgresql+psycopg://user:password@localhost/platform_test"),
    )
    provider = create_platform_repository_provider(settings)

    assert provider.enabled is True
    first = provider.get()
    second = provider.get()

    assert first is second
    assert isinstance(first.tasks, ScopedTaskRepository)
    assert isinstance(first.shares, ResourceShareRepository)
    assert isinstance(first.governance, GovernanceRepository)
    assert isinstance(first.execution, RuntimeExecutionRepository)
    assert isinstance(first.facts, RuntimeFactRepository)
    assert isinstance(first.assets, RuntimeAssetRepository)
    assert isinstance(first.scheduler, TaskScheduler)
    assert isinstance(first.tools, RuntimeToolGovernanceRepository)
    assert isinstance(first.checkpoints, ScopedCheckpointStore)
    assert first.engine.url.drivername == "postgresql+psycopg"

    asyncio.run(provider.close())
