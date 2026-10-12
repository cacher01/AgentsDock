from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.domain.models import (
    ArtifactSnapshot,
    DocumentBlockSnapshot,
    InputFileSnapshot,
    LLMCallSnapshot,
    RequestContext,
)


@runtime_checkable
class RuntimeAssetRepository(Protocol):
    async def save_input_file(
        self, context: RequestContext, file: InputFileSnapshot
    ) -> InputFileSnapshot: ...

    async def update_parse_status(
        self,
        context: RequestContext,
        input_file_id: str,
        status: str,
        error: str = "",
    ) -> InputFileSnapshot: ...

    async def list_input_files(
        self, context: RequestContext, task_id: str | None = None
    ) -> list[InputFileSnapshot]: ...

    async def attach_input_files(
        self,
        context: RequestContext,
        task_id: str,
        input_file_ids: list[str],
    ) -> None: ...

    async def save_artifact(
        self, context: RequestContext, artifact: ArtifactSnapshot
    ) -> ArtifactSnapshot: ...

    async def get_artifact(
        self, context: RequestContext, artifact_id: str
    ) -> ArtifactSnapshot | None: ...

    async def list_artifacts(
        self, context: RequestContext, task_id: str
    ) -> list[ArtifactSnapshot]: ...

    async def record_llm_call(
        self, context: RequestContext, call: LLMCallSnapshot
    ) -> LLMCallSnapshot: ...

    async def list_llm_calls(
        self, context: RequestContext, task_id: str
    ) -> list[LLMCallSnapshot]: ...


@runtime_checkable
class DocumentRepository(Protocol):
    async def replace_blocks(
        self,
        context: RequestContext,
        input_file_id: str,
        blocks: list[DocumentBlockSnapshot],
    ) -> None: ...

    async def list_task_blocks(
        self,
        context: RequestContext,
        task_id: str,
    ) -> list[DocumentBlockSnapshot]: ...
