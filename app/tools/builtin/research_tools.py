import re
from collections.abc import Callable
from urllib.parse import urlparse


class QueryPlannerTool:
    name = "query_planner"
    description = "Create a compact set of research queries."

    async def run(self, arguments: dict) -> dict:
        query = str(arguments.get("query") or arguments.get("input") or "").strip()
        max_queries = int(arguments.get("max_queries") or 5)
        queries: list[str] = []
        for term in re.findall(r"\b[A-Za-z][A-Za-z0-9_.-]{2,}\b", query):
            _add(queries, f"{term} official documentation")
            _add(queries, f"{term} multi-agent orchestration framework")
        _add(queries, query)
        if "company" in query.lower() or "research" in query.lower():
            _add(queries, f"{query} business financial competitors recent news")
        return {"queries": queries[:max_queries]}


class CitationCheckerTool:
    name = "citation_checker"
    description = "Validate final citations against persisted evidence."

    def __init__(self, evidence_loader: Callable[[str], list[dict]] | None = None) -> None:
        self.evidence_loader = evidence_loader or (lambda task_id: [])

    async def run(self, arguments: dict) -> dict:
        task_id = str(arguments.get("task_id") or "")
        citations = list(arguments.get("citations") or [])
        evidence = self.evidence_loader(task_id)
        valid_ids = {item.get("evidence_id") for item in evidence}
        domains: dict[str, int] = {}
        missing = []
        for citation in citations:
            evidence_id = citation.get("evidence_id")
            if evidence_id and evidence_id not in valid_ids:
                missing.append(evidence_id)
            url = citation.get("url") or ""
            domain = urlparse(url).netloc
            if domain:
                domains[domain] = domains.get(domain, 0) + 1
        duplicate_domains = [domain for domain, count in domains.items() if count > 2]
        return {
            "valid": not missing and not duplicate_domains,
            "missing_evidence_ids": missing,
            "duplicate_domains": duplicate_domains,
            "citation_count": len(citations),
        }


class ResultCriticTool:
    name = "result_critic"
    description = "Apply lightweight rule checks to a final answer."

    async def run(self, arguments: dict) -> dict:
        answer = str(arguments.get("answer") or "")
        citations = list(arguments.get("citations") or [])
        issues = []
        if len(answer.strip()) < 80:
            issues.append("answer_is_short")
        if arguments.get("web_used") and not citations:
            issues.append("missing_citations")
        return {
            "acceptable": not issues,
            "issues": issues,
            "recommendation": "accept" if not issues else "revise_or_degrade",
        }


def _add(items: list[str], value: str) -> None:
    value = " ".join(value.split())
    if value and value not in items:
        items.append(value)
