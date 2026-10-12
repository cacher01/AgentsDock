from collections.abc import Callable
from typing import Any

from app.core.enums import EventType, ToolCallStatus
from app.db.repositories import Repository
from app.services.event_service import EventService
from app.tools.policy import ToolPolicy
from app.tools.registry import ToolRegistry


class ToolExecutor:
    def __init__(
        self,
        registry: ToolRegistry,
        policy: ToolPolicy,
        repository: Repository,
        event_service: EventService,
        checkpoint: Callable[[str], None] | None = None,
    ) -> None:
        self.registry = registry
        self.policy = policy
        self.repository = repository
        self.event_service = event_service
        self.checkpoint = checkpoint
        self._call_counts: dict[str, int] = {}

    async def execute(
        self,
        task_id: str,
        agent_id: str | None,
        tool_name: str,
        arguments: dict[str, Any],
        allowed_tools: list[str],
    ) -> dict[str, Any]:
        if self.checkpoint is not None:
            self.checkpoint(task_id)
        self.policy.check_allowed(tool_name, allowed_tools)
        self._call_counts[task_id] = self._call_counts.get(task_id, 0) + 1
        if self._call_counts[task_id] > self.policy.max_tool_calls:
            raise RuntimeError("Maximum tool calls exceeded")
        cached = self.repository.find_completed_tool_result(task_id, tool_name, arguments)
        if cached is not None:
            self.event_service.emit(
                task_id,
                agent_id,
                EventType.TOOL_CALL_COMPLETED,
                {"tool_name": tool_name, "reused": True},
                f"Tool result reused: {tool_name}",
            )
            return cached
        graph_node_id = f"tool_{self._call_counts[task_id]}_{tool_name}"
        task = self.repository.get_task(task_id)
        workflow_node_id = f"workflow_{task.get('workflow')}" if task else ""
        self.event_service.create_graph_node(
            task_id,
            graph_node_id,
            "tool",
            tool_name,
            "running",
            metadata={"arguments": arguments},
        )
        if workflow_node_id:
            self.event_service.create_graph_edge(
                task_id,
                f"edge_{workflow_node_id}_{graph_node_id}",
                workflow_node_id,
                graph_node_id,
                "used_tool",
            )

        self.event_service.emit(
            task_id,
            agent_id,
            EventType.TOOL_CALL_REQUESTED,
            {"tool_name": tool_name, "arguments": arguments},
            f"Tool requested: {tool_name}",
        )
        tool = self.registry.get(tool_name)
        try:
            result = await tool.run(arguments)
            self.repository.save_tool_call(
                task_id,
                agent_id,
                tool_name,
                arguments,
                ToolCallStatus.COMPLETED,
                result_summary=str(result)[:500],
                result=result,
            )
            for item in result.get("results", []):
                self.repository.save_evidence(
                    task_id=task_id,
                    title=item.get("title", ""),
                    url=item.get("url", ""),
                    snippet=item.get("snippet", ""),
                    source=item.get("source", tool_name),
                    rank=int(item.get("rank", 0)),
                    source_type=item.get("source_type", "search_result"),
                    summary=item.get("summary", item.get("snippet", "")),
                )
            self.event_service.update_graph_node(
                task_id, graph_node_id, "tool", tool_name, "completed"
            )
            self.event_service.emit(
                task_id,
                agent_id,
                EventType.TOOL_CALL_COMPLETED,
                {"tool_name": tool_name},
                f"Tool completed: {tool_name}",
            )
        except Exception as exc:
            self.repository.save_tool_call(
                task_id,
                agent_id,
                tool_name,
                arguments,
                ToolCallStatus.FAILED,
                error=str(exc),
            )
            self.event_service.update_graph_node(
                task_id,
                graph_node_id,
                "tool",
                tool_name,
                "failed",
                metadata={"error": str(exc)},
            )
            self.event_service.emit(
                task_id,
                agent_id,
                EventType.TOOL_CALL_FAILED,
                {"tool_name": tool_name, "error": str(exc)},
                f"Tool failed: {tool_name}",
            )
            raise
        if self.checkpoint is not None:
            self.checkpoint(task_id)
        return result
