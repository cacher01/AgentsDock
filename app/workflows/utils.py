from __future__ import annotations

import json

from app.schemas.workflow import SubAgentOutput, SubTask


def format_agent_output(output: SubAgentOutput) -> str:
    findings = (
        "\n".join(
            f"- {json.dumps(item, ensure_ascii=False) if isinstance(item, dict | list) else item}"
            for item in output.findings
        )
        or "- none"
    )
    questions = "\n".join(f"- {item}" for item in output.open_questions) or "- none"
    return (
        f"Summary: {output.summary}\n"
        f"Findings:\n{findings}\n"
        f"Open questions:\n{questions}\n"
        f"Confidence: {output.confidence}"
    )


def has_cycle(subtasks: list[SubTask]) -> bool:
    graph = {subtask.subtask_id: list(subtask.depends_on) for subtask in subtasks}
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str) -> bool:
        if node in visiting:
            return True
        if node in visited:
            return False
        visiting.add(node)
        for dependency in graph.get(node, []):
            if dependency not in graph:
                continue
            if visit(dependency):
                return True
        visiting.remove(node)
        visited.add(node)
        return False

    return any(visit(node) for node in graph)


def has_invalid_dependencies(subtasks: list[SubTask]) -> bool:
    known = {subtask.subtask_id for subtask in subtasks}
    return any(
        dependency not in known or dependency == subtask.subtask_id
        for subtask in subtasks
        for dependency in subtask.depends_on
    )
