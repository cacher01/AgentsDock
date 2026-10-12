from typing import Any

from app.core.enums import EventType
from app.db.repositories import Repository
from app.services.event_service import EventService


class MessageBus:
    def __init__(self, repository: Repository, event_service: EventService):
        self.repository = repository
        self.event_service = event_service

    def send(
        self,
        task_id: str,
        sender_agent_id: str | None,
        receiver_agent_id: str | None,
        message_type: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        message = self.repository.save_message(
            task_id, sender_agent_id, receiver_agent_id, message_type, payload
        )
        self.event_service.emit(
            task_id,
            sender_agent_id,
            EventType.MESSAGE_SENT,
            {"message_type": message_type, "receiver_agent_id": receiver_agent_id},
            f"Message sent: {message_type}",
        )
        return message
