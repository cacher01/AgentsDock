from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone

import pytest

from app.core.enums import WorkflowType
from app.domain.identity import WorkspaceRole, build_request_context
from app.domain.models import (
    AgentRunSnapshot,
    CompletionState,
    EvidenceSnapshot,
    ResultSnapshot,
    RunSnapshot,
    RunStatus,
    ToolCallSnapshot,
)
from app.persistence.postgres import (
    PostgresRuntimeExecutionRepository,
    PostgresRuntimeFactRepository,
    PostgresScopedTaskRepository,
    create_postgres_engine,
    create_session_factory,
)
from scripts.postgres_dev_reset import (
    reset_development_database,
    validate_dev_database_url,
)


def _context(user_id: str, workspace_id: str):
    return build_request_context(
        request_id=f"request_{user_id}",
        user_id=user_id,
        workspace_id=workspace_id,
        role=WorkspaceRole.MEMBER,
    )


def _seed(psycopg, database_url: str) -> None:
    with psycopg.connect(database_url, autocommit=True) as connection:
        connection.execute(
            """
            insert into users (id, email, display_name, password_hash)
            values
                ('fact_owner', 'fact-owner@example.com', 'Owner', 'hash'),
                ('fact_other', 'fact-other@example.com', 'Other', 'hash')
            """
        )
        connection.execute(
            """
            insert into workspaces (id, name, slug, created_by)
            values
                ('fact_workspace', 'Facts', 'facts-workspace', 'fact_owner'),
                ('other_workspace', 'Other', 'facts-other', 'fact_other')
            """
        )
        connection.execute(
            """
            insert into workspace_memberships (
                workspace_id, user_id, role, status, joined_at
            ) values
                ('fact_workspace', 'fact_owner', 'member', 'active', now()),
                ('other_workspace', 'fact_other', 'member', 'active', now())
            """
        )


def test_runtime_facts_preserve_scope_sequence_and_results() -> None:
    database_url = os.getenv("TEST_POSTGRES_URL")
    if not database_url:
        pytest.skip("TEST_POSTGRES_URL is not configured")
    psycopg = pytest.importorskip("psycopg")
    normalized = validate_dev_database_url(database_url)
    reset_development_database(normalized)
    _seed(psycopg, normalized)

    async def scenario() -> None:
        engine = create_postgres_engine(normalized)
        factory = create_session_factory(engine)
        tasks = PostgresScopedTaskRepository(factory)
        execution = PostgresRuntimeExecutionRepository(factory)
        facts = PostgresRuntimeFactRepository(factory)
        owner = _context("fact_owner", "fact_workspace")
        outsider = _context("fact_other", "other_workspace")
        now = datetime.now(timezone.utc)

        try:
            task = await tasks.create_task(owner, "fact task")
            run = await execution.create_run(
                owner,
                RunSnapshot(
                    run_id="fact_run",
                    task_id=task.task_id,
                    attempt=1,
                    status=RunStatus.RUNNING,
                ),
            )
            agent = await facts.create_agent_run(
                owner,
                AgentRunSnapshot(
                    agent_run_id="fact_agent",
                    run_id=run.run_id,
                    role="researcher",
                    goal="collect evidence",
                    status="running",
                    capability_snapshot={"tools": ["web_search"]},
                    created_at=now,
                    updated_at=now,
                ),
            )
            first = await facts.append_event(
                owner,
                task_id=task.task_id,
                run_id=run.run_id,
                agent_run_id=agent.agent_run_id,
                event_type="agent_started",
                payload={"role": "researcher"},
            )
            second = await facts.append_event(
                owner,
                task_id=task.task_id,
                run_id=run.run_id,
                event_type="evidence_saved",
                payload={"count": 1},
            )
            assert (first.sequence, second.sequence) == (1, 2)

            call = await facts.save_tool_call(
                owner,
                ToolCallSnapshot(
                    tool_call_id="fact_tool",
                    task_id=task.task_id,
                    agent_run_id=agent.agent_run_id,
                    tool_key="web_search",
                    status="completed",
                    arguments={"query": "example"},
                    result_ref="evidence_1",
                    created_at=now,
                    completed_at=now,
                ),
            )
            assert call.tool_key == "web_search"

            evidence = await facts.save_evidence(
                owner,
                EvidenceSnapshot(
                    evidence_id="fact_evidence",
                    task_id=task.task_id,
                    source_type="web",
                    source_ref="https://example.com",
                    locator={"line": 1},
                    title="Example",
                    snippet="Evidence",
                    checksum="abc",
                    rank=1,
                    created_at=now,
                ),
            )
            assert evidence.rank == 1

            result = await facts.save_result(
                owner,
                ResultSnapshot(
                    result_id="fact_result",
                    task_id=task.task_id,
                    answer="Partial answer",
                    completion_state=CompletionState.DEGRADED,
                    citations=({"evidence_id": evidence.evidence_id},),
                    limitations=("source coverage incomplete",),
                    confidence=0.6,
                    used_workflow=WorkflowType.RESEARCH,
                    created_at=now,
                ),
            )
            assert result.completion_state == CompletionState.DEGRADED
            assert (await facts.get_result(owner, task.task_id)) == result

            assert await facts.list_events(outsider, task.task_id) == []
            assert await facts.list_agent_runs(outsider, task.task_id) == []
            assert await facts.list_tool_calls(outsider, task.task_id) == []
            assert await facts.list_evidence(outsider, task.task_id) == []
            assert await facts.get_result(outsider, task.task_id) is None
        finally:
            await engine.dispose()

    asyncio.run(scenario())
