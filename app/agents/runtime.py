from app.agents.context import build_sub_agent_context
from app.llm.client import LLMClient
from app.orchestration.json_repair import parse_structured_output
from app.orchestration.prompts import JSON_REPAIR_PROMPT, SUB_AGENT_PROMPT
from app.schemas.workflow import SpawnAgentSpec, SubAgentOutput


class AgentRuntime:
    def __init__(self, llm: LLMClient):
        self.llm = llm

    async def run_sub_agent(self, spec: SpawnAgentSpec) -> SubAgentOutput:
        messages = [
            {
                "role": "system",
                "content": SUB_AGENT_PROMPT,
            },
            {"role": "user", "content": build_sub_agent_context(spec)},
        ]
        return await parse_structured_output(
            llm=self.llm,
            schema=SubAgentOutput,
            messages=messages,
            repair_prompt=JSON_REPAIR_PROMPT,
        )
