"""Aggregate token / cost / latency statistics from persisted events.

This service is read-only — it never writes to the database. It exists to power
the embedded quota chip and popover dashboard.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from app.db.repositories import Repository


# Pricing table (USD per 1M tokens). For demonstration; adjust to your provider.
# Default is used when the configured model is unknown — better to show *some*
# cost than to give up.
MODEL_PRICING: dict[str, dict[str, float]] = {
    "deepseek-chat": {"input": 0.14, "output": 0.28},
    "deepseek-reasoner": {"input": 0.55, "output": 2.19},
    "gpt-4o": {"input": 2.50, "output": 10.00},
    "gpt-4o-mini": {"input": 0.15, "output": 0.60},
    "claude-sonnet-4-6": {"input": 3.00, "output": 15.00},
    "claude-opus-4-6": {"input": 15.00, "output": 75.00},
    "claude-haiku-4-5": {"input": 0.80, "output": 4.00},
    "qwen-plus": {"input": 0.80, "output": 2.00},
    "moonshot-v1-8k": {"input": 2.00, "output": 2.00},
    "default": {"input": 0.50, "output": 1.50},
}


def model_cost(model: str | None, tokens: int) -> float:
    """Estimate USD cost for a given model and token count (assumes 50/50 in/out)."""
    pricing = MODEL_PRICING.get(model or "", MODEL_PRICING["default"])
    if tokens <= 0:
        return 0.0
    return tokens / 1_000_000 * (pricing["input"] + pricing["output"]) / 2


@dataclass
class QuotaService:
    repository: Repository
    model: str = "deepseek-chat"  # primary model configured for the session

    # ─── Summary ────────────────────────────────────────────────

    def summary(self, scope: str = "today") -> dict[str, Any]:
        where, since = self._scope_filter(scope)
        rows = self.repository.connection.execute(
            f"""
            select
              count(*) as tasks,
              sum(case when status in ('completed','degraded') then 1 else 0 end) as ok,
              sum(case when status = 'failed' then 1 else 0 end) as failed,
              sum(case when status = 'degraded' then 1 else 0 end) as degraded,
              sum(coalesce(token_estimate, 0)) as tokens,
              avg(
                case
                  when completed_at is not null and created_at is not null
                  then (julianday(completed_at) - julianday(created_at)) * 86400.0
                  else null
                end
              ) as avg_latency_s
            from tasks
            where 1=1 {where}
            """,
            since,
        ).fetchone()
        tasks = int(rows["tasks"] or 0)
        ok = int(rows["ok"] or 0)
        tokens = int(rows["tokens"] or 0)
        avg_latency = float(rows["avg_latency_s"] or 0.0)
        cost = model_cost(self.model, tokens)
        return {
            "scope": scope,
            "tasks": tasks,
            "completed": ok,
            "failed": int(rows["failed"] or 0),
            "degraded": int(rows["degraded"] or 0),
            "success_rate": round((ok / tasks) if tasks else 0.0, 4),
            "tokens": tokens,
            "cost_usd": round(cost, 4),
            "avg_latency_seconds": round(avg_latency, 2),
        }

    # ─── Breakdown ──────────────────────────────────────────────

    def breakdown(self, by: str, scope: str = "today") -> list[dict[str, Any]]:
        where, since = self._scope_filter(scope)
        if by == "workflow":
            rows = self.repository.connection.execute(
                f"""
                select coalesce(workflow, 'pending') as key,
                       count(*) as tasks,
                       sum(coalesce(token_estimate, 0)) as tokens
                from tasks
                where 1=1 {where}
                group by coalesce(workflow, 'pending')
                order by tokens desc
                """,
                since,
            ).fetchall()
        elif by == "model":
            # Token accounting does not distinguish models; synthesize
            # by attributing proportionally to configured model.
            # (In a real deployment you'd persist per-call model + tokens.)
            total = self.summary(scope)["tokens"]
            rows = [{"key": self.model, "tasks": self.summary(scope)["tasks"], "tokens": total}]
        else:
            raise ValueError(f"Unsupported breakdown dimension: {by}")
        out: list[dict[str, Any]] = []
        for row in rows:
            tokens = int(row["tokens"] or 0)
            out.append(
                {
                    "key": row["key"],
                    "tasks": int(row["tasks"] or 0),
                    "tokens": tokens,
                    "cost_usd": round(model_cost(self.model if by == "model" else None, tokens), 4),
                }
            )
        return out

    # ─── Timeline ───────────────────────────────────────────────

    def timeline(self, days: int = 7) -> list[dict[str, Any]]:
        since = _since(days)
        rows = self.repository.connection.execute(
            """
            select substr(created_at, 1, 10) as day,
                   count(*) as tasks,
                   sum(coalesce(token_estimate, 0)) as tokens
            from tasks
            where created_at >= ?
            group by substr(created_at, 1, 10)
            order by day asc
            """,
            (since,),
        ).fetchall()
        by_day = {row["day"]: row for row in rows}
        out: list[dict[str, Any]] = []
        for offset in range(days - 1, -1, -1):
            day = (datetime.now(timezone.utc).date() - timedelta(days=offset)).isoformat()
            row = by_day.get(day)
            tokens = int(row["tokens"] or 0) if row else 0
            tasks = int(row["tasks"] or 0) if row else 0
            out.append(
                {
                    "day": day,
                    "tasks": tasks,
                    "tokens": tokens,
                    "cost_usd": round(model_cost(self.model, tokens), 4),
                }
            )
        return out

    # ─── Recent tasks ───────────────────────────────────────────

    def recent_tasks(self, limit: int = 12) -> list[dict[str, Any]]:
        rows = self.repository.connection.execute(
            """
            select task_id, input, coalesce(workflow, 'pending') as workflow,
                   status, coalesce(token_estimate, 0) as tokens,
                   created_at, completed_at, session_id
            from tasks
            order by created_at desc
            limit ?
            """,
            (max(1, min(limit, 50)),),
        ).fetchall()
        return [
            {
                "task_id": row["task_id"],
                "input": row["input"],
                "workflow": row["workflow"],
                "status": row["status"],
                "tokens": int(row["tokens"] or 0),
                "cost_usd": round(model_cost(self.model, int(row["tokens"] or 0)), 4),
                "created_at": row["created_at"],
                "completed_at": row["completed_at"],
                "session_id": row["session_id"],
                "duration_s": round(
                    (
                        datetime.fromisoformat(row["completed_at"].replace("Z", "+00:00"))
                        - datetime.fromisoformat(row["created_at"].replace("Z", "+00:00"))
                    ).total_seconds(),
                    2,
                )
                if row["completed_at"]
                else None,
            }
            for row in rows
        ]

    # ─── Session consumption ────────────────────────────────────

    def session_consumption(self, limit: int = 8) -> list[dict[str, Any]]:
        rows = self.repository.connection.execute(
            """
            select s.session_id, s.title, s.created_at,
                   count(t.task_id) as tasks,
                   sum(coalesce(t.token_estimate, 0)) as tokens
            from sessions s
            left join tasks t on t.session_id = s.session_id
            group by s.session_id, s.title, s.created_at
            order by tokens desc, s.created_at desc
            limit ?
            """,
            (max(1, min(limit, 50)),),
        ).fetchall()
        return [
            {
                "session_id": row["session_id"],
                "title": row["title"],
                "tasks": int(row["tasks"] or 0),
                "tokens": int(row["tokens"] or 0),
                "cost_usd": round(model_cost(self.model, int(row["tokens"] or 0)), 4),
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    # ─── Rate limits ────────────────────────────────────────────

    def limits(self) -> dict[str, Any]:
        today = self.summary("today")
        all_time = self.summary("all")
        # Soft caps — adjust to your deployment's actual policy
        soft_daily_tokens = 500_000
        soft_daily_cost = 5.00
        return {
            "daily_tokens_cap": soft_daily_tokens,
            "daily_cost_cap": soft_daily_cost,
            "today_tokens": today["tokens"],
            "today_cost": today["cost_usd"],
            "tokens_pct": round(min(1.0, today["tokens"] / soft_daily_tokens), 4),
            "cost_pct": round(min(1.0, today["cost_usd"] / soft_daily_cost), 4),
            "all_time_tokens": all_time["tokens"],
            "all_time_cost": all_time["cost_usd"],
        }

    # ─── Helpers ────────────────────────────────────────────────

    def _scope_filter(self, scope: str) -> tuple[str, tuple]:
        if scope == "today":
            since = datetime.now(timezone.utc).date().isoformat()
            return "and substr(created_at, 1, 10) >= ?", (since,)
        return "", ()


def _since(days: int) -> str:
    return (datetime.now(timezone.utc).date() - timedelta(days=days - 1)).isoformat()
