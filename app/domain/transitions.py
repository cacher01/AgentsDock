from __future__ import annotations

from collections.abc import Mapping
from enum import Enum
from typing import TypeVar

from app.core.enums import TaskStatus
from app.domain.models import NodeStatus, RunStatus


class InvalidStateTransition(ValueError):
    pass


TASK_TRANSITIONS: Mapping[TaskStatus, frozenset[TaskStatus]] = {
    TaskStatus.CREATED: frozenset({TaskStatus.QUEUED, TaskStatus.CANCELLED}),
    TaskStatus.QUEUED: frozenset({TaskStatus.RUNNING, TaskStatus.FAILED, TaskStatus.CANCELLED}),
    TaskStatus.RUNNING: frozenset(
        {
            TaskStatus.COMPLETED,
            TaskStatus.DEGRADED,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
        }
    ),
    TaskStatus.COMPLETED: frozenset(),
    TaskStatus.DEGRADED: frozenset(),
    TaskStatus.FAILED: frozenset(),
    TaskStatus.CANCELLED: frozenset(),
}

RUN_TRANSITIONS: Mapping[RunStatus, frozenset[RunStatus]] = {
    RunStatus.QUEUED: frozenset({RunStatus.RUNNING, RunStatus.FAILED, RunStatus.CANCELLED}),
    RunStatus.RUNNING: frozenset(
        {
            RunStatus.COMPLETED,
            RunStatus.DEGRADED,
            RunStatus.FAILED,
            RunStatus.CANCELLED,
        }
    ),
    RunStatus.COMPLETED: frozenset(),
    RunStatus.DEGRADED: frozenset(),
    RunStatus.FAILED: frozenset(),
    RunStatus.CANCELLED: frozenset(),
}

NODE_TRANSITIONS: Mapping[NodeStatus, frozenset[NodeStatus]] = {
    NodeStatus.PENDING: frozenset({NodeStatus.RUNNING, NodeStatus.SKIPPED, NodeStatus.CANCELLED}),
    NodeStatus.RUNNING: frozenset(
        {
            NodeStatus.COMPLETED,
            NodeStatus.BLOCKED,
            NodeStatus.FAILED,
            NodeStatus.CANCELLED,
        }
    ),
    NodeStatus.BLOCKED: frozenset({NodeStatus.RUNNING, NodeStatus.FAILED, NodeStatus.CANCELLED}),
    NodeStatus.COMPLETED: frozenset(),
    NodeStatus.FAILED: frozenset(),
    NodeStatus.SKIPPED: frozenset(),
    NodeStatus.CANCELLED: frozenset(),
}

StatusT = TypeVar("StatusT", bound=Enum)


def _ensure_transition(
    current: StatusT,
    target: StatusT,
    allowed: Mapping[StatusT, frozenset[StatusT]],
) -> None:
    if current == target:
        return
    if target not in allowed[current]:
        raise InvalidStateTransition(
            f"invalid {type(current).__name__} transition: {current.value} -> {target.value}"
        )


def ensure_task_transition(current: TaskStatus, target: TaskStatus) -> None:
    _ensure_transition(current, target, TASK_TRANSITIONS)


def ensure_run_transition(current: RunStatus, target: RunStatus) -> None:
    _ensure_transition(current, target, RUN_TRANSITIONS)


def ensure_node_transition(current: NodeStatus, target: NodeStatus) -> None:
    _ensure_transition(current, target, NODE_TRANSITIONS)
