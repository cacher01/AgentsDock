from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol, runtime_checkable

from app.domain.models import RequestContext


@dataclass(frozen=True, slots=True)
class EnabledTool:
    key: str
    policy: Mapping[str, Any]
    credential_ref: str | None = None


@runtime_checkable
class RuntimeToolGovernanceRepository(Protocol):
    async def list_enabled(self, context: RequestContext) -> list[EnabledTool]: ...
