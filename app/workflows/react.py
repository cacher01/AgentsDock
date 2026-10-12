from __future__ import annotations

from app.core.enums import WorkflowType
from app.orchestration.prompts import REACT_DECISION_PROMPT
from app.schemas.workflow import FinalSynthesis, ReactDecision
from app.workflows.base import WorkflowContext, WorkflowRuntime


class ReactWorkflowStrategy:
    workflow_type = WorkflowType.REACT

    async def execute(self, context: WorkflowContext, runtime: WorkflowRuntime) -> None:
        task_id = context.task_id
        runtime.checkpoint(task_id)
        user_input = runtime.task_context(task_id)
        outputs: list[str] = []
        allowed = {
            "weather",
            "current_time",
            "date_calculator",
            "unit_converter",
            "calculator",
        }
        available = allowed.intersection(runtime.tool_executor.registry.list_names())
        degraded = False
        final_hint = ""
        legacy_synthesis: FinalSynthesis | None = None
        try:
            for _ in range(runtime.settings.react_max_tool_calls + 1):
                runtime.checkpoint(task_id)
                response = await runtime.llm.chat(
                    [
                        {"role": "system", "content": REACT_DECISION_PROMPT},
                        {
                            "role": "user",
                            "content": (
                                f"Task:\n{user_input}\n\n"
                                f"Available tools: {sorted(available)}\n\n"
                                f"Observations:\n{chr(10).join(outputs) or 'none'}"
                            ),
                        },
                    ]
                )
                try:
                    decision = ReactDecision.model_validate_json(response.content)
                except Exception:
                    legacy_synthesis = FinalSynthesis.model_validate_json(response.content)
                    break
                if decision.action == "final_answer":
                    final_hint = decision.answer
                    break
                if len(outputs) >= runtime.settings.react_max_tool_calls:
                    degraded = True
                    final_hint = "Maximum ReAct tool calls reached."
                    break
                if decision.tool_name not in allowed or decision.tool_name not in available:
                    degraded = True
                    final_hint = f"Tool unavailable or disallowed: {decision.tool_name}"
                    break
                result = await runtime.tool_executor.execute(
                    task_id,
                    None,
                    decision.tool_name,
                    decision.arguments,
                    [decision.tool_name],
                )
                outputs.append(f"{decision.tool_name}: {result}")
        except Exception:
            outputs = await runtime.react_fallback(task_id, user_input, available)
            degraded = not outputs
        if legacy_synthesis is not None:
            if not outputs:
                outputs = await runtime.react_fallback(task_id, user_input, available)
            await runtime.finish(task_id, legacy_synthesis, degraded=degraded)
            return
        synthesis = await runtime.final_synthesis(
            task_id=task_id,
            workflow=WorkflowType.REACT,
            web_used=False,
            context=(
                f"User task:\n{user_input}\n\n"
                f"Tool outputs:\n{chr(10).join(outputs)}\n\n"
                f"Decision answer:\n{final_hint}"
            ),
        )
        await runtime.finish(task_id, synthesis, degraded=degraded or not outputs)
