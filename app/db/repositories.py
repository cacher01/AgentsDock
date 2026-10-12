import json
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any

from app.core.enums import AgentStatus, TaskStatus, ToolCallStatus
from app.core.errors import TaskCancelledError, TaskTimeoutError


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return dict(row)


class Repository:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def create_session(self, title: str) -> dict[str, Any]:
        now = _now()
        session_id = _id("session")
        self.connection.execute(
            """
            insert into sessions (session_id, title, status, created_at, updated_at)
            values (?, ?, 'active', ?, ?)
            """,
            (session_id, title.strip() or "New session", now, now),
        )
        self.connection.commit()
        return self.get_session(session_id) or {}

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            "select * from sessions where session_id = ?", (session_id,)
        ).fetchone()
        return _row_to_dict(row)

    def list_sessions(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            select s.*, count(t.task_id) as task_count
            from sessions s
            left join tasks t on t.session_id = s.session_id
            group by s.session_id
            order by s.updated_at desc
            limit ?
            """,
            (max(1, min(limit, 200)),),
        ).fetchall()
        return [dict(row) for row in rows]

    def list_session_tasks(self, session_id: str) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "select * from tasks where session_id = ? order by created_at asc",
            (session_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def create_task(
        self,
        input_text: str,
        session_id: str | None = None,
        *,
        task_id: str | None = None,
    ) -> dict[str, Any]:
        now = _now()
        task_id = task_id or _id("task")
        if session_id and self.get_session(session_id) is None:
            raise ValueError(f"Session not found: {session_id}")
        self.connection.execute(
            """
            insert into tasks
            (task_id, session_id, input, status, created_at, updated_at)
            values (?, ?, ?, ?, ?, ?)
            """,
            (
                task_id,
                session_id,
                input_text,
                TaskStatus.CREATED.value,
                now,
                now,
            ),
        )
        if session_id:
            self.connection.execute(
                "update sessions set updated_at = ? where session_id = ?",
                (now, session_id),
            )
        self.connection.commit()
        task = self.get_task(task_id)
        assert task is not None
        return task

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            "select * from tasks where task_id = ?", (task_id,)
        ).fetchone()
        return _row_to_dict(row)

    def list_tasks(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            select task_id, session_id, input, objective, workflow, status, token_estimate,
                   error_summary, created_at, updated_at, completed_at
            from tasks
            order by created_at desc
            limit ?
            """,
            (max(1, min(limit, 200)),),
        ).fetchall()
        return [dict(row) for row in rows]

    def session_context_for_task(self, task_id: str, limit: int = 5) -> str:
        task = self.get_task(task_id)
        if task is None or not task.get("session_id"):
            return ""
        rows = self.connection.execute(
            """
            select t.input, t.workflow, r.answer
            from tasks t
            left join results r on r.task_id = t.task_id
            where t.session_id = ? and t.created_at < ?
            order by t.created_at desc
            limit ?
            """,
            (task["session_id"], task["created_at"], max(1, min(limit, 10))),
        ).fetchall()
        if not rows:
            return ""
        parts = ["Previous tasks in this session:"]
        for row in reversed(rows):
            answer = (row["answer"] or "")[:2000]
            parts.append(
                f"User task: {row['input']}\n"
                f"Workflow: {row['workflow'] or 'unknown'}\n"
                f"Result summary: {answer}"
            )
        return "\n\n".join(parts)

    def list_agents(self, task_id: str) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "select * from agents where task_id = ? order by created_at asc",
            (task_id,),
        ).fetchall()
        agents = []
        for row in rows:
            agent = dict(row)
            agent["allowed_tools"] = json.loads(agent["allowed_tools"])
            agents.append(agent)
        return agents

    def list_tool_calls(self, task_id: str) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "select * from tool_calls where task_id = ? order by created_at asc",
            (task_id,),
        ).fetchall()
        calls = []
        for row in rows:
            call = dict(row)
            call["arguments"] = json.loads(call["arguments"])
            calls.append(call)
        return calls

    def find_completed_tool_result(
        self,
        task_id: str,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any] | None:
        arguments_json = json.dumps(arguments, ensure_ascii=False, sort_keys=True)
        for call in reversed(self.list_tool_calls(task_id)):
            if call["tool_name"] != tool_name or call["status"] != "completed":
                continue
            if json.dumps(call["arguments"], ensure_ascii=False, sort_keys=True) != arguments_json:
                continue
            if not call.get("result_json"):
                return None
            return json.loads(call["result_json"])
        return None

    def update_task_status(self, task_id: str, status: TaskStatus, error_summary: str = "") -> None:
        completed_at = (
            _now()
            if status
            in {
                TaskStatus.COMPLETED,
                TaskStatus.DEGRADED,
                TaskStatus.FAILED,
                TaskStatus.CANCELLED,
            }
            else None
        )
        self.connection.execute(
            """
            update tasks
            set status = ?, error_summary = ?, updated_at = ?, completed_at = coalesce(?, completed_at)
            where task_id = ?
            """,
            (status.value, error_summary, _now(), completed_at, task_id),
        )
        self.connection.commit()

    def update_task_workflow(self, task_id: str, workflow: str, objective: str) -> None:
        self.connection.execute(
            "update tasks set workflow = ?, objective = ?, updated_at = ? where task_id = ?",
            (workflow, objective, _now(), task_id),
        )
        self.connection.commit()

    def increment_task_tokens(self, task_id: str, amount: int) -> None:
        if amount <= 0:
            return
        self.connection.execute(
            """
            update tasks
            set token_estimate = token_estimate + ?, updated_at = ?
            where task_id = ?
            """,
            (amount, _now(), task_id),
        )
        self.connection.commit()

    def check_task_runtime(self, task_id: str, timeout_seconds: int) -> None:
        task = self.get_task(task_id)
        if task is None:
            raise ValueError(f"Task not found: {task_id}")
        if task["status"] == TaskStatus.CANCELLED.value:
            raise TaskCancelledError(f"Task cancelled: {task_id}")
        created_at = datetime.fromisoformat(task["created_at"])
        elapsed = (datetime.now(timezone.utc) - created_at).total_seconds()
        if elapsed > timeout_seconds:
            raise TaskTimeoutError(f"Task exceeded timeout of {timeout_seconds} seconds")

    def create_agent(
        self,
        task_id: str,
        name: str,
        template: str,
        goal: str,
        context_brief: str,
        allowed_tools: list[str],
        status: AgentStatus = AgentStatus.CREATED,
    ) -> dict[str, Any]:
        now = _now()
        agent_id = _id("agent")
        self.connection.execute(
            """
            insert into agents
            (agent_id, task_id, name, template, goal, context_brief, allowed_tools, status, created_at, updated_at)
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                agent_id,
                task_id,
                name,
                template,
                goal,
                context_brief,
                json.dumps(allowed_tools),
                status.value,
                now,
                now,
            ),
        )
        self.connection.commit()
        row = self.connection.execute(
            "select * from agents where agent_id = ?", (agent_id,)
        ).fetchone()
        agent = _row_to_dict(row)
        assert agent is not None
        agent["allowed_tools"] = json.loads(agent["allowed_tools"])
        return agent

    def update_agent_status(self, agent_id: str, status: AgentStatus) -> None:
        self.connection.execute(
            "update agents set status = ?, updated_at = ? where agent_id = ?",
            (status.value, _now(), agent_id),
        )
        self.connection.commit()

    def append_event(
        self,
        task_id: str,
        agent_id: str | None,
        event_type: str,
        payload: dict[str, Any],
        summary: str,
    ) -> dict[str, Any]:
        current = self.connection.execute(
            "select coalesce(max(sequence), 0) from events where task_id = ?",
            (task_id,),
        ).fetchone()[0]
        sequence = int(current) + 1
        event_id = _id("event")
        timestamp = _now()
        self.connection.execute(
            """
            insert into events
            (event_id, task_id, agent_id, type, timestamp, sequence, payload, summary)
            values (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_id,
                task_id,
                agent_id,
                event_type,
                timestamp,
                sequence,
                json.dumps(payload, ensure_ascii=False),
                summary,
            ),
        )
        self.connection.commit()
        return {
            "event_id": event_id,
            "task_id": task_id,
            "agent_id": agent_id,
            "type": event_type,
            "timestamp": timestamp,
            "sequence": sequence,
            "payload": payload,
            "summary": summary,
        }

    def list_events(self, task_id: str) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "select * from events where task_id = ? order by sequence asc",
            (task_id,),
        ).fetchall()
        events: list[dict[str, Any]] = []
        for row in rows:
            event = dict(row)
            event["payload"] = json.loads(event["payload"])
            events.append(event)
        return events

    def save_message(
        self,
        task_id: str,
        sender_agent_id: str | None,
        receiver_agent_id: str | None,
        message_type: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        message_id = _id("msg")
        created_at = _now()
        self.connection.execute(
            """
            insert into messages
            (message_id, task_id, sender_agent_id, receiver_agent_id, type, payload, created_at)
            values (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                message_id,
                task_id,
                sender_agent_id,
                receiver_agent_id,
                message_type,
                json.dumps(payload, ensure_ascii=False),
                created_at,
            ),
        )
        self.connection.commit()
        return {
            "message_id": message_id,
            "task_id": task_id,
            "sender_agent_id": sender_agent_id,
            "receiver_agent_id": receiver_agent_id,
            "type": message_type,
            "payload": payload,
            "created_at": created_at,
        }

    def save_tool_call(
        self,
        task_id: str,
        agent_id: str | None,
        tool_name: str,
        arguments: dict[str, Any],
        status: ToolCallStatus,
        result_summary: str = "",
        result: dict[str, Any] | None = None,
        error: str = "",
    ) -> dict[str, Any]:
        tool_call_id = _id("tool")
        now = _now()
        completed_at = now if status in {ToolCallStatus.COMPLETED, ToolCallStatus.FAILED} else None
        self.connection.execute(
            """
            insert into tool_calls
            (tool_call_id, task_id, agent_id, tool_name, arguments, status, result_summary, result_json, error, created_at, completed_at)
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                tool_call_id,
                task_id,
                agent_id,
                tool_name,
                json.dumps(arguments, ensure_ascii=False),
                status.value,
                result_summary,
                json.dumps(result, ensure_ascii=False) if result is not None else "",
                error,
                now,
                completed_at,
            ),
        )
        self.connection.commit()
        return {"tool_call_id": tool_call_id, "status": status.value}

    def save_evidence(
        self,
        task_id: str,
        title: str,
        url: str,
        snippet: str,
        source: str,
        rank: int,
        source_type: str,
        summary: str,
    ) -> dict[str, Any]:
        evidence_id = _id("evidence")
        created_at = _now()
        self.connection.execute(
            """
            insert into evidence
            (evidence_id, task_id, title, url, snippet, source, rank, source_type, summary, created_at)
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                evidence_id,
                task_id,
                title,
                url,
                snippet,
                source,
                rank,
                source_type,
                summary,
                created_at,
            ),
        )
        self.connection.commit()
        return {
            "evidence_id": evidence_id,
            "task_id": task_id,
            "title": title,
            "url": url,
            "snippet": snippet,
            "source": source,
            "rank": rank,
            "source_type": source_type,
            "summary": summary,
            "created_at": created_at,
        }

    def list_evidence(self, task_id: str) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "select * from evidence where task_id = ? order by rank asc", (task_id,)
        ).fetchall()
        return [dict(row) for row in rows]

    def save_result(
        self,
        task_id: str,
        answer: str,
        citations: list[dict[str, Any]],
        limitations: list[str],
        confidence: float,
        used_workflow: str,
    ) -> dict[str, Any]:
        result_id = _id("result")
        created_at = _now()
        self.connection.execute(
            """
            insert or replace into results
            (result_id, task_id, answer, citations, limitations, confidence, used_workflow, created_at)
            values (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                result_id,
                task_id,
                answer,
                json.dumps(citations, ensure_ascii=False),
                json.dumps(limitations, ensure_ascii=False),
                confidence,
                used_workflow,
                created_at,
            ),
        )
        self.connection.commit()
        return self.get_result(task_id) or {}

    def get_result(self, task_id: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            "select * from results where task_id = ?", (task_id,)
        ).fetchone()
        result = _row_to_dict(row)
        if result is None:
            return None
        result["citations"] = json.loads(result["citations"])
        result["limitations"] = json.loads(result["limitations"])
        return result

    def register_artifact(
        self,
        task_id: str,
        filename: str,
        media_type: str,
        size_bytes: int,
        relative_path: str,
    ) -> dict[str, Any]:
        if self.get_task(task_id) is None:
            raise ValueError(f"Task not found: {task_id}")
        artifact_id = _id("artifact")
        created_at = _now()
        self.connection.execute(
            """
            insert into artifacts
            (artifact_id, task_id, filename, media_type, size_bytes, relative_path, created_at)
            values (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                artifact_id,
                task_id,
                filename,
                media_type,
                int(size_bytes),
                relative_path,
                created_at,
            ),
        )
        self.connection.commit()
        artifact = self.get_artifact(task_id, artifact_id)
        assert artifact is not None
        return artifact

    def list_artifacts(self, task_id: str) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            select * from artifacts
            where task_id = ?
            order by created_at asc, artifact_id asc
            """,
            (task_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def get_artifact(self, task_id: str, artifact_id: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            """
            select * from artifacts
            where task_id = ? and artifact_id = ?
            """,
            (task_id, artifact_id),
        ).fetchone()
        return _row_to_dict(row)

    def artifact_usage(self, task_id: str) -> dict[str, int]:
        row = self.connection.execute(
            """
            select count(*) as count, coalesce(sum(size_bytes), 0) as total_size_bytes
            from artifacts
            where task_id = ?
            """,
            (task_id,),
        ).fetchone()
        return {
            "count": int(row["count"]),
            "total_size_bytes": int(row["total_size_bytes"]),
        }

    def count_artifacts(self, task_id: str) -> int:
        return self.artifact_usage(task_id)["count"]

    def total_artifact_size(self, task_id: str) -> int:
        return self.artifact_usage(task_id)["total_size_bytes"]
