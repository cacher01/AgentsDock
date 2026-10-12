from app.core.config import Settings
from app.core.enums import WorkflowType
from app.schemas.workflow import RoutingDecision


def route_by_rules(user_input: str) -> RoutingDecision:
    text = user_input.lower()
    weather_terms = (
        "weather",
        "temperature",
        "forecast",
        "timezone",
        "天气",
        "气温",
        "预报",
        "当地时间",
    )
    research_terms = (
        "research",
        "investigate",
        "survey",
        "citation",
        "citations",
        "调研",
        "研究",
        "引用",
        "资料来源",
    )
    dependency_terms = (
        "first",
        "then",
        "finally",
        "after that",
        "先",
        "然后",
        "再",
        "最后",
        "基于前一步",
    )
    supervisor_terms = (
        "trend",
        "trends",
        "risk",
        "risks",
        "use cases",
        "multiple perspectives",
        "发展趋势",
        "风险",
        "适用场景",
        "多角度",
        "综合分析",
    )
    comparison_terms = ("compare", "comparison", "versus", "vs.", "比较", "对比")
    planning_terms = (
        "plan",
        "steps",
        "roadmap",
        "implementation plan",
        "计划",
        "步骤",
        "路线图",
        "实施方案",
    )

    if _contains_any(text, weather_terms):
        return _decision(WorkflowType.REACT, "simple", "safe functional tool task", False, 2)
    if _contains_any(text, dependency_terms):
        return _decision(WorkflowType.DAG, "complex", "dependent subtask workflow", False, 5)
    if _contains_any(text, supervisor_terms):
        return _decision(
            WorkflowType.SUPERVISOR,
            "complex",
            "broad multi-perspective analysis",
            True,
            5,
        )
    if _contains_any(text, research_terms):
        return _decision(WorkflowType.RESEARCH, "medium", "source-grounded research task", True, 5)
    if _contains_any(text, comparison_terms):
        return _decision(WorkflowType.RESEARCH, "medium", "comparison needs evidence", True, 5)
    if _contains_any(text, planning_terms):
        return _decision(WorkflowType.PLAN_EXECUTE, "medium", "multi-step planning task", False, 4)
    return _decision(WorkflowType.DIRECT, "simple", "short answer task", False, 1)


def reconcile_routing_decisions(
    user_input: str,
    rule_decision: RoutingDecision,
    llm_decision: RoutingDecision,
) -> RoutingDecision:
    if llm_decision.workflow == WorkflowType.SWARM and _explicit_swarm_request(user_input):
        return llm_decision
    if rule_decision.workflow != WorkflowType.DIRECT:
        return rule_decision

    if llm_decision.workflow == WorkflowType.SWARM and not _explicit_swarm_request(user_input):
        if llm_decision.requires_web:
            return _decision(
                WorkflowType.RESEARCH,
                llm_decision.complexity,
                "swarm is experimental; routed to research",
                True,
                llm_decision.estimated_steps,
            )
        return _decision(
            WorkflowType.PLAN_EXECUTE,
            "medium" if llm_decision.complexity == "simple" else llm_decision.complexity,
            "swarm is experimental; routed to plan_execute",
            False,
            llm_decision.estimated_steps,
        )
    return llm_decision


def apply_routing_guardrails(decision: RoutingDecision, settings: Settings) -> RoutingDecision:
    if decision.workflow == WorkflowType.SWARM and decision.expected_sub_agents == 0:
        decision.workflow = WorkflowType.PLAN_EXECUTE
    if decision.workflow == WorkflowType.SWARM:
        decision.expected_sub_agents = min(decision.expected_sub_agents, settings.max_agents)
        decision.constraints.max_agents = min(decision.constraints.max_agents, settings.max_agents)
        decision.constraints.max_swarm_rounds = min(
            decision.constraints.max_swarm_rounds, settings.max_swarm_rounds
        )
        decision.constraints.max_concurrent_agents = min(
            decision.constraints.max_concurrent_agents, settings.max_concurrent_agents
        )
    return decision


def _decision(
    workflow: WorkflowType,
    complexity: str,
    reason: str,
    requires_web: bool,
    estimated_steps: int,
) -> RoutingDecision:
    return RoutingDecision(
        workflow=workflow,
        complexity=complexity,  # type: ignore[arg-type]
        reason=reason,
        requires_web=requires_web,
        expected_sub_agents=0,
        estimated_steps=max(1, estimated_steps),
        risk_flags=[],
    )


def _contains_any(text: str, terms: tuple[str, ...]) -> bool:
    return any(term in text for term in terms)


def _explicit_swarm_request(user_input: str) -> bool:
    return _contains_any(
        user_input.lower(),
        ("swarm", "蜂群", "群体智能", "使用多个动态子智能体", "使用多个动态子agent"),
    )
