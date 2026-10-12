from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.core.enums import WorkflowType


class RoutingConstraints(BaseModel):
    max_agents: int = 4
    max_swarm_rounds: int = 2
    max_concurrent_agents: int = 2


class RoutingDecision(BaseModel):
    workflow: WorkflowType
    complexity: Literal["simple", "medium", "complex"]
    reason: str
    requires_web: bool
    expected_sub_agents: int = Field(ge=0)
    estimated_steps: int = Field(ge=0)
    risk_flags: list[str] = Field(default_factory=list)
    constraints: RoutingConstraints = Field(default_factory=RoutingConstraints)


class SubTask(BaseModel):
    subtask_id: str
    title: str
    description: str
    expected_output: str
    requires_web: bool
    priority: int = Field(ge=1)
    depends_on: list[str] = Field(default_factory=list)


class TaskDecomposition(BaseModel):
    objective: str
    plan_summary: str
    subtasks: list[SubTask] = Field(default_factory=list)
    success_criteria: list[str] = Field(default_factory=list)

    @field_validator("subtasks")
    @classmethod
    def subtask_ids_must_be_unique(cls, value: list[SubTask]) -> list[SubTask]:
        ids = [item.subtask_id for item in value]
        if len(ids) != len(set(ids)):
            raise ValueError("subtask_id values must be unique")
        return value


class SpawnAgentSpec(BaseModel):
    agent_name: str
    template: Literal["research", "analysis", "review", "synthesis"]
    assigned_subtasks: list[str]
    goal: str
    context_brief: str
    allowed_tools: list[str] = Field(default_factory=list)
    expected_output: str
    stop_condition: str


class SpawnPlan(BaseModel):
    round: int = Field(ge=1)
    agents: list[SpawnAgentSpec] = Field(default_factory=list)


class RoundEvaluation(BaseModel):
    round: int = Field(ge=1)
    status: Literal["sufficient", "needs_more_work", "failed"]
    summary: str
    completed_subtasks: list[str] = Field(default_factory=list)
    incomplete_subtasks: list[str] = Field(default_factory=list)
    knowledge_gaps: list[str] = Field(default_factory=list)
    needs_supplemental_search: bool = False
    next_action: Literal["finish", "run_next_round", "retry_failed", "degrade_and_finish"]
    next_round_focus: str = ""


class ReactDecision(BaseModel):
    action: Literal["tool_call", "final_answer"]
    tool_name: str = ""
    arguments: dict[str, Any] = Field(default_factory=dict)
    summary: str
    answer: str = ""


class SubAgentOutput(BaseModel):
    status: Literal["completed", "blocked", "failed"]
    summary: str
    findings: list[Any] = Field(default_factory=list)
    evidence: list[Any] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    recommended_next_action: str


class Citation(BaseModel):
    title: str
    url: str
    evidence_id: str | None = None


class FinalSynthesis(BaseModel):
    answer: str
    citations: list[Citation] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    used_workflow: WorkflowType
    web_used: bool = False

    @model_validator(mode="after")
    def citations_required_when_web_used(self) -> "FinalSynthesis":
        if self.web_used and not self.citations:
            raise ValueError("citations are required when web tools were used")
        return self
