from __future__ import annotations

from app.core.enums import EventType, WorkflowType
from app.workflows.base import WorkflowContext, WorkflowRuntime


class PlanExecuteWorkflowStrategy:
    workflow_type = WorkflowType.PLAN_EXECUTE

    async def execute(self, context: WorkflowContext, runtime: WorkflowRuntime) -> None:
        task_id = context.task_id
        runtime.checkpoint(task_id)
        user_input = runtime.task_context(task_id)
        decomposition = await runtime.decompose(task_id)
        runtime.event_service.emit(
            task_id,
            None,
            EventType.PLAN_GENERATED,
            decomposition.model_dump(mode="json"),
            "Plan generated",
        )
        research_context, web_used = await runtime.maybe_research(
            task_id, None, context.routing.requires_web
        )
        synthesis = await runtime.final_synthesis(
            task_id=task_id,
            workflow=context.routing.workflow,
            web_used=web_used,
            context=(
                f"User task:\n{user_input}\n\n"
                f"Plan:\n{decomposition.plan_summary}\n\n"
                f"{research_context}"
            ),
        )
        await runtime.finish(task_id, synthesis, degraded=False)
