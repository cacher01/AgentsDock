from app.core.enums import WorkflowType
from app.orchestration.prompts import FINAL_SYNTHESIS_PROMPT
from app.orchestration.routing import route_by_rules


def test_rule_router_supports_chinese_workflow_triggers():
    assert route_by_rules("查询北京天气和当地时间").workflow == WorkflowType.REACT
    assert route_by_rules("调研特斯拉公司并给出引用").workflow == WorkflowType.RESEARCH
    assert (
        route_by_rules("分析多智能体框架的发展趋势、风险和适用场景").workflow
        == WorkflowType.SUPERVISOR
    )
    assert (
        route_by_rules("先收集资料，再分析竞争格局，最后形成风险报告").workflow
        == WorkflowType.DAG
    )
    assert route_by_rules("制定一个项目实施计划和步骤").workflow == WorkflowType.PLAN_EXECUTE
    comparison = route_by_rules(
        "综合对比分析特斯拉、小米、华为三家企业，并分析他们的发展趋势"
    )
    assert comparison.workflow == WorkflowType.SUPERVISOR
    assert comparison.requires_web is True


def test_final_synthesis_prompt_requests_detailed_structured_answer():
    assert "multiple sections" in FINAL_SYNTHESIS_PROMPT
    assert "complex tasks" in FINAL_SYNTHESIS_PROMPT
    assert "Do not compress" in FINAL_SYNTHESIS_PROMPT
