from enum import Enum


class TaskStatus(str, Enum):
    CREATED = "created"
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    DEGRADED = "degraded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class AgentStatus(str, Enum):
    CREATED = "created"
    RUNNING = "running"
    COMPLETED = "completed"
    BLOCKED = "blocked"
    FAILED = "failed"


class WorkflowType(str, Enum):
    DIRECT = "direct"
    PLAN_EXECUTE = "plan_execute"
    SWARM = "swarm"
    RESEARCH = "research"
    REACT = "react"
    SUPERVISOR = "supervisor"
    DAG = "dag"


class EventType(str, Enum):
    TASK_CREATED = "task_created"
    TASK_STARTED = "task_started"
    WORKFLOW_SELECTED = "workflow_selected"
    PLAN_GENERATED = "plan_generated"
    AGENT_SPAWNED = "agent_spawned"
    AGENT_STARTED = "agent_started"
    AGENT_COMPLETED = "agent_completed"
    MESSAGE_SENT = "message_sent"
    TOOL_CALL_REQUESTED = "tool_call_requested"
    TOOL_CALL_COMPLETED = "tool_call_completed"
    TOOL_CALL_FAILED = "tool_call_failed"
    CONTEXT_SUMMARIZED = "context_summarized"
    FINAL_ANSWER_GENERATED = "final_answer_generated"
    TASK_FAILED = "task_failed"
    TASK_COMPLETED = "task_completed"
    TASK_DEGRADED = "task_degraded"
    TASK_CANCELLED = "task_cancelled"
    WORKFLOW_DEGRADED = "workflow_degraded"
    GRAPH_NODE_CREATED = "graph_node_created"
    GRAPH_NODE_UPDATED = "graph_node_updated"
    GRAPH_EDGE_CREATED = "graph_edge_created"
    CRITIC_COMPLETED = "critic_completed"
    CRITIC_SKIPPED = "critic_skipped"
    CITATION_CHECK_COMPLETED = "citation_check_completed"


class ToolCallStatus(str, Enum):
    REQUESTED = "requested"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
