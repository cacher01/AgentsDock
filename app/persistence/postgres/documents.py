from __future__ import annotations

import json

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.models import DocumentBlockSnapshot, RequestContext


class PostgresDocumentRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def replace_blocks(
        self,
        context: RequestContext,
        input_file_id: str,
        blocks: list[DocumentBlockSnapshot],
    ) -> None:
        for expected, block in enumerate(blocks):
            if block.input_file_id != input_file_id or block.block_index != expected:
                raise ValueError("document blocks must be ordered and contiguous")
        async with self._session_factory() as session:
            async with session.begin():
                owned = (
                    await session.execute(
                        text(
                            """
                            select 1 from input_files
                            where id=:file_id and workspace_id=:workspace_id
                              and owner_user_id=:user_id
                            """
                        ),
                        {
                            "file_id": input_file_id,
                            "workspace_id": context.workspace_id,
                            "user_id": context.user_id,
                        },
                    )
                ).first()
                if owned is None:
                    raise ValueError("input file not found in owner scope")
                await session.execute(
                    text("delete from document_blocks where input_file_id=:file_id"),
                    {"file_id": input_file_id},
                )
                if blocks:
                    await session.execute(
                        text(
                            """
                            insert into document_blocks (
                                id, input_file_id, block_index, block_type,
                                locator, text, metadata
                            ) values (
                                :id, :file_id, :block_index, :block_type,
                                cast(:locator as jsonb), :text,
                                cast(:metadata as jsonb)
                            )
                            """
                        ),
                        [
                            {
                                "id": block.document_block_id,
                                "file_id": input_file_id,
                                "block_index": block.block_index,
                                "block_type": block.block_type,
                                "locator": json.dumps(block.locator),
                                "text": block.text,
                                "metadata": json.dumps(block.metadata),
                            }
                            for block in blocks
                        ],
                    )

    async def list_task_blocks(
        self,
        context: RequestContext,
        task_id: str,
    ) -> list[DocumentBlockSnapshot]:
        async with self._session_factory() as session:
            rows = (
                (
                    await session.execute(
                        text(
                            """
                        select b.id, b.input_file_id, b.block_index,
                               b.block_type, b.locator, b.text, b.metadata
                        from document_blocks b
                        join input_files f on f.id=b.input_file_id
                        where f.task_id=:task_id
                          and f.workspace_id=:workspace_id
                          and f.owner_user_id=:user_id
                        order by f.created_at, b.block_index
                        """
                        ),
                        {
                            "task_id": task_id,
                            "workspace_id": context.workspace_id,
                            "user_id": context.user_id,
                        },
                    )
                )
                .mappings()
                .all()
            )
        return [
            DocumentBlockSnapshot(
                document_block_id=str(row["id"]),
                input_file_id=str(row["input_file_id"]),
                block_index=int(row["block_index"]),
                block_type=str(row["block_type"]),
                locator=dict(row["locator"]),
                text=str(row["text"]),
                metadata=dict(row["metadata"]),
            )
            for row in rows
        ]
