from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from app.domain.models import RequestContext


class WorkspaceRole(str, Enum):
    ADMIN = "admin"
    MEMBER = "member"


class MembershipStatus(str, Enum):
    INVITED = "invited"
    ACTIVE = "active"
    DISABLED = "disabled"


class ResourceType(str, Enum):
    TASK = "task"
    INPUT_FILE = "input_file"
    ARTIFACT = "artifact"


class SharePermission(str, Enum):
    READ = "read"
    COMMENT = "comment"


class GranteeType(str, Enum):
    USER = "user"
    WORKSPACE = "workspace"


class AccessKind(str, Enum):
    CONTENT = "content"
    COMMENT = "comment"
    MANAGE_SHARE = "manage_share"
    GOVERNANCE_METADATA = "governance_metadata"


@dataclass(frozen=True, slots=True)
class WorkspaceMembership:
    workspace_id: str
    user_id: str
    role: WorkspaceRole
    status: MembershipStatus
    joined_at: datetime | None = None

    def matches(self, context: RequestContext) -> bool:
        return (
            self.workspace_id == context.workspace_id
            and self.user_id == context.user_id
            and self.status == MembershipStatus.ACTIVE
            and self.role.value == context.role
        )


@dataclass(frozen=True, slots=True)
class ResourceRef:
    resource_type: ResourceType
    resource_id: str
    workspace_id: str
    owner_user_id: str


@dataclass(frozen=True, slots=True)
class ShareGrant:
    workspace_id: str
    resource_type: ResourceType
    resource_id: str
    grantee_type: GranteeType
    grantee_id: str
    permission: SharePermission
    created_by: str

    def applies_to(self, context: RequestContext, resource: ResourceRef) -> bool:
        if (
            self.workspace_id != context.workspace_id
            or self.workspace_id != resource.workspace_id
            or self.resource_type != resource.resource_type
            or self.resource_id != resource.resource_id
        ):
            return False
        if self.grantee_type == GranteeType.USER:
            return self.grantee_id == context.user_id
        return self.grantee_id == context.workspace_id


@dataclass(frozen=True, slots=True)
class AuthorizationDecision:
    allowed: bool
    reason: str
    access_kind: AccessKind
