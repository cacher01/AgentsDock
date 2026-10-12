from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.domain.models import ModelRequest, ModelResponse


@runtime_checkable
class ModelClient(Protocol):
    async def invoke(self, request: ModelRequest) -> ModelResponse: ...
