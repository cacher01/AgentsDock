from __future__ import annotations

from app.core.enums import WorkflowType
from app.workflows.base import WorkflowContext, WorkflowRuntime


class DirectWorkflowStrategy:
    workflow_type = WorkflowType.DIRECT

    async def execute(self, context: WorkflowContext, runtime: WorkflowRuntime) -> None:
        runtime.checkpoint(context.task_id)
        user_input = runtime.task_context(context.task_id)
        research_context, web_used = await runtime.maybe_research(
            context.task_id, None, context.routing.requires_web
        )
        synthesis = await runtime.final_synthesis(
            task_id=context.task_id,
            workflow=context.routing.workflow,
            web_used=web_used,
            context=(
                f"User task:\n{user_input}\n\nAnswer the user task directly.\n{research_context}"
            ),
        )
        await runtime.finish(context.task_id, synthesis, degraded=False)
