import json
from pathlib import Path

from app.core.enums import EventType, TaskStatus
from app.db.database import init_database
from app.db.repositories import Repository
from app.services.event_service import EventService


ROOT = Path(__file__).parents[1]
EVENT_DIR = ROOT / "contracts/events/v1"
ENVELOPE = json.loads((EVENT_DIR / "envelope.json").read_text(encoding="utf-8"))
ENUMS = json.loads((EVENT_DIR / "enums.json").read_text(encoding="utf-8"))


def test_task_status_and_event_type_values_are_append_only_compatible():
    assert {item.value for item in TaskStatus} == set(ENUMS["task_statuses"])
    assert {item.value for item in EventType} == set(ENUMS["event_types"])


def test_persisted_event_envelope_keeps_required_fields_and_types():
    repository = Repository(init_database(":memory:"))
    task = repository.create_task("event contract")
    event = EventService(repository).emit(
        task["task_id"],
        None,
        EventType.TASK_CREATED,
        {"input": "event contract"},
        "Task created",
    ).to_dict()

    assert set(event) == set(ENVELOPE["required_fields"])
    assert isinstance(event["event_id"], str)
    assert isinstance(event["task_id"], str)
    assert event["agent_id"] is None
    assert isinstance(event["type"], str)
    assert isinstance(event["timestamp"], str)
    assert isinstance(event["sequence"], int)
    assert isinstance(event["payload"], dict)
    assert isinstance(event["summary"], str)
