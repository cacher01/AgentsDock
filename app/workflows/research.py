from __future__ import annotations

from app.core.enums import WorkflowType
from app.workflows.base import WorkflowContext, WorkflowRuntime


class ResearchWorkflowStrategy:
    workflow_type = WorkflowType.RESEARCH

    async def execute(self, context: WorkflowContext, runtime: WorkflowRuntime) -> None:
        task_id = context.task_id
        runtime.checkpoint(task_id)
        user_input = runtime.task_context(task_id)
        research_context, web_used = await runtime.research_context(task_id, None)
        if web_used:
            degraded = False
        else:
            degraded = True
            research_context = (
                research_context
                or "Research tools did not return usable evidence. Answer with limitations."
            )
        try:
            synthesis = await runtime.final_synthesis(
                task_id=task_id,
                workflow=WorkflowType.RESEARCH,
                web_used=web_used,
                context=f"User task:\n{user_input}\n\n{research_context}",
            )
        except Exception as exc:
            synthesis = runtime.research_fallback_synthesis(task_id, user_input, exc)
            degraded = True
        citations_valid = await runtime.citation_check(task_id, synthesis)
        critic_acceptable = await runtime.optional_critic(task_id, synthesis, web_used)
        if web_used and not citations_valid and runtime.settings.research_supplemental_searches > 0:
            added = await runtime.supplemental_research(task_id, None)
            if added:
                synthesis = await runtime.final_synthesis(
                    task_id=task_id,
                    workflow=WorkflowType.RESEARCH,
                    web_used=True,
                    context=(
                        f"User task:\n{user_input}\n\n{runtime.persisted_evidence_context(task_id)}"
                    ),
                )
                citations_valid = await runtime.citation_check(task_id, synthesis)
        degraded = degraded or not citations_valid or not critic_acceptable
        await runtime.finish(task_id, synthesis, degraded=degraded)
