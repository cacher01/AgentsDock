from __future__ import annotations

from enum import Enum

from app.domain.identity.models import WorkspaceRole
from app.domain.models import RequestContext


class Capability(str, Enum):
    TASK_CREATE = "task:create"
    RESOURCE_READ_OWNED = "resource:read_owned"
    RESOURCE_READ_SHARED = "resource:read_shared"
    RESOURCE_SHARE_OWNED = "resource:share_owned"
    COMMENT_CREATE = "comment:create"
    MODEL_USE = "model:use"
    TOOL_USE = "tool:use"
    MEMBERS_MANAGE = "members:manage"
    MODELS_MANAGE = "models:manage"
    TOOLS_MANAGE = "tools:manage"
    BUDGET_MANAGE = "budget:manage"
    GOVERNANCE_VIEW = "governance:view"


_MEMBER_CAPABILITIES = frozenset(
    {
        Capability.TASK_CREATE.value,
        Capability.RESOURCE_READ_OWNED.value,
        Capability.RESOURCE_READ_SHARED.value,
        Capability.RESOURCE_SHARE_OWNED.value,
        Capability.COMMENT_CREATE.value,
        Capability.MODEL_USE.value,
        Capability.TOOL_USE.value,
    }
)

_ROLE_CAPABILITIES = {
    WorkspaceRole.MEMBER: _MEMBER_CAPABILITIES,
    WorkspaceRole.ADMIN: _MEMBER_CAPABILITIES
    | frozenset(
        {
            Capability.MEMBERS_MANAGE.value,
            Capability.MODELS_MANAGE.value,
            Capability.TOOLS_MANAGE.value,
            Capability.BUDGET_MANAGE.value,
            Capability.GOVERNANCE_VIEW.value,
        }
    ),
}


def capabilities_for_role(role: WorkspaceRole) -> frozenset[str]:
    return _ROLE_CAPABILITIES[role]


def build_request_context(
    *,
    request_id: str,
    user_id: str,
    workspace_id: str,
    role: WorkspaceRole,
) -> RequestContext:
    return RequestContext(
        request_id=request_id,
        user_id=user_id,
        workspace_id=workspace_id,
        role=role.value,
        capabilities=capabilities_for_role(role),
    )
