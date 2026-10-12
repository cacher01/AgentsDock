from __future__ import annotations

import asyncio

from app.core.enums import AgentStatus, EventType, WorkflowType
from app.schemas.workflow import SpawnAgentSpec, SubAgentOutput, SubTask
from app.workflows.base import WorkflowContext, WorkflowRuntime
from app.workflows.utils import format_agent_output


class SupervisorWorkflowStrategy:
    workflow_type = WorkflowType.SUPERVISOR

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
            "Supervisor plan generated",
        )
        runtime.event_service.create_graph_node(
            task_id, "workflow_supervisor", "workflow", "Supervisor", "running"
        )
        selected = decomposition.subtasks[: max(2, min(4, len(decomposition.subtasks)))]
        if not selected:
            selected = [
                SubTask(
                    subtask_id="subtask_1",
                    title="Analyze task",
                    description=user_input,
                    expected_output="analysis",
                    requires_web=False,
                    priority=1,
                )
            ]
        semaphore = asyncio.Semaphore(runtime.settings.max_concurrent_agents)

        async def run_worker(subtask: SubTask) -> tuple[SubAgentOutput, bool]:
            async with semaphore:
                runtime.checkpoint(task_id)
                node_id = f"agent_{subtask.subtask_id}"
                agent = runtime.repository.create_agent(
                    task_id=task_id,
                    name=node_id,
                    template="research" if routing.requires_web else "analysis",
                    goal=subtask.title,
                    context_brief=subtask.description,
                    allowed_tools=["web_search"] if routing.requires_web else [],
                    status=AgentStatus.RUNNING,
                )
                research_context, worker_web_used = await runtime.research_subtask(
                    task_id,
                    agent["agent_id"],
                    subtask,
                    routing.requires_web,
                )
                spec = SpawnAgentSpec(
                    agent_name=node_id,
                    template="research" if worker_web_used else "analysis",
                    assigned_subtasks=[subtask.subtask_id],
                    goal=subtask.title,
                    context_brief=(
                        f"Subtask: {subtask.description}\n"
                        f"Expected output: {subtask.expected_output}\n"
                        f"{research_context}"
                    ),
                    allowed_tools=["web_search"] if worker_web_used else [],
                    expected_output=subtask.expected_output,
                    stop_condition="Return structured subtask output.",
                )
                runtime.event_service.create_graph_node(
                    task_id,
                    node_id,
                    "agent",
                    subtask.title,
                    "running",
                    agent["agent_id"],
                )
                runtime.event_service.create_graph_edge(
                    task_id,
                    f"edge_workflow_{node_id}",
                    "workflow_supervisor",
                    node_id,
                    "spawned",
                )
                runtime.event_service.emit(
                    task_id,
                    agent["agent_id"],
                    EventType.AGENT_SPAWNED,
                    spec.model_dump(mode="json"),
                    f"Agent spawned: {spec.agent_name}",
                )
                try:
                    output = await runtime.agent_runtime.run_sub_agent(spec)
                except Exception as exc:
                    output = SubAgentOutput(
                        status="failed",
                        summary=(
                            "Worker output could not be parsed: "
                            f"{str(exc).strip() or type(exc).__name__}"
                        ),
                        findings=[],
                        evidence=[],
                        open_questions=[subtask.title],
                        confidence=0.0,
                        recommended_next_action="continue with other workers",
                    )
                status = "completed" if output.status == "completed" else "failed"
                agent_status = (
                    AgentStatus.COMPLETED if output.status == "completed" else AgentStatus.FAILED
                )
                runtime.repository.update_agent_status(agent["agent_id"], agent_status)
                runtime.event_service.update_graph_node(
                    task_id,
                    node_id,
                    "agent",
                    subtask.title,
                    status,
                    agent["agent_id"],
                )
                runtime.event_service.emit(
                    task_id,
                    agent["agent_id"],
                    EventType.AGENT_COMPLETED,
                    output.model_dump(mode="json"),
                    f"Agent completed: {spec.agent_name}",
                )
                return output, worker_web_used

        worker_results = await asyncio.gather(*(run_worker(subtask) for subtask in selected))
        runtime.checkpoint(task_id)
        outputs = [output for output, _ in worker_results]
        worker_web_used = any(web_used for _, web_used in worker_results)
        runtime.event_service.update_graph_node(
            task_id, "workflow_supervisor", "workflow", "Supervisor", "completed"
        )
        try:
            synthesis = await runtime.final_synthesis(
                task_id=task_id,
                workflow=WorkflowType.SUPERVISOR,
                web_used=worker_web_used,
                context=(
                    f"User task:\n{user_input}\n\n"
                    f"Supervisor plan:\n{decomposition.plan_summary}\n\n"
                    "Worker findings:\n"
                    + "\n\n".join(format_agent_output(output) for output in outputs)
                ),
            )
        except Exception as exc:
            synthesis = runtime.multi_agent_fallback_synthesis(task_id, user_input, outputs, exc)
        await runtime.finish(
            task_id,
            synthesis,
            degraded=(
                any(output.status != "completed" for output in outputs)
                or synthesis.confidence <= 0.5
            ),
        )
