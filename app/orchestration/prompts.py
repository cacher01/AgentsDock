JSON_REPAIR_PROMPT = (
    "Return only valid JSON matching the requested schema. "
    "Do not add markdown fences or explanatory text."
)

ROUTING_PROMPT = """Choose the workflow for the user task.
Return only JSON with this exact shape:
{
  "workflow": "direct | plan_execute | swarm | research | react | supervisor | dag",
  "complexity": "simple | medium | complex",
  "reason": "short reason",
  "requires_web": true,
  "expected_sub_agents": 0,
  "estimated_steps": 1,
  "risk_flags": [],
  "constraints": {
    "max_agents": 4,
    "max_swarm_rounds": 2,
    "max_concurrent_agents": 2
  }
}
Rules:
- direct: simple explanation or short answer.
- plan_execute: medium task, single main target, needs a few steps.
- research: source-grounded investigation or comparison requiring citations.
- react: tasks dominated by weather, time, calculation, date, or conversion tools.
- supervisor: broad multi-perspective analysis with independent analytical angles.
- dag: multi-step tasks with explicit dependencies such as first/then/finally.
- swarm: experimental; choose only when the user explicitly asks for swarm-style execution.
Return JSON only."""

DECOMPOSITION_PROMPT = """Decompose the task into an internal objective and subtasks.
Return only JSON with this exact shape:
{
  "objective": "normalized internal task objective",
  "plan_summary": "short execution plan",
  "subtasks": [
    {
      "subtask_id": "subtask_1",
      "title": "short title",
      "description": "what to do",
      "expected_output": "what to return",
      "requires_web": false,
      "priority": 1,
      "depends_on": []
    }
  ],
  "success_criteria": ["criterion"]
}
For comparison tasks with multiple named entities, create one independent research
subtask per entity before any comparison or synthesis subtask. Mark research subtasks
with requires_web=true. Keep each subtask focused enough for one worker agent.
Return JSON only."""

SPAWN_PLAN_PROMPT = """Create a sub-agent spawn plan for the current swarm round.
Return only JSON with this exact shape:
{
  "round": 1,
  "agents": [
    {
      "agent_name": "research_topic",
      "template": "research",
      "assigned_subtasks": ["subtask_1"],
      "goal": "specific goal",
      "context_brief": "only information this sub-agent needs",
      "allowed_tools": [],
      "expected_output": "structured notes",
      "stop_condition": "when the subtask is complete"
    }
  ]
}
template must be one of: research, analysis, review, synthesis.
Return JSON only."""

ROUND_EVALUATION_PROMPT = """Evaluate a completed swarm round.
Return only JSON with this exact shape:
{
  "round": 1,
  "status": "sufficient | needs_more_work | failed",
  "summary": "what happened",
  "completed_subtasks": [],
  "incomplete_subtasks": [],
  "knowledge_gaps": [],
  "needs_supplemental_search": false,
  "next_action": "finish | run_next_round | retry_failed | degrade_and_finish",
  "next_round_focus": ""
}
Return JSON only."""

REACT_DECISION_PROMPT = """Choose the next safe functional-tool action.
Return only JSON with this exact shape:
{
  "action": "tool_call | final_answer",
  "tool_name": "weather | current_time | date_calculator | unit_converter | calculator",
  "arguments": {},
  "summary": "short action summary",
  "answer": ""
}
Use only a tool listed as available in the user message.
Use final_answer when the observations are sufficient.
Do not select web, file, shell, browser, or code-execution tools.
Return JSON only."""

SUB_AGENT_PROMPT = """You are a task-scoped sub-agent. You cannot create other agents.
Use only the provided subtask context. Do not assume access to the original user question.
Return only JSON with this exact shape:
{
  "status": "completed | blocked | failed",
  "summary": "short summary",
  "findings": ["concise finding as a string"],
  "evidence": [],
  "open_questions": [],
  "confidence": 0.8,
  "recommended_next_action": "what the orchestrator should do next"
}
Return JSON only."""

FINAL_SYNTHESIS_PROMPT = """Generate the final user-facing answer.
Return only JSON with this exact shape:
{
  "answer": "final answer text",
  "citations": [],
  "limitations": [],
  "confidence": 0.8,
  "used_workflow": "direct | plan_execute | swarm | research | react | supervisor | dag",
  "web_used": false
}
If web evidence is provided, include citations using title, url, and evidence_id when available.
Write in the same language as the user.
For complex tasks, produce a detailed answer with multiple sections, clear headings,
key findings, supporting analysis, and a concise conclusion. Cover every requested
dimension and use bullet lists or tables when they improve readability.
Do not compress worker findings, research evidence, or multi-step results into one
short paragraph. A complex report should normally contain at least 5 substantive
paragraphs or equivalent structured content. Keep simple factual/tool answers concise.
Return JSON only."""
