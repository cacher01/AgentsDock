from __future__ import annotations

from collections.abc import Iterable

from app.domain.identity import (
    AccessKind,
    AuthorizationDecision,
    Capability,
    ResourceRef,
    ShareGrant,
    SharePermission,
    WorkspaceMembership,
    WorkspaceRole,
)
from app.domain.models import RequestContext


class AuthorizationDeniedError(PermissionError):
    pass


class AuthorizationPolicy:
    def decide(
        self,
        *,
        context: RequestContext,
        membership: WorkspaceMembership,
        resource: ResourceRef,
        access_kind: AccessKind,
        shares: Iterable[ShareGrant] = (),
    ) -> AuthorizationDecision:
        if not membership.matches(context):
            return AuthorizationDecision(
                False, "request context does not match an active membership", access_kind
            )
        if resource.workspace_id != context.workspace_id:
            return AuthorizationDecision(
                False, "resource belongs to another workspace", access_kind
            )
        if access_kind == AccessKind.GOVERNANCE_METADATA:
            return self._governance_decision(context, access_kind)

        is_owner = resource.owner_user_id == context.user_id
        if access_kind == AccessKind.MANAGE_SHARE:
            allowed = is_owner and context.can(Capability.RESOURCE_SHARE_OWNED.value)
            return AuthorizationDecision(
                allowed,
                "resource owner may manage sharing"
                if allowed
                else "only the resource owner may manage sharing",
                access_kind,
            )
        if is_owner:
            capability = (
                Capability.COMMENT_CREATE
                if access_kind == AccessKind.COMMENT
                else Capability.RESOURCE_READ_OWNED
            )
            allowed = context.can(capability.value)
            return AuthorizationDecision(
                allowed,
                "resource owner" if allowed else f"capability is required: {capability.value}",
                access_kind,
            )

        matching = [grant for grant in shares if grant.applies_to(context, resource)]
        if (
            access_kind == AccessKind.CONTENT
            and context.can(Capability.RESOURCE_READ_SHARED.value)
            and any(
                grant.permission in {SharePermission.READ, SharePermission.COMMENT}
                for grant in matching
            )
        ):
            return AuthorizationDecision(True, "resource was explicitly shared", access_kind)
        if (
            access_kind == AccessKind.COMMENT
            and context.can(Capability.COMMENT_CREATE.value)
            and any(grant.permission == SharePermission.COMMENT for grant in matching)
        ):
            return AuthorizationDecision(
                True, "comment permission was explicitly shared", access_kind
            )
        return AuthorizationDecision(
            False, "resource is private and has no matching grant", access_kind
        )

    def require(self, **kwargs) -> AuthorizationDecision:
        decision = self.decide(**kwargs)
        if not decision.allowed:
            raise AuthorizationDeniedError(decision.reason)
        return decision

    @staticmethod
    def _governance_decision(
        context: RequestContext, access_kind: AccessKind
    ) -> AuthorizationDecision:
        allowed = context.role == WorkspaceRole.ADMIN.value and context.can(
            Capability.GOVERNANCE_VIEW.value
        )
        return AuthorizationDecision(
            allowed,
            "administrator may view redacted governance metadata"
            if allowed
            else "administrator governance capability is required",
            access_kind,
        )
