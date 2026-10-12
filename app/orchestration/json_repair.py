import re
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from app.core.errors import SchemaValidationError
from app.llm.client import LLMClient

SchemaT = TypeVar("SchemaT", bound=BaseModel)


async def parse_structured_output(
    llm: LLMClient,
    schema: type[SchemaT],
    messages: list[dict[str, str]],
    repair_prompt: str,
) -> SchemaT:
    response = await llm.chat(messages)
    parsed = _try_parse(schema, response.content)
    if parsed is not None:
        return parsed

    repair_messages = [
        {"role": "system", "content": repair_prompt},
        {"role": "user", "content": response.content},
    ]
    repaired = await llm.chat(repair_messages)
    parsed = _try_parse(schema, repaired.content)
    if parsed is not None:
        return parsed

    retry = await llm.chat(messages)
    parsed = _try_parse(schema, retry.content)
    if parsed is not None:
        return parsed
    raise SchemaValidationError(f"Could not parse {schema.__name__}")


def _try_parse(schema: type[SchemaT], content: str) -> SchemaT | None:
    for candidate in _json_candidates(content):
        try:
            return schema.model_validate_json(candidate)
        except (ValidationError, ValueError):
            continue
    return None


def _json_candidates(content: str) -> list[str]:
    candidates = [content.strip()]
    fenced = re.findall(r"```(?:json)?\s*(.*?)```", content, flags=re.DOTALL | re.IGNORECASE)
    candidates.extend(item.strip() for item in fenced)
    start = content.find("{")
    end = content.rfind("}")
    if start != -1 and end != -1 and end > start:
        candidates.append(content[start : end + 1])
    seen = set()
    unique = []
    for candidate in candidates:
        if candidate and candidate not in seen:
            seen.add(candidate)
            unique.append(candidate)
    return unique
