import asyncio
from datetime import datetime, timezone

import pytest

from app.application.ports import CheckpointStore, ModelClient, TaskRepository
from app.core.enums import TaskStatus, WorkflowType
from app.db.database import init_database
from app.db.repositories import Repository
from app.domain import (
    Checkpoint,
    InvalidStateTransition,
    ModelRequest,
    NodeStatus,
    RequestContext,
    RunStatus,
    WorkflowOutcome,
    ensure_node_transition,
    ensure_run_transition,
    ensure_task_transition,
)
from app.llm.client import LLMResponse, MockLLMClient
from app.persistence import (
    InMemoryCheckpointStore,
    LegacyModelClientAdapter,
    SQLiteTaskRepositoryAdapter,
)


def test_request_context_capabilities_are_explicit():
    context = RequestContext(
        request_id="req_1",
        user_id="usr_1",
        workspace_id="wsp_1",
        role="member",
        capabilities=frozenset({"task:create"}),
    )
    assert context.can("task:create")
    assert not context.can("task:admin")


def test_workflow_outcome_rejects_invalid_confidence():
    with pytest.raises(ValueError, match="confidence"):
        WorkflowOutcome(
            answer="answer", used_workflow=WorkflowType.DIRECT, confidence=1.1
        )


@pytest.mark.parametrize(
    ("current", "target", "validator"),
    [
        (TaskStatus.CREATED, TaskStatus.QUEUED, ensure_task_transition),
        (RunStatus.QUEUED, RunStatus.RUNNING, ensure_run_transition),
        (NodeStatus.PENDING, NodeStatus.RUNNING, ensure_node_transition),
        (NodeStatus.BLOCKED, NodeStatus.RUNNING, ensure_node_transition),
    ],
)
def test_valid_state_transitions(current, target, validator):
    validator(current, target)


@pytest.mark.parametrize(
    ("current", "target", "validator"),
    [
        (TaskStatus.COMPLETED, TaskStatus.RUNNING, ensure_task_transition),
        (RunStatus.FAILED, RunStatus.RUNNING, ensure_run_transition),
        (NodeStatus.COMPLETED, NodeStatus.RUNNING, ensure_node_transition),
        (NodeStatus.PENDING, NodeStatus.COMPLETED, ensure_node_transition),
    ],
)
def test_invalid_state_transitions_are_rejected(current, target, validator):
    with pytest.raises(InvalidStateTransition):
        validator(current, target)


def test_sqlite_adapter_satisfies_task_repository_contract():
    adapter = SQLiteTaskRepositoryAdapter(Repository(init_database(":memory:")))
    assert isinstance(adapter, TaskRepository)
    task = asyncio.run(adapter.create_task("hello"))
    queued = asyncio.run(
        adapter.update_task_status(task.task_id, TaskStatus.QUEUED)
    )
    routed = asyncio.run(
        adapter.update_task_workflow(
            task.task_id, WorkflowType.DIRECT, "answer hello"
        )
    )
    assert task.input_text == "hello"
    assert queued.status == TaskStatus.QUEUED
    assert routed.workflow == WorkflowType.DIRECT


def test_legacy_model_adapter_satisfies_model_contract():
    adapter = LegacyModelClientAdapter(
        MockLLMClient([LLMResponse(content="ok", token_estimate=3)])
    )
    assert isinstance(adapter, ModelClient)
    response = asyncio.run(
        adapter.invoke(
            ModelRequest(
                messages=[{"role": "user", "content": "hello"}],
                model="test-model",
            )
        )
    )
    assert response.content == "ok"
    assert response.token_estimate == 3
    assert response.model == "test-model"


def test_checkpoint_store_is_idempotent_and_monotonic():
    store = InMemoryCheckpointStore()
    first = Checkpoint(
        checkpoint_id="cp_1",
        task_id="task_1",
        run_id="run_1",
        sequence=1,
        kind="workflow_selected",
        payload={"workflow": "direct"},
        created_at=datetime.now(timezone.utc),
    )
    stale = Checkpoint(
        checkpoint_id="cp_2",
        task_id="task_1",
        run_id="run_1",
        sequence=1,
        kind="stale",
        payload={},
        created_at=datetime.now(timezone.utc),
    )
    assert isinstance(store, CheckpointStore)
    asyncio.run(store.save(first))
    asyncio.run(store.save(first))
    with pytest.raises(ValueError, match="sequence"):
        asyncio.run(store.save(stale))
    assert asyncio.run(store.list("task_1", "run_1")) == [first]
