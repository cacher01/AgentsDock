from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Mapping, Sequence

from app.core.enums import TaskStatus, WorkflowType


class RunStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    DEGRADED = "degraded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class NodeStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    BLOCKED = "blocked"
    FAILED = "failed"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class RequestContext:
    request_id: str
    user_id: str
    workspace_id: str
    role: str
    capabilities: frozenset[str] = field(default_factory=frozenset)

    def can(self, capability: str) -> bool:
        return capability in self.capabilities


@dataclass(frozen=True, slots=True)
class TaskSnapshot:
    task_id: str
    input_text: str
    status: TaskStatus
    created_at: datetime
    updated_at: datetime
    session_id: str | None = None
    objective: str = ""
    workflow: WorkflowType | None = None
    token_estimate: int = 0
    error_summary: str = ""
    completed_at: datetime | None = None
    workspace_id: str | None = None
    owner_user_id: str | None = None
    requested_model_variant_id: str | None = None


@dataclass(frozen=True, slots=True)
class RunSnapshot:
    run_id: str
    task_id: str
    attempt: int
    status: RunStatus
    workflow: WorkflowType | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error_summary: str = ""

    def __post_init__(self) -> None:
        if self.attempt < 1:
            raise ValueError("run attempt must be positive")


@dataclass(frozen=True, slots=True)
class ExecutionNodeSnapshot:
    node_id: str
    run_id: str
    node_key: str
    node_type: str
    status: NodeStatus
    attempt: int
    created_at: datetime
    updated_at: datetime
    input_ref: str | None = None
    output_ref: str | None = None

    def __post_init__(self) -> None:
        if self.attempt < 1:
            raise ValueError("node attempt must be positive")


class CompletionState(str, Enum):
    COMPLETE = "complete"
    DEGRADED = "degraded"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class AgentRunSnapshot:
    agent_run_id: str
    run_id: str
    role: str
    goal: str
    status: str
    capability_snapshot: Mapping[str, Any]
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class RuntimeEventSnapshot:
    event_id: str
    task_id: str
    sequence: int
    event_type: str
    payload: Mapping[str, Any]
    summary: str
    created_at: datetime
    run_id: str | None = None
    agent_run_id: str | None = None

    def __post_init__(self) -> None:
        if self.sequence < 1:
            raise ValueError("event sequence must be positive")


@dataclass(frozen=True, slots=True)
class ToolCallSnapshot:
    tool_call_id: str
    task_id: str
    tool_key: str
    status: str
    arguments: Mapping[str, Any]
    created_at: datetime
    agent_run_id: str | None = None
    result_ref: str | None = None
    error_summary: str = ""
    completed_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class EvidenceSnapshot:
    evidence_id: str
    task_id: str
    source_type: str
    source_ref: str
    locator: Mapping[str, Any]
    title: str
    snippet: str
    created_at: datetime
    checksum: str | None = None
    rank: int | None = None


@dataclass(frozen=True, slots=True)
class ResultSnapshot:
    result_id: str
    task_id: str
    answer: str
    completion_state: CompletionState
    citations: tuple[Mapping[str, Any], ...]
    limitations: tuple[str, ...]
    confidence: float | None
    used_workflow: WorkflowType
    created_at: datetime

    def __post_init__(self) -> None:
        if self.confidence is not None and not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be between 0 and 1")


@dataclass(frozen=True, slots=True)
class CitationSnapshot:
    title: str
    url: str
    evidence_id: str | None = None


@dataclass(frozen=True, slots=True)
class WorkflowOutcome:
    answer: str
    used_workflow: WorkflowType
    confidence: float
    citations: tuple[CitationSnapshot, ...] = ()
    limitations: tuple[str, ...] = ()
    web_used: bool = False
    degraded: bool = False

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be between 0 and 1")


@dataclass(frozen=True, slots=True)
class Checkpoint:
    checkpoint_id: str
    task_id: str
    run_id: str
    sequence: int
    kind: str
    payload: Mapping[str, Any]
    created_at: datetime
    workflow_version: str = "legacy-v1"

    def __post_init__(self) -> None:
        if self.sequence < 1:
            raise ValueError("checkpoint sequence must be positive")


@dataclass(frozen=True, slots=True)
class CommentSnapshot:
    comment_id: str
    task_id: str
    author_user_id: str
    author_name: str
    body: str
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class NotificationSnapshot:
    notification_id: str
    user_id: str
    notification_type: str
    resource_type: str
    resource_id: str
    payload: Mapping[str, Any]
    read_at: datetime | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class ModelRequest:
    messages: Sequence[Mapping[str, str]]
    model: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    purpose: str = "task"
    task_id: str | None = None
    agent_id: str | None = None


@dataclass(frozen=True, slots=True)
class ModelResponse:
    content: str
    token_estimate: int = 0
    model: str | None = None
    provider: str | None = None
    raw: Mapping[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class InputFileSnapshot:
    input_file_id: str
    task_id: str | None
    object_key: str
    filename: str
    media_type: str
    size_bytes: int
    sha256: str
    parse_status: str
    parse_error: str
    created_at: datetime
    workspace_id: str
    owner_user_id: str

    def __post_init__(self) -> None:
        if self.size_bytes < 0:
            raise ValueError("file size cannot be negative")


@dataclass(frozen=True, slots=True)
class DocumentBlockSnapshot:
    document_block_id: str
    input_file_id: str
    block_index: int
    block_type: str
    locator: Mapping[str, Any]
    text: str
    metadata: Mapping[str, Any]

    def __post_init__(self) -> None:
        if self.block_index < 0:
            raise ValueError("block index cannot be negative")


@dataclass(frozen=True, slots=True)
class LLMCallSnapshot:
    llm_call_id: str
    task_id: str
    status: str
    request_tokens: int
    response_tokens: int
    cost: float
    created_at: datetime
    agent_run_id: str | None = None
    model_variant_id: str | None = None
    latency_ms: int | None = None
    fallback_from: str | None = None
    error_category: str | None = None

    def __post_init__(self) -> None:
        if self.request_tokens < 0 or self.response_tokens < 0:
            raise ValueError("token counts cannot be negative")
        if self.cost < 0:
            raise ValueError("cost cannot be negative")
        if self.latency_ms is not None and self.latency_ms < 0:
            raise ValueError("latency cannot be negative")


@dataclass(frozen=True, slots=True)
class ArtifactSnapshot:
    artifact_id: str
    task_id: str
    filename: str
    media_type: str
    size_bytes: int
    relative_path: str
    created_at: datetime
    workspace_id: str | None = None
    owner_user_id: str | None = None
    requested_model_variant_id: str | None = None
    object_key: str | None = None
    logical_name: str | None = None
    sha256: str | None = None
    version: int = 1

    def __post_init__(self) -> None:
        if self.size_bytes < 0:
            raise ValueError("artifact size cannot be negative")
        if self.version < 1:
            raise ValueError("artifact version must be positive")


@dataclass(frozen=True, slots=True)
class ScheduledTask:
    task_id: str
    attempt: int = 1
    workspace_id: str = ""
    owner_user_id: str = ""
