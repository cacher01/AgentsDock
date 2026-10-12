from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.application.ports.collaboration import CollaborationRepository
from app.application.ports.runtime_assets import (
    DocumentRepository,
    RuntimeAssetRepository,
)
from app.application.ports.runtime_facts import RuntimeFactRepository
from app.application.ports.scheduler import TaskScheduler
from app.application.ports.runtime_repositories import (
    RuntimeExecutionRepository,
    ScopedCheckpointStore,
)
from app.application.ports.tool_governance import (
    RuntimeToolGovernanceRepository,
)
from app.application.ports.scoped_repositories import (
    GovernanceRepository,
    ResourceShareRepository,
    ScopedTaskRepository,
)
from app.core.config import Settings


@dataclass(slots=True)
class PlatformRepositoryBundle:
    engine: Any
    tasks: ScopedTaskRepository
    shares: ResourceShareRepository
    governance: GovernanceRepository
    execution: RuntimeExecutionRepository
    checkpoints: ScopedCheckpointStore
    facts: RuntimeFactRepository
    assets: RuntimeAssetRepository
    documents: DocumentRepository
    tools: RuntimeToolGovernanceRepository
    collaboration: CollaborationRepository
    scheduler: TaskScheduler

    async def close(self) -> None:
        await self.engine.dispose()


class PlatformRepositoryProvider:
    def __init__(self, *, backend: str, database_url: str) -> None:
        self._backend = backend
        self._database_url = database_url
        self._bundle: PlatformRepositoryBundle | None = None

    @property
    def enabled(self) -> bool:
        return self._backend != "disabled"

    def get(self) -> PlatformRepositoryBundle:
        if not self.enabled:
            raise RuntimeError("platform repositories are disabled")
        if self._bundle is None:
            self._bundle = self._create_postgres_bundle()
        return self._bundle

    async def close(self) -> None:
        if self._bundle is not None:
            await self._bundle.close()
            self._bundle = None

    def _create_postgres_bundle(self) -> PlatformRepositoryBundle:
        if self._backend != "postgres":
            raise ValueError(f"unsupported platform repository backend: {self._backend}")
        from app.persistence.postgres import (
            PostgresCollaborationRepository,
            PostgresDocumentRepository,
            PostgresGovernanceRepository,
            PostgresResourceShareRepository,
            PostgresRuntimeAssetRepository,
            PostgresRuntimeExecutionRepository,
            PostgresRuntimeFactRepository,
            PostgresRuntimeToolGovernanceRepository,
            PostgresScopedCheckpointStore,
            PostgresScopedTaskRepository,
            PostgresTaskScheduler,
            create_postgres_engine,
            create_session_factory,
        )

        engine = create_postgres_engine(self._database_url)
        sessions = create_session_factory(engine)
        return PlatformRepositoryBundle(
            engine=engine,
            tasks=PostgresScopedTaskRepository(sessions),
            shares=PostgresResourceShareRepository(sessions),
            governance=PostgresGovernanceRepository(sessions),
            execution=PostgresRuntimeExecutionRepository(sessions),
            checkpoints=PostgresScopedCheckpointStore(sessions),
            facts=PostgresRuntimeFactRepository(sessions),
            assets=PostgresRuntimeAssetRepository(sessions),
            documents=PostgresDocumentRepository(sessions),
            tools=PostgresRuntimeToolGovernanceRepository(sessions),
            collaboration=PostgresCollaborationRepository(sessions),
            scheduler=PostgresTaskScheduler(sessions),
        )


def create_platform_repository_provider(
    settings: Settings,
) -> PlatformRepositoryProvider:
    return PlatformRepositoryProvider(
        backend=settings.platform_repository_backend,
        database_url=settings.platform_database_url,
    )
