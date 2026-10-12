import pytest

from app.application.authorization import (
    AuthorizationDeniedError,
    AuthorizationPolicy,
)
from app.domain.identity import (
    AccessKind,
    Capability,
    GranteeType,
    MembershipStatus,
    ResourceRef,
    ResourceType,
    ShareGrant,
    SharePermission,
    WorkspaceMembership,
    WorkspaceRole,
    build_request_context,
)


from app.domain.models import RequestContext

WORKSPACE_A = "workspace_a"
WORKSPACE_B = "workspace_b"
OWNER = "user_owner"
MEMBER = "user_member"
ADMIN = "user_admin"


def _context(user_id, role=WorkspaceRole.MEMBER, workspace_id=WORKSPACE_A):
    return build_request_context(
        request_id=f"request_{user_id}",
        user_id=user_id,
        workspace_id=workspace_id,
        role=role,
    )


def _membership(context, status=MembershipStatus.ACTIVE):
    return WorkspaceMembership(
        workspace_id=context.workspace_id,
        user_id=context.user_id,
        role=WorkspaceRole(context.role),
        status=status,
    )


def _resource(workspace_id=WORKSPACE_A, owner_user_id=OWNER):
    return ResourceRef(
        resource_type=ResourceType.TASK,
        resource_id="task_1",
        workspace_id=workspace_id,
        owner_user_id=owner_user_id,
    )


def _grant(
    *,
    grantee_type=GranteeType.USER,
    grantee_id=MEMBER,
    permission=SharePermission.READ,
    workspace_id=WORKSPACE_A,
):
    return ShareGrant(
        workspace_id=workspace_id,
        resource_type=ResourceType.TASK,
        resource_id="task_1",
        grantee_type=grantee_type,
        grantee_id=grantee_id,
        permission=permission,
        created_by=OWNER,
    )


def _decide(context, access_kind, shares=(), resource=None, membership=None):
    return AuthorizationPolicy().decide(
        context=context,
        membership=membership or _membership(context),
        resource=resource or _resource(),
        access_kind=access_kind,
        shares=shares,
    )


def test_role_capabilities_do_not_give_admin_private_content_access():
    member = _context(MEMBER)
    admin = _context(ADMIN, WorkspaceRole.ADMIN)

    assert member.can(Capability.TASK_CREATE.value)
    assert not member.can(Capability.GOVERNANCE_VIEW.value)
    assert admin.can(Capability.GOVERNANCE_VIEW.value)
    assert "content:read_all" not in admin.capabilities


def test_owner_access_still_requires_task_capability():
    restricted = RequestContext(
        request_id="request_restricted",
        user_id=OWNER,
        workspace_id=WORKSPACE_A,
        role=WorkspaceRole.MEMBER.value,
        capabilities=frozenset(),
    )

    assert not _decide(restricted, AccessKind.CONTENT).allowed
    assert not _decide(restricted, AccessKind.COMMENT).allowed


def test_governance_capability_does_not_turn_member_into_admin():
    forged = RequestContext(
        request_id="request_forged",
        user_id=MEMBER,
        workspace_id=WORKSPACE_A,
        role=WorkspaceRole.MEMBER.value,
        capabilities=frozenset({Capability.GOVERNANCE_VIEW.value}),
    )

    assert not _decide(forged, AccessKind.GOVERNANCE_METADATA).allowed

def test_owner_can_read_comment_and_manage_sharing():
    owner = _context(OWNER)

    for access_kind in (
        AccessKind.CONTENT,
        AccessKind.COMMENT,
        AccessKind.MANAGE_SHARE,
    ):
        assert _decide(owner, access_kind).allowed


def test_unshared_resource_is_private_from_other_member():
    member = _context(MEMBER)

    decision = _decide(member, AccessKind.CONTENT)

    assert not decision.allowed
    assert "private" in decision.reason


@pytest.mark.parametrize(
    ("permission", "content_allowed", "comment_allowed"),
    [
        (SharePermission.READ, True, False),
        (SharePermission.COMMENT, True, True),
    ],
)
def test_user_share_permissions_are_explicit(
    permission, content_allowed, comment_allowed
):
    member = _context(MEMBER)
    shares = [_grant(permission=permission)]

    assert _decide(member, AccessKind.CONTENT, shares).allowed is content_allowed
    assert _decide(member, AccessKind.COMMENT, shares).allowed is comment_allowed


def test_workspace_share_applies_only_inside_same_workspace():
    member = _context(MEMBER)
    workspace_grant = _grant(
        grantee_type=GranteeType.WORKSPACE,
        grantee_id=WORKSPACE_A,
    )
    other_workspace_context = _context(MEMBER, workspace_id=WORKSPACE_B)

    assert _decide(member, AccessKind.CONTENT, [workspace_grant]).allowed
    assert not _decide(
        other_workspace_context,
        AccessKind.CONTENT,
        [workspace_grant],
        resource=_resource(workspace_id=WORKSPACE_A),
    ).allowed


def test_cross_workspace_user_grant_never_overrides_workspace_boundary():
    other_workspace_context = _context(MEMBER, workspace_id=WORKSPACE_B)
    forged_grant = _grant(grantee_id=MEMBER, workspace_id=WORKSPACE_A)

    decision = _decide(
        other_workspace_context,
        AccessKind.CONTENT,
        [forged_grant],
        resource=_resource(workspace_id=WORKSPACE_A),
    )

    assert not decision.allowed
    assert "another workspace" in decision.reason


def test_admin_can_view_governance_but_not_private_content():
    admin = _context(ADMIN, WorkspaceRole.ADMIN)

    assert _decide(admin, AccessKind.GOVERNANCE_METADATA).allowed
    assert not _decide(admin, AccessKind.CONTENT).allowed
    assert not _decide(admin, AccessKind.MANAGE_SHARE).allowed


def test_member_cannot_use_governance_projection():
    member = _context(MEMBER)

    assert not _decide(member, AccessKind.GOVERNANCE_METADATA).allowed


@pytest.mark.parametrize(
    "status",
    [MembershipStatus.INVITED, MembershipStatus.DISABLED],
)
def test_inactive_membership_denies_all_access(status):
    owner = _context(OWNER)

    decision = _decide(
        owner,
        AccessKind.CONTENT,
        membership=_membership(owner, status),
    )

    assert not decision.allowed
    assert "active membership" in decision.reason


def test_require_raises_a_stable_permission_error():
    member = _context(MEMBER)

    with pytest.raises(AuthorizationDeniedError, match="private"):
        AuthorizationPolicy().require(
            context=member,
            membership=_membership(member),
            resource=_resource(),
            access_kind=AccessKind.CONTENT,
        )