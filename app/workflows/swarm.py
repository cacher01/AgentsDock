from __future__ import annotations

import asyncio

from app.core.enums import AgentStatus, EventType, WorkflowType
from app.workflows.base import WorkflowContext, WorkflowRuntime
from app.workflows.utils import format_agent_output


class SwarmWorkflowStrategy:
    workflow_type = WorkflowType.SWARM

    async def execute(self, context: WorkflowContext, runtime: WorkflowRuntime) -> None:
        task_id = context.task_id
        routing = context.routing
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
        summaries: list[str] = []
        for round_number in range(1, runtime.settings.max_swarm_rounds + 1):
            runtime.checkpoint(task_id)
            spawn_plan = await runtime.spawn_plan(round_number)
            agents = spawn_plan.agents[: runtime.settings.max_agents]
            semaphore = asyncio.Semaphore(runtime.settings.max_concurrent_agents)

            async def run_agent(spec):
                async with semaphore:
                    agent = runtime.repository.create_agent(
                        task_id=task_id,
                        name=spec.agent_name,
                        template=spec.template,
                        goal=spec.goal,
                        context_brief=spec.context_brief,
                        allowed_tools=spec.allowed_tools,
                        status=AgentStatus.RUNNING,
                    )
                    runtime.event_service.emit(
                        task_id,
                        agent["agent_id"],
                        EventType.AGENT_SPAWNED,
                        spec.model_dump(mode="json"),
                        f"Agent spawned: {spec.agent_name}",
                    )
                    output = await runtime.agent_runtime.run_sub_agent(spec)
                    runtime.repository.update_agent_status(
                        agent["agent_id"],
                        AgentStatus.COMPLETED
                        if output.status == "completed"
                        else AgentStatus.FAILED,
                    )
                    runtime.event_service.emit(
                        task_id,
                        agent["agent_id"],
                        EventType.AGENT_COMPLETED,
                        output.model_dump(mode="json"),
                        f"Agent completed: {spec.agent_name}",
                    )
                    return format_agent_output(output)

            summaries.extend(await asyncio.gather(*(run_agent(spec) for spec in agents)))
            evaluation = await runtime.round_evaluation(round_number)
            if evaluation.next_action in {"finish", "degrade_and_finish"}:
                break
        research_context, web_used = await runtime.maybe_research(
            task_id, None, routing.requires_web
        )
        synthesis = await runtime.final_synthesis(
            task_id=task_id,
            workflow=routing.workflow,
            web_used=web_used,
            context=(
                f"User task:\n{user_input}\n\n"
                f"Sub-agent findings:\n{chr(10).join(summaries)}\n\n"
                f"{research_context}"
            ),
        )
        await runtime.finish(task_id, synthesis, degraded=False)
