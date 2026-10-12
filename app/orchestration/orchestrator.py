import json
import re
from collections.abc import Callable
from urllib.parse import urlsplit, urlunsplit

from app.agents.runtime import AgentRuntime
from app.core.config import Settings
from app.core.enums import EventType, TaskStatus, WorkflowType
from app.core.errors import TaskCancelledError, TaskTimeoutError
from app.db.repositories import Repository
from app.llm.client import LLMClient
from app.llm.task_client import TaskAwareLLMClient
from app.orchestration.json_repair import parse_structured_output
from app.orchestration.prompts import (
    DECOMPOSITION_PROMPT,
    FINAL_SYNTHESIS_PROMPT,
    JSON_REPAIR_PROMPT,
    ROUND_EVALUATION_PROMPT,
    ROUTING_PROMPT,
    SPAWN_PLAN_PROMPT,
)
from app.orchestration.routing import (
    apply_routing_guardrails,
    reconcile_routing_decisions,
    route_by_rules,
)
from app.schemas.workflow import (
    Citation,
    FinalSynthesis,
    RoundEvaluation,
    RoutingDecision,
    SpawnPlan,
    SubAgentOutput,
    SubTask,
    TaskDecomposition,
)
from app.services.event_service import EventService
from app.services.result_service import ResultService
from app.tools.executor import ToolExecutor
from app.workflows import (
    DagWorkflowStrategy,
    DirectWorkflowStrategy,
    PlanExecuteWorkflowStrategy,
    ReactWorkflowStrategy,
    ResearchWorkflowStrategy,
    SupervisorWorkflowStrategy,
    SwarmWorkflowStrategy,
)
from app.workflows.base import WorkflowContext
from app.workflows.runtime import LegacyWorkflowRuntime


class Orchestrator:
    def __init__(
        self,
        settings: Settings,
        repository: Repository,
        event_service: EventService,
        result_service: ResultService,
        llm: LLMClient,
        tool_executor: ToolExecutor,
        checkpoint_callback: Callable[[str], None] | None = None,
    ) -> None:
        self.settings = settings
        self.repository = repository
        self.event_service = event_service
        self.result_service = result_service
        self.llm = TaskAwareLLMClient(llm, repository, self._checkpoint)
        self.tool_executor = tool_executor
        self.checkpoint_callback = checkpoint_callback
        self.tool_executor.checkpoint = self._checkpoint
        self.agent_runtime = AgentRuntime(self.llm)
        self.workflow_runtime = LegacyWorkflowRuntime(self)
        self.workflow_strategies = {
            WorkflowType.DIRECT: DirectWorkflowStrategy(),
            WorkflowType.PLAN_EXECUTE: PlanExecuteWorkflowStrategy(),
            WorkflowType.REACT: ReactWorkflowStrategy(),
            WorkflowType.RESEARCH: ResearchWorkflowStrategy(),
            WorkflowType.SUPERVISOR: SupervisorWorkflowStrategy(),
            WorkflowType.DAG: DagWorkflowStrategy(),
            WorkflowType.SWARM: SwarmWorkflowStrategy(),
        }

    async def run_task(self, task_id: str) -> None:
        task = self.repository.get_task(task_id)
        if task is None:
            raise ValueError(f"Task not found: {task_id}")
        if task["status"] == TaskStatus.CANCELLED.value:
            return
        self.repository.update_task_status(task_id, TaskStatus.RUNNING)
        self.event_service.emit(task_id, None, EventType.TASK_STARTED, {}, "Task started")
        task_token = self.llm.bind_task(task_id)
        try:
            routing = await self._route(task["input"])
            self._checkpoint(task_id)
            routing = apply_routing_guardrails(routing, self.settings)
            objective = task["input"]
            self.repository.update_task_workflow(task_id, routing.workflow.value, objective)
            self.event_service.emit(
                task_id,
                None,
                EventType.WORKFLOW_SELECTED,
                routing.model_dump(mode="json"),
                f"Workflow selected: {routing.workflow.value}",
            )
            self.event_service.create_graph_node(
                task_id,
                f"workflow_{routing.workflow.value}",
                "workflow",
                routing.workflow.value,
                "running",
                metadata={"reason": routing.reason, "complexity": routing.complexity},
            )
            if routing.workflow == WorkflowType.DIRECT:
                await self._run_direct(task_id, routing)
            elif routing.workflow == WorkflowType.PLAN_EXECUTE:
                await self._run_plan_execute(task_id, routing)
            elif routing.workflow == WorkflowType.RESEARCH:
                await self._run_research(task_id, routing)
            elif routing.workflow == WorkflowType.REACT:
                await self._run_react(task_id, routing)
            elif routing.workflow == WorkflowType.SUPERVISOR:
                await self._run_supervisor(task_id, routing)
            elif routing.workflow == WorkflowType.DAG:
                await self._run_dag(task_id, routing)
            else:
                await self._run_swarm(task_id, routing)
        except TaskCancelledError:
            return
        except TaskTimeoutError as exc:
            error = str(exc)
            self.repository.update_task_status(task_id, TaskStatus.FAILED, error)
            self.event_service.emit(
                task_id,
                None,
                EventType.TASK_FAILED,
                {"error": error},
                "Task timed out",
            )
        except Exception as exc:
            error = str(exc).strip() or type(exc).__name__
            self.repository.update_task_status(task_id, TaskStatus.FAILED, error)
            self.event_service.emit(
                task_id,
                None,
                EventType.TASK_FAILED,
                {"error": error},
                "Task failed",
            )
            raise
        finally:
            self.llm.reset_task(task_token)
            self._emit_checkpoint(task_id)

    async def _route(self, user_input: str) -> RoutingDecision:
        rule_decision = route_by_rules(user_input)
        try:
            llm_decision = await parse_structured_output(
                llm=self.llm,
                schema=RoutingDecision,
                messages=[
                    {
                        "role": "system",
                        "content": ROUTING_PROMPT,
                    },
                    {"role": "user", "content": user_input},
                ],
                repair_prompt=JSON_REPAIR_PROMPT,
            )
        except Exception:
            return rule_decision
        return reconcile_routing_decisions(user_input, rule_decision, llm_decision)

    async def _run_direct(self, task_id: str, routing: RoutingDecision) -> None:
        await self.workflow_strategies[WorkflowType.DIRECT].execute(
            WorkflowContext(task_id=task_id, routing=routing),
            self.workflow_runtime,
        )

    async def _run_plan_execute(self, task_id: str, routing: RoutingDecision) -> None:
        await self.workflow_strategies[WorkflowType.PLAN_EXECUTE].execute(
            WorkflowContext(task_id=task_id, routing=routing),
            self.workflow_runtime,
        )

    async def _run_research(self, task_id: str, routing: RoutingDecision) -> None:
        await self.workflow_strategies[WorkflowType.RESEARCH].execute(
            WorkflowContext(task_id=task_id, routing=routing),
            self.workflow_runtime,
        )

    def _research_fallback_synthesis(
        self, task_id: str, user_input: str, error: Exception
    ) -> FinalSynthesis:
        evidence = self.repository.list_evidence(task_id)
        if not evidence:
            raise error
        sections = [
            "# 调研结果（降级模式）",
            "",
            f"任务：{user_input}",
            "",
            "最终综合模型调用未能按时完成，以下内容根据已经成功获取的网页证据整理。",
            "",
            "## 已获取的关键信息",
        ]
        for item in evidence[:5]:
            summary = item.get("summary") or item.get("snippet") or ""
            sections.append(f"- **{item.get('title', '来源')}**：{summary}")
        sections.extend(
            [
                "",
                "## 限制",
                "- 该结果由证据摘要自动整理，尚未完成完整的模型综合与交叉验证。",
            ]
        )
        citations = [
            Citation(
                title=item["title"],
                url=item["url"],
                evidence_id=item["evidence_id"],
            )
            for item in evidence[:5]
        ]
        return FinalSynthesis(
            answer="\n".join(sections),
            citations=citations,
            limitations=[
                f"Final synthesis unavailable: {str(error).strip() or type(error).__name__}"
            ],
            confidence=0.45,
            used_workflow=WorkflowType.RESEARCH,
            web_used=True,
        )

    async def _run_react(self, task_id: str, routing: RoutingDecision) -> None:
        await self.workflow_strategies[WorkflowType.REACT].execute(
            WorkflowContext(task_id=task_id, routing=routing),
            self.workflow_runtime,
        )

    async def _run_react_fallback(
        self, task_id: str, user_input: str, available: set[str]
    ) -> list[str]:
        outputs: list[str] = []
        lower = user_input.lower()
        city = _extract_city(user_input)
        if "weather" in available and any(term in lower for term in ("weather", "天气", "气温")):
            result = await self.tool_executor.execute(
                task_id, None, "weather", {"city": city}, ["weather"]
            )
            outputs.append(f"weather: {result}")
        if "current_time" in available and any(term in lower for term in ("time", "时间")):
            result = await self.tool_executor.execute(
                task_id, None, "current_time", {"city": city}, ["current_time"]
            )
            outputs.append(f"current_time: {result}")
        return outputs

    async def _run_supervisor(self, task_id: str, routing: RoutingDecision) -> None:
        await self.workflow_strategies[WorkflowType.SUPERVISOR].execute(
            WorkflowContext(task_id=task_id, routing=routing),
            self.workflow_runtime,
        )

    async def _run_dag(self, task_id: str, routing: RoutingDecision) -> None:
        await self.workflow_strategies[WorkflowType.DAG].execute(
            WorkflowContext(task_id=task_id, routing=routing),
            self.workflow_runtime,
        )

    def _multi_agent_fallback_synthesis(
        self,
        task_id: str,
        user_input: str,
        outputs: list[SubAgentOutput],
        error: Exception,
    ) -> FinalSynthesis:
        evidence = self.repository.list_evidence(task_id)
        sections = [
            "# 多智能体综合结果（降级模式）",
            "",
            f"任务：{user_input}",
            "",
            "最终综合输出因长度或格式问题未能完成，以下内容由各子智能体结果直接汇总。",
        ]
        for index, output in enumerate(outputs, start=1):
            sections.extend(
                [
                    "",
                    f"## 子任务 {index}",
                    "",
                    output.summary,
                ]
            )
            sections.extend(f"- {_stringify_finding(item)}" for item in output.findings)
        sections.extend(
            [
                "",
                "## 说明",
                "",
                "该结果保留了各子智能体的主要发现，但未完成最终模型的统一润色。",
            ]
        )
        citations = [
            Citation(
                title=item["title"],
                url=item["url"],
                evidence_id=item["evidence_id"],
            )
            for item in evidence[:5]
        ]
        return FinalSynthesis(
            answer="\n".join(sections),
            citations=citations,
            limitations=[
                f"Final synthesis unavailable: {str(error).strip() or type(error).__name__}"
            ],
            confidence=0.5,
            used_workflow=WorkflowType.SUPERVISOR,
            web_used=bool(citations),
        )

    async def _run_swarm(self, task_id: str, routing: RoutingDecision) -> None:
        await self.workflow_strategies[WorkflowType.SWARM].execute(
            WorkflowContext(task_id=task_id, routing=routing),
            self.workflow_runtime,
        )

    async def _decompose(self, task_id: str) -> TaskDecomposition:
        return await parse_structured_output(
            llm=self.llm,
            schema=TaskDecomposition,
            messages=[
                {"role": "system", "content": DECOMPOSITION_PROMPT},
                {"role": "user", "content": self._task_context(task_id)},
            ],
            repair_prompt=JSON_REPAIR_PROMPT,
        )

    def _task_context(self, task_id: str) -> str:
        task = self.repository.get_task(task_id)
        if task is None:
            return ""
        session_context = self.repository.session_context_for_task(task_id)
        if not session_context:
            return task["input"]
        return f"Current task:\n{task['input']}\n\n{session_context}"

    async def _research_subtask(
        self,
        task_id: str,
        agent_id: str,
        subtask: SubTask,
        requires_web: bool,
    ) -> tuple[str, bool]:
        if not requires_web or "web_search" not in self.tool_executor.registry.list_names():
            return "", False
        query = f"{subtask.title} {subtask.description}".strip()
        try:
            result = await self.tool_executor.execute(
                task_id=task_id,
                agent_id=agent_id,
                tool_name="web_search",
                arguments={"query": query, "max_results": 3},
                allowed_tools=["web_search"],
            )
        except Exception as exc:
            return f"Web research unavailable: {type(exc).__name__}", False
        lines = [
            f"- {item.get('title', '')}: {item.get('snippet', '')} ({item.get('url', '')})"
            for item in result.get("results", [])[:3]
        ]
        return "Web evidence for this subtask:\n" + "\n".join(lines), bool(lines)

    async def _spawn_plan(self, round_number: int) -> SpawnPlan:
        return await parse_structured_output(
            llm=self.llm,
            schema=SpawnPlan,
            messages=[
                {"role": "system", "content": SPAWN_PLAN_PROMPT},
                {"role": "user", "content": f"round={round_number}"},
            ],
            repair_prompt=JSON_REPAIR_PROMPT,
        )

    async def _round_evaluation(self, round_number: int) -> RoundEvaluation:
        return await parse_structured_output(
            llm=self.llm,
            schema=RoundEvaluation,
            messages=[
                {"role": "system", "content": ROUND_EVALUATION_PROMPT},
                {"role": "user", "content": f"round={round_number}"},
            ],
            repair_prompt=JSON_REPAIR_PROMPT,
        )

    async def _final_synthesis(
        self,
        task_id: str,
        workflow: WorkflowType,
        web_used: bool,
        context: str,
    ) -> FinalSynthesis:
        synthesis = await parse_structured_output(
            llm=self.llm,
            schema=FinalSynthesis,
            messages=[
                {"role": "system", "content": FINAL_SYNTHESIS_PROMPT},
                {"role": "user", "content": context},
            ],
            repair_prompt=JSON_REPAIR_PROMPT,
        )
        if web_used:
            synthesis = self._enforce_evidence_citations(task_id, synthesis)
        return synthesis

    def _enforce_evidence_citations(
        self, task_id: str, synthesis: FinalSynthesis
    ) -> FinalSynthesis:
        evidence = self.repository.list_evidence(task_id)
        valid_ids = {item["evidence_id"] for item in evidence}
        if synthesis.citations and all(
            citation.evidence_id in valid_ids for citation in synthesis.citations
        ):
            return synthesis
        citations = [
            Citation(
                title=item["title"],
                url=item["url"],
                evidence_id=item["evidence_id"],
            )
            for item in evidence[:5]
        ]
        if not citations:
            return synthesis
        return synthesis.model_copy(update={"citations": citations})

    async def _maybe_research(
        self, task_id: str, agent_id: str | None, requires_web: bool
    ) -> tuple[str, bool]:
        if not requires_web:
            return "", False
        task = self.repository.get_task(task_id)
        if task is None or "web_search" not in self.tool_executor.registry.list_names():
            return "Web search was requested but the web_search tool is unavailable.", False
        return await self._research_context(task_id, agent_id)

    async def _research_context(self, task_id: str, agent_id: str | None) -> tuple[str, bool]:
        task = self.repository.get_task(task_id)
        if task is None or "web_search" not in self.tool_executor.registry.list_names():
            return "Web search was requested but the web_search tool is unavailable.", False
        results = []
        seen_urls = set()
        queries = await self._planned_queries(task_id, task["input"])
        for query_index, query in enumerate(queries[: self.settings.research_max_queries]):
            self._checkpoint(task_id)
            result = await self.tool_executor.execute(
                task_id=task_id,
                agent_id=agent_id,
                tool_name="web_search",
                arguments={
                    "query": query,
                    "max_results": self.settings.search_results_limit,
                },
                allowed_tools=["web_search"],
            )
            for item in result.get("results", []):
                url = item.get("url", "")
                normalized_url = _normalize_url(url)
                if normalized_url and normalized_url not in seen_urls:
                    seen_urls.add(normalized_url)
                    results.append(item)
            if query_index >= 1 and len(results) >= self.settings.search_results_limit:
                break
        lines = []
        for item in results[: self.settings.search_results_limit]:
            lines.append(
                f"- {item.get('title', '')}: {item.get('snippet', '')} ({item.get('url', '')})"
            )
        fetch_limit = min(self.settings.fetch_top_results, 2)
        if "web_fetch" in self.tool_executor.registry.list_names():
            for rank, item in enumerate(results[:fetch_limit], start=1):
                self._checkpoint(task_id)
                try:
                    fetched = await self.tool_executor.execute(
                        task_id=task_id,
                        agent_id=agent_id,
                        tool_name="web_fetch",
                        arguments={"url": item.get("url", "")},
                        allowed_tools=["web_fetch"],
                    )
                except Exception:
                    continue
                summary = str(fetched.get("summary") or fetched.get("text") or "")
                if not summary:
                    continue
                self.repository.save_evidence(
                    task_id=task_id,
                    title=item.get("title", "") or fetched.get("url", ""),
                    url=str(fetched.get("url") or item.get("url") or ""),
                    snippet=summary[:500],
                    source="web_fetch",
                    rank=rank,
                    source_type="fetched_page",
                    summary=summary[: self.settings.max_fetch_chars],
                )
                lines.append(
                    f"- Fetched page {item.get('title', '')}: "
                    f"{summary[: self.settings.max_fetch_chars]} "
                    f"({fetched.get('url') or item.get('url', '')})"
                )
        if not lines:
            return "", False
        return "Web evidence:\n" + "\n".join(lines), True

    async def _planned_queries(self, task_id: str, user_input: str) -> list[str]:
        if "query_planner" not in self.tool_executor.registry.list_names():
            return candidate_search_queries(user_input)
        try:
            result = await self.tool_executor.execute(
                task_id,
                None,
                "query_planner",
                {"query": user_input, "max_queries": self.settings.research_max_queries},
                ["query_planner"],
            )
            queries = [str(item) for item in result.get("queries", []) if str(item).strip()]
            return queries or candidate_search_queries(user_input)
        except Exception:
            return candidate_search_queries(user_input)

    async def _run_citation_check(self, task_id: str, synthesis: FinalSynthesis) -> bool:
        evidence = self.repository.list_evidence(task_id)
        valid_ids = {item["evidence_id"] for item in evidence}
        local_valid = bool(synthesis.citations) and all(
            citation.evidence_id in valid_ids for citation in synthesis.citations
        )
        if "citation_checker" not in self.tool_executor.registry.list_names():
            return local_valid
        try:
            result = await self.tool_executor.execute(
                task_id,
                None,
                "citation_checker",
                {
                    "task_id": task_id,
                    "citations": [item.model_dump() for item in synthesis.citations],
                },
                ["citation_checker"],
            )
            valid = bool(result.get("valid")) or (
                local_valid and bool(result.get("missing_evidence_ids"))
            )
            self.event_service.emit(
                task_id,
                None,
                EventType.CITATION_CHECK_COMPLETED,
                {**result, "valid": valid},
                "Citation check completed",
            )
            return valid
        except Exception as exc:
            self.event_service.emit(
                task_id,
                None,
                EventType.CITATION_CHECK_COMPLETED,
                {"valid": local_valid, "error": str(exc)},
                "Citation check completed with fallback",
            )
            return local_valid

    async def _run_optional_critic(
        self, task_id: str, synthesis: FinalSynthesis, web_used: bool
    ) -> bool:
        if "result_critic" not in self.tool_executor.registry.list_names():
            self.event_service.emit(
                task_id,
                None,
                EventType.CRITIC_SKIPPED,
                {"reason": "result_critic unavailable"},
                "Critic skipped",
            )
            return True
        try:
            result = await self.tool_executor.execute(
                task_id,
                None,
                "result_critic",
                {
                    "answer": synthesis.answer,
                    "citations": [item.model_dump() for item in synthesis.citations],
                    "web_used": web_used,
                },
                ["result_critic"],
            )
            self.event_service.emit(
                task_id,
                None,
                EventType.CRITIC_COMPLETED,
                result,
                "Critic completed",
            )
            issues = set(result.get("issues") or [])
            return "missing_citations" not in issues
        except Exception as exc:
            self.event_service.emit(
                task_id,
                None,
                EventType.CRITIC_SKIPPED,
                {"error": str(exc)},
                "Critic skipped",
            )
            return True

    async def _supplemental_research(self, task_id: str, agent_id: str | None) -> bool:
        task = self.repository.get_task(task_id)
        if task is None or "web_search" not in self.tool_executor.registry.list_names():
            return False
        before = {_normalize_url(item["url"]) for item in self.repository.list_evidence(task_id)}
        self._checkpoint(task_id)
        result = await self.tool_executor.execute(
            task_id=task_id,
            agent_id=agent_id,
            tool_name="web_search",
            arguments={
                "query": f"{task['input']} additional independent sources",
                "max_results": self.settings.search_results_limit,
            },
            allowed_tools=["web_search"],
        )
        return any(
            _normalize_url(item.get("url", "")) not in before
            for item in result.get("results", [])
            if _normalize_url(item.get("url", ""))
        )

    def _persisted_evidence_context(self, task_id: str) -> str:
        lines = [
            f"- {item['title']}: {item['summary']} ({item['url']})"
            for item in self.repository.list_evidence(task_id)
        ]
        return "Web evidence:\n" + "\n".join(lines)

    def _complete(self, task_id: str, synthesis: FinalSynthesis, degraded: bool) -> None:
        try:
            self._checkpoint(task_id)
        except TaskCancelledError:
            return
        self.result_service.save(task_id, synthesis)
        status = TaskStatus.DEGRADED if degraded else TaskStatus.COMPLETED
        self.repository.update_task_status(task_id, status)
        self.event_service.emit(
            task_id,
            None,
            EventType.FINAL_ANSWER_GENERATED,
            synthesis.model_dump(mode="json"),
            "Final answer generated",
        )
        workflow_node_id = f"workflow_{synthesis.used_workflow.value}"
        node_status = "degraded" if degraded else "completed"
        self.event_service.update_graph_node(
            task_id,
            workflow_node_id,
            "workflow",
            synthesis.used_workflow.value,
            node_status,
        )
        self.event_service.create_graph_node(
            task_id,
            "result_final",
            "result",
            "Final answer",
            node_status,
            metadata={"confidence": synthesis.confidence},
        )
        self.event_service.create_graph_edge(
            task_id,
            f"edge_{workflow_node_id}_result",
            workflow_node_id,
            "result_final",
            "produced",
        )
        self.event_service.emit(
            task_id,
            None,
            EventType.TASK_DEGRADED if degraded else EventType.TASK_COMPLETED,
            {},
            "Task degraded" if degraded else "Task completed",
        )

    async def _finish(self, task_id: str, synthesis: FinalSynthesis, degraded: bool) -> None:
        model_warnings = self.llm.consume_warnings(task_id)
        if model_warnings:
            degraded = True
            synthesis = synthesis.model_copy(
                update={"limitations": [*synthesis.limitations, *model_warnings]}
            )
            self.event_service.emit(
                task_id,
                None,
                EventType.WORKFLOW_DEGRADED,
                {"reason": "model_fallback", "warnings": model_warnings},
                "Model fallback was used",
            )
        task = self.repository.get_task(task_id)
        request = task["input"] if task else ""
        if _requests_artifact(request):
            try:
                artifact = await self._write_requested_artifact(task_id, request, synthesis)
                if _requests_archive(request):
                    await self.tool_executor.execute(
                        task_id,
                        None,
                        "artifact_archiver",
                        {
                            "task_id": task_id,
                            "filename": "task-artifacts.zip",
                            "artifact_ids": [artifact["artifact_id"]],
                        },
                        ["artifact_archiver"],
                    )
            except Exception as exc:
                degraded = True
                synthesis = synthesis.model_copy(
                    update={
                        "limitations": [
                            *synthesis.limitations,
                            f"Artifact generation failed: {type(exc).__name__}",
                        ]
                    }
                )
        self._complete(task_id, synthesis, degraded)

    async def _write_requested_artifact(
        self, task_id: str, request: str, synthesis: FinalSynthesis
    ) -> dict:
        lower = request.lower()
        if "csv" in lower:
            arguments = {
                "task_id": task_id,
                "filename": "task-result.csv",
                "format": "csv",
                "rows": [{"result": synthesis.answer}],
            }
        elif "json" in lower:
            arguments = {
                "task_id": task_id,
                "filename": "task-result.json",
                "format": "json",
                "content": {
                    "answer": synthesis.answer,
                    "citations": [citation.model_dump() for citation in synthesis.citations],
                    "limitations": synthesis.limitations,
                },
            }
        elif "txt" in lower or "文本文件" in request:
            arguments = {
                "task_id": task_id,
                "filename": "task-result.txt",
                "format": "txt",
                "content": synthesis.answer,
            }
        else:
            arguments = {
                "task_id": task_id,
                "filename": "task-report.md",
                "format": "md",
                "content": synthesis.answer,
            }
        return await self.tool_executor.execute(
            task_id,
            None,
            "artifact_writer",
            arguments,
            ["artifact_writer"],
        )

    def _is_cancelled(self, task_id: str) -> bool:
        task = self.repository.get_task(task_id)
        return bool(task and task["status"] == TaskStatus.CANCELLED.value)

    def _checkpoint(self, task_id: str) -> None:
        self.repository.check_task_runtime(
            task_id, timeout_seconds=self.settings.task_timeout_seconds
        )
        self._emit_checkpoint(task_id)

    def _emit_checkpoint(self, task_id: str) -> None:
        if self.checkpoint_callback is not None:
            self.checkpoint_callback(task_id)


def candidate_search_queries(user_input: str) -> list[str]:
    queries = []
    ascii_terms = re.findall(r"\b[A-Za-z][A-Za-z0-9_.-]{2,}\b", user_input)
    for term in ascii_terms:
        expanded = f"{term} multi-agent orchestration framework"
        if expanded not in queries:
            queries.append(expanded)
    if user_input not in queries:
        queries.append(user_input)
    return queries


def _extract_city(user_input: str) -> str:
    lower = user_input.lower()
    if "北京" in user_input:
        return "Beijing"
    if "上海" in user_input:
        return "Shanghai"
    if "beijing" in lower or "北京" in user_input:
        return "Beijing"
    if "shanghai" in lower or "上海" in user_input:
        return "Shanghai"
    words = re.findall(r"\b[A-Z][A-Za-z-]+\b", user_input)
    return words[0] if words else user_input.strip()


def _stringify_finding(item: object) -> str:
    if isinstance(item, dict | list):
        return json.dumps(item, ensure_ascii=False)
    return str(item)


def _normalize_url(url: str) -> str:
    try:
        parsed = urlsplit(url)
    except ValueError:
        return ""
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        return ""
    host = parsed.hostname.lower()
    port = f":{parsed.port}" if parsed.port else ""
    path = parsed.path.rstrip("/") or "/"
    return urlunsplit((parsed.scheme.lower(), f"{host}{port}", path, parsed.query, ""))


def _requests_artifact(user_input: str) -> bool:
    text = user_input.lower()
    return any(
        term in text
        for term in (
            "生成报告",
            "导出",
            "保存为文件",
            "生成文件",
            "csv",
            "json文件",
            "json file",
            "markdown file",
            "report file",
            "export",
            "zip",
            "压缩包",
            "打包",
        )
    )


def _requests_archive(user_input: str) -> bool:
    text = user_input.lower()
    return any(term in text for term in ("zip", "压缩包", "打包"))
