from app.schemas.workflow import SpawnAgentSpec


def build_sub_agent_context(spec: SpawnAgentSpec) -> str:
    return (
        f"Goal: {spec.goal}\n"
        f"Assigned subtasks: {', '.join(spec.assigned_subtasks)}\n"
        f"Context brief: {spec.context_brief}\n"
        f"Expected output: {spec.expected_output}\n"
        f"Stop condition: {spec.stop_condition}\n"
        "Return only structured JSON matching the sub-agent output schema."
    )
