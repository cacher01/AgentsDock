from app.core.enums import EventType, TaskStatus
from app.db.database import init_database
from app.db.repositories import Repository
from app.services.event_service import EventService


def test_repository_creates_task_and_updates_status():
    connection = init_database(":memory:")
    repo = Repository(connection)

    task = repo.create_task("Explain orchestration")
    repo.update_task_status(task["task_id"], TaskStatus.RUNNING)
    loaded = repo.get_task(task["task_id"])

    assert loaded is not None
    assert loaded["input"] == "Explain orchestration"
    assert loaded["status"] == "running"


def test_event_service_assigns_sequence_and_lists_events():
    connection = init_database(":memory:")
    repo = Repository(connection)
    task = repo.create_task("Explain orchestration")
    events = EventService(repo)

    first = events.emit(
        task_id=task["task_id"],
        agent_id=None,
        event_type=EventType.TASK_CREATED,
        payload={"input": "Explain orchestration"},
        summary="Task created",
    )
    second = events.emit(
        task_id=task["task_id"],
        agent_id=None,
        event_type=EventType.WORKFLOW_SELECTED,
        payload={"workflow": "direct"},
        summary="Workflow selected",
    )

    listed = events.list_events(task["task_id"])

    assert first.sequence == 1
    assert second.sequence == 2
    assert [item["summary"] for item in listed] == ["Task created", "Workflow selected"]


def test_repository_saves_result_and_evidence_linkage():
    connection = init_database(":memory:")
    repo = Repository(connection)
    task = repo.create_task("Research Shannon")
    evidence = repo.save_evidence(
        task_id=task["task_id"],
        title="Shannon",
        url="https://example.com/shannon",
        snippet="A framework",
        source="tavily",
        rank=1,
        source_type="search_result",
        summary="A framework",
    )

    repo.save_result(
        task_id=task["task_id"],
        answer="Answer",
        citations=[
            {
                "title": "Shannon",
                "url": "https://example.com/shannon",
                "evidence_id": evidence["evidence_id"],
            }
        ],
        limitations=[],
        confidence=0.8,
        used_workflow="plan_execute",
    )

    result = repo.get_result(task["task_id"])

    assert result is not None
    assert result["citations"][0]["evidence_id"] == evidence["evidence_id"]
