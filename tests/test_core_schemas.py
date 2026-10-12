from pathlib import Path

import pytest

from app.core.config import Settings
from app.core.enums import TaskStatus, WorkflowType
from app.schemas.workflow import (
    FinalSynthesis,
    RoutingDecision,
    SubAgentOutput,
    TaskDecomposition,
)


def test_settings_defaults_match_design_limits():
    settings = Settings()

    assert settings.max_swarm_rounds == 2
    assert settings.hard_max_swarm_rounds == 3
    assert settings.max_agents == 4
    assert settings.hard_max_agents == 6
    assert settings.max_concurrent_agents == 2
    assert settings.hard_max_concurrent_agents == 3


def test_settings_rejects_defaults_above_hard_limits():
    with pytest.raises(ValueError, match="max_agents"):
        Settings(max_agents=7, hard_max_agents=6)


def test_settings_loads_document_limits(monkeypatch):
    monkeypatch.setenv("INPUT_FILE_ROOT", "workspace-inputs")
    monkeypatch.setenv("INPUT_FILE_MAX_BYTES", "4096")
    monkeypatch.setenv("DOCUMENT_CONTEXT_MAX_CHARS", "12345")

    settings = Settings.from_environment()

    assert settings.input_file_root == "workspace-inputs"
    assert settings.input_file_max_bytes == 4096
    assert settings.document_context_max_chars == 12345


def test_settings_rejects_non_positive_document_limits():
    with pytest.raises(ValueError, match="INPUT_FILE_MAX_BYTES"):
        Settings(input_file_max_bytes=0)
    with pytest.raises(ValueError, match="DOCUMENT_CONTEXT_MAX_CHARS"):
        Settings(document_context_max_chars=0)


def test_settings_loads_deepseek_style_env_file(monkeypatch):
    env_file = Path(".test_env")
    try:
        env_file.write_text(
            "\n".join(
                [
                    "DEEPSEEK_API_KEY=test-key",
                    "DEEPSEEK_API_BASE_URL=https://deepseek.example/v1",
                    "MODEL_NAME=deepseek-test",
                ]
            ),
            encoding="utf-8",
        )
        monkeypatch.delenv("LLM_API_KEY", raising=False)
        monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
        monkeypatch.delenv("LLM_BASE_URL", raising=False)
        monkeypatch.delenv("DEEPSEEK_API_BASE_URL", raising=False)
        monkeypatch.delenv("LLM_MODEL", raising=False)
        monkeypatch.delenv("MODEL_NAME", raising=False)

        settings = Settings.from_env_file(env_file)

        assert settings.llm_api_key == "test-key"
        assert settings.llm_base_url == "https://deepseek.example/v1"
        assert settings.llm_model == "deepseek-test"
    finally:
        env_file.unlink(missing_ok=True)


def test_core_status_and_workflow_enums():
    assert TaskStatus.DEGRADED.value == "degraded"
    assert WorkflowType.SWARM.value == "swarm"


def test_routing_decision_schema_accepts_required_contract():
    decision = RoutingDecision(
        workflow="swarm",
        complexity="complex",
        reason="multi-object research",
        requires_web=True,
        expected_sub_agents=4,
        estimated_steps=5,
        risk_flags=[],
        constraints={
            "max_agents": 4,
            "max_swarm_rounds": 2,
            "max_concurrent_agents": 2,
        },
    )

    assert decision.workflow == WorkflowType.SWARM
    assert decision.constraints.max_agents == 4


def test_task_decomposition_requires_subtask_ids():
    decomposition = TaskDecomposition(
        objective="Compare orchestration frameworks.",
        plan_summary="Research and compare.",
        subtasks=[
            {
                "subtask_id": "subtask_1",
                "title": "Research Shannon",
                "description": "Find orchestration details.",
                "expected_output": "Key architecture notes.",
                "requires_web": True,
                "priority": 1,
                "depends_on": [],
            }
        ],
        success_criteria=["answer compares frameworks"],
    )

    assert decomposition.subtasks[0].subtask_id == "subtask_1"


def test_sub_agent_output_schema_is_structured():
    output = SubAgentOutput(
        status="completed",
        summary="Done",
        findings=["Finding"],
        evidence=[],
        open_questions=[],
        confidence=0.8,
        recommended_next_action="finish",
    )

    assert output.status == "completed"
    assert output.confidence == 0.8


def test_final_synthesis_requires_citations_when_web_used():
    with pytest.raises(ValueError, match="citations"):
        FinalSynthesis(
            answer="Answer",
            citations=[],
            limitations=[],
            confidence=0.5,
            used_workflow="plan_execute",
            web_used=True,
        )
