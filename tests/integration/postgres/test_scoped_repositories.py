import asyncio
import os

import pytest

from app.domain.identity import (
    GranteeType,
    ResourceRef,
    ResourceType,
    ShareGrant,
    SharePermission,
    WorkspaceRole,
    build_request_context,
)
from app.persistence.postgres import (
    PostgresCollaborationRepository,
    PostgresGovernanceRepository,
    PostgresResourceShareRepository,
    PostgresScopedTaskRepository,
    create_postgres_engine,
    create_session_factory,
)
from scripts.postgres_dev_reset import (
    reset_development_database,
    validate_dev_database_url,
)


def _context(user_id, workspace_id, role=WorkspaceRole.MEMBER):
    return build_request_context(
        request_id=f"request_{user_id}",
        user_id=user_id,
        workspace_id=workspace_id,
        role=role,
    )


def _seed_database(psycopg, database_url):
    with psycopg.connect(database_url, autocommit=True) as connection:
        connection.execute(
            """
            insert into users (id, email, display_name, password_hash)
            values
                ('owner_a', 'owner-a@example.com', 'Owner A', 'hash'),
                ('member_a', 'member-a@example.com', 'Member A', 'hash'),
                ('admin_a', 'admin-a@example.com', 'Admin A', 'hash'),
                ('owner_b', 'owner-b@example.com', 'Owner B', 'hash')
            """
        )
        connection.execute(
            """
            insert into workspaces (id, name, slug, created_by)
            values
                ('workspace_a', 'Workspace A', 'workspace-a', 'owner_a'),
                ('workspace_b', 'Workspace B', 'workspace-b', 'owner_b')
            """
        )
        connection.execute(
            """
            insert into workspace_memberships (
                workspace_id, user_id, role, status, joined_at
            )
            values
                ('workspace_a', 'owner_a', 'member', 'active', now()),
                ('workspace_a', 'member_a', 'member', 'active', now()),
                ('workspace_a', 'admin_a', 'admin', 'active', now()),
                ('workspace_b', 'owner_b', 'member', 'active', now())
            """
        )
        connection.execute(
            """
            insert into model_connections (
                id, workspace_id, name, provider_type, base_url,
                secret_ciphertext, secret_nonce, secret_key_version,
                status, created_by
            ) values (
                'connection_a', 'workspace_a', 'Team Model',
                'openai_compatible', 'https://model.example/v1',
                decode('00', 'hex'), decode('00', 'hex'), 1,
                'active', 'admin_a'
            )
            """
        )
        connection.execute(
            """
            insert into model_variants (
                id, connection_id, model_name, capabilities, enabled
            ) values (
                'variant_a', 'connection_a', 'model-a', '["chat"]', true
            )
            """
        )


def test_scoped_repositories_enforce_sharing_and_governance_boundaries():
    database_url = os.getenv("TEST_POSTGRES_URL")
    if not database_url:
        pytest.skip("TEST_POSTGRES_URL is not configured")
    psycopg = pytest.importorskip("psycopg")
    normalized = validate_dev_database_url(database_url)
    reset_development_database(normalized)
    _seed_database(psycopg, normalized)

    async def scenario():
        engine = create_postgres_engine(normalized)
        factory = create_session_factory(engine)
        tasks = PostgresScopedTaskRepository(factory)
        shares = PostgresResourceShareRepository(factory)
        governance = PostgresGovernanceRepository(factory)
        collaboration = PostgresCollaborationRepository(factory)

        owner = _context("owner_a", "workspace_a")
        member = _context("member_a", "workspace_a")
        admin = _context("admin_a", "workspace_a", WorkspaceRole.ADMIN)
        outsider = _context("owner_b", "workspace_b")

        try:
            task = await tasks.create_task(
                owner,
                "private task body",
                idempotency_key="request-1",
                requested_model_variant_id="variant_a",
            )
            duplicate = await tasks.create_task(
                owner,
                "must not replace original",
                idempotency_key="request-1",
                requested_model_variant_id="variant_a",
            )
            assert duplicate.task_id == task.task_id
            assert duplicate.input_text == "private task body"
            assert duplicate.requested_model_variant_id == "variant_a"

            with pytest.raises(ValueError, match="not enabled"):
                await tasks.create_task(
                    owner,
                    "invalid model",
                    requested_model_variant_id="variant_missing",
                )

            assert await tasks.get_task(member, task.task_id) is None
            assert await tasks.get_task(admin, task.task_id) is None
            assert await tasks.get_task(outsider, task.task_id) is None

            resource = ResourceRef(
                resource_type=ResourceType.TASK,
                resource_id=task.task_id,
                workspace_id="workspace_a",
                owner_user_id="owner_a",
            )
            grant = ShareGrant(
                workspace_id="workspace_a",
                resource_type=ResourceType.TASK,
                resource_id=task.task_id,
                grantee_type=GranteeType.USER,
                grantee_id="member_a",
                permission=SharePermission.READ,
                created_by="owner_a",
            )
            await shares.add_grant(owner, resource, grant)

            visible = await tasks.get_task(member, task.task_id)
            assert visible is not None
            assert visible.input_text == "private task body"
            assert await tasks.get_task(admin, task.task_id) is None

            with pytest.raises(PermissionError):
                await collaboration.create_comment(member, task.task_id, "read only")

            comment_grant = ShareGrant(
                workspace_id="workspace_a",
                resource_type=ResourceType.TASK,
                resource_id=task.task_id,
                grantee_type=GranteeType.USER,
                grantee_id="member_a",
                permission=SharePermission.COMMENT,
                created_by="owner_a",
            )
            await shares.add_grant(owner, resource, comment_grant)
            comment = await collaboration.create_comment(
                member,
                task.task_id,
                "Please review this result.",
                ("owner_a",),
            )
            assert comment.author_name == "Member A"
            assert await collaboration.list_comments(owner, task.task_id) == [comment]
            owner_notifications = await collaboration.list_notifications(owner, unread_only=True)
            assert owner_notifications[0].notification_type == "task_mention"
            read = await collaboration.mark_notification_read(
                owner, owner_notifications[0].notification_id
            )
            assert read.read_at is not None
            member_notifications = await collaboration.list_notifications(member)
            assert any(item.notification_type == "resource_shared" for item in member_notifications)

            projection = await governance.get_task_metadata(admin, task.task_id)
            assert projection is not None
            assert projection.owner_user_id == "owner_a"
            assert not hasattr(projection, "input_text")

            with pytest.raises(PermissionError):
                await governance.get_task_metadata(member, task.task_id)

            grants = await shares.list_grants(owner, resource)
            assert grants == [grant]
            await shares.remove_grant(owner, resource, grant)
            assert await tasks.get_task(member, task.task_id) is None
        finally:
            await engine.dispose()

    asyncio.run(scenario())
