from __future__ import annotations

from typing import Any, Protocol

from app.domain.models import ArtifactSnapshot


class FileStore(Protocol):
    async def write(
        self,
        task_id: str,
        filename: str,
        format: str,
        *,
        content: Any = None,
        rows: Any = None,
    ) -> ArtifactSnapshot: ...

    async def read(self, artifact: ArtifactSnapshot) -> bytes: ...
