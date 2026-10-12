from __future__ import annotations

import asyncio

from app.core.enums import EventType, WorkflowType
from app.schemas.workflow import SpawnAgentSpec, SubAgentOutput, SubTask
from app.workflows.base import WorkflowContext, WorkflowRuntime
from app.workflows.utils import (
    format_agent_output,
    has_cycle,
    has_invalid_dependencies,
)


class DagWorkflowStrategy:
    workflow_type = WorkflowType.DAG

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
            "DAG plan generated",
        )
        if has_cycle(decomposition.subtasks) or has_invalid_dependencies(decomposition.subtasks):
            routing.workflow = WorkflowType.PLAN_EXECUTE
            await runtime.execute_workflow(
                WorkflowType.PLAN_EXECUTE,
                WorkflowContext(task_id=task_id, routing=routing),
            )
            return
        completed: set[str] = set()
        failed: set[str] = set()
        summaries: list[str] = []
        outputs_by_id: dict[str, SubAgentOutput] = {}
        for subtask in decomposition.subtasks:
            node_id = f"subtask_{subtask.subtask_id}"
            runtime.event_service.create_graph_node(
                task_id, node_id, "subtask", subtask.title, "pending"
            )
            for dependency in subtask.depends_on:
                runtime.event_service.create_graph_edge(
                    task_id,
                    f"edge_{dependency}_{subtask.subtask_id}",
                    f"subtask_{dependency}",
                    node_id,
                    "depends_on",
                )
        while len(completed | failed) < len(decomposition.subtasks):
            runtime.checkpoint(task_id)
            ready = [
                subtask
                for subtask in decomposition.subtasks
                if subtask.subtask_id not in completed | failed
                and all(dep in completed for dep in subtask.depends_on)
            ]
            blocked = [
                subtask
                for subtask in decomposition.subtasks
                if subtask.subtask_id not in completed | failed
                and any(dep in failed for dep in subtask.depends_on)
            ]
            for subtask in blocked:
                failed.add(subtask.subtask_id)
                runtime.event_service.update_graph_node(
                    task_id,
                    f"subtask_{subtask.subtask_id}",
                    "subtask",
                    subtask.title,
                    "blocked",
                )
            if not ready:
                break

            semaphore = asyncio.Semaphore(runtime.settings.max_concurrent_agents)

            async def run_node(subtask: SubTask) -> tuple[SubTask, SubAgentOutput]:
                async with semaphore:
                    runtime.event_service.update_graph_node(
                        task_id,
                        f"subtask_{subtask.subtask_id}",
                        "subtask",
                        subtask.title,
                        "running",
                    )
                    dependency_context = "\n\n".join(
                        f"Dependency {dependency} output:\n"
                        f"{format_agent_output(outputs_by_id[dependency])}"
                        for dependency in subtask.depends_on
                    )
                    spec = SpawnAgentSpec(
                        agent_name=f"dag_{subtask.subtask_id}",
                        template="analysis",
                        assigned_subtasks=[subtask.subtask_id],
                        goal=subtask.title,
                        context_brief=(
                            f"Subtask: {subtask.description}\n"
                            f"Depends on: {', '.join(subtask.depends_on)}\n"
                            f"{dependency_context}"
                        ),
                        allowed_tools=[],
                        expected_output=subtask.expected_output,
                        stop_condition="Return structured subtask output.",
                    )
                    output = await runtime.agent_runtime.run_sub_agent(spec)
                    return subtask, output

            results = await asyncio.gather(*(run_node(item) for item in ready))
            for subtask, output in results:
                if output.status == "completed":
                    completed.add(subtask.subtask_id)
                    outputs_by_id[subtask.subtask_id] = output
                    summaries.append(format_agent_output(output))
                    status = "completed"
                else:
                    failed.add(subtask.subtask_id)
                    status = "failed"
                runtime.event_service.update_graph_node(
                    task_id,
                    f"subtask_{subtask.subtask_id}",
                    "subtask",
                    subtask.title,
                    status,
                )
        synthesis = await runtime.final_synthesis(
            task_id=task_id,
            workflow=WorkflowType.DAG,
            web_used=False,
            context=(
                f"User task:\n{user_input}\n\n"
                f"DAG plan:\n{decomposition.plan_summary}\n\n"
                f"Completed node findings:\n{chr(10).join(summaries)}"
            ),
        )
        await runtime.finish(task_id, synthesis, degraded=bool(failed))
