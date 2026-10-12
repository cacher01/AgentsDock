from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.application.ports.tool_governance import EnabledTool
from app.domain.models import RequestContext


class PostgresRuntimeToolGovernanceRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def list_enabled(self, context: RequestContext) -> list[EnabledTool]:
        async with self._session_factory() as session:
            rows = (
                (
                    await session.execute(
                        text(
                            """
                        select d.tool_key, b.policy, b.credential_ref
                        from workspace_tool_bindings b
                        join tool_definitions d
                          on d.id=b.tool_definition_id
                        where b.workspace_id=:workspace_id
                          and b.enabled=true
                        order by d.tool_key, d.version desc
                        """
                        ),
                        {"workspace_id": context.workspace_id},
                    )
                )
                .mappings()
                .all()
            )
        return [
            EnabledTool(
                key=str(row["tool_key"]),
                policy=dict(row["policy"]),
                credential_ref=row["credential_ref"],
            )
            for row in rows
        ]
