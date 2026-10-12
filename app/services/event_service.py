import asyncio
from dataclasses import dataclass
from typing import Any

from app.core.enums import EventType
from app.db.repositories import Repository


@dataclass
class EventEnvelope:
    event_id: str
    task_id: str
    agent_id: str | None
    type: str
    timestamp: str
    sequence: int
    payload: dict[str, Any]
    summary: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "task_id": self.task_id,
            "agent_id": self.agent_id,
            "type": self.type,
            "timestamp": self.timestamp,
            "sequence": self.sequence,
            "payload": self.payload,
            "summary": self.summary,
        }


class EventService:
    def __init__(self, repository: Repository):
        self.repository = repository
        self._subscribers: dict[str, list[asyncio.Queue[dict[str, Any]]]] = {}

    def emit(
        self,
        task_id: str,
        agent_id: str | None,
        event_type: EventType,
        payload: dict[str, Any],
        summary: str,
    ) -> EventEnvelope:
        event = self.repository.append_event(
            task_id=task_id,
            agent_id=agent_id,
            event_type=event_type.value,
            payload=payload,
            summary=summary,
        )
        envelope = EventEnvelope(**event)
        for queue in self._subscribers.get(task_id, []):
            queue.put_nowait(envelope.to_dict())
        return envelope

    def list_events(self, task_id: str) -> list[dict[str, Any]]:
        return self.repository.list_events(task_id)

    def subscribe(self, task_id: str) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._subscribers.setdefault(task_id, []).append(queue)
        return queue

    def unsubscribe(self, task_id: str, queue: asyncio.Queue[dict[str, Any]]) -> None:
        queues = self._subscribers.get(task_id, [])
        if queue in queues:
            queues.remove(queue)

    def create_graph_node(
        self,
        task_id: str,
        node_id: str,
        node_type: str,
        label: str,
        status: str,
        ref_id: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> EventEnvelope:
        return self.emit(
            task_id,
            None,
            EventType.GRAPH_NODE_CREATED,
            {
                "node_id": node_id,
                "node_type": node_type,
                "label": label,
                "status": status,
                "ref_id": ref_id,
                "metadata": metadata or {},
            },
            f"Graph node created: {label}",
        )

    def update_graph_node(
        self,
        task_id: str,
        node_id: str,
        node_type: str,
        label: str,
        status: str,
        ref_id: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> EventEnvelope:
        return self.emit(
            task_id,
            None,
            EventType.GRAPH_NODE_UPDATED,
            {
                "node_id": node_id,
                "node_type": node_type,
                "label": label,
                "status": status,
                "ref_id": ref_id,
                "metadata": metadata or {},
            },
            f"Graph node updated: {label}",
        )

    def create_graph_edge(
        self,
        task_id: str,
        edge_id: str,
        source: str,
        target: str,
        edge_type: str,
    ) -> EventEnvelope:
        return self.emit(
            task_id,
            None,
            EventType.GRAPH_EDGE_CREATED,
            {
                "edge_id": edge_id,
                "source": source,
                "target": target,
                "edge_type": edge_type,
            },
            f"Graph edge created: {source} -> {target}",
        )
