from fastapi.testclient import TestClient

from app.main import create_app


def test_session_can_group_multiple_tasks_and_return_history():
    app = create_app(testing=True)
    client = TestClient(app)

    session = client.post("/sessions", json={"title": "Tesla analysis"}).json()
    first = client.post(
        f"/sessions/{session['session_id']}/tasks",
        json={"input": "Explain orchestration"},
    ).json()
    second = client.post(
        f"/sessions/{session['session_id']}/tasks",
        json={"input": "Explain orchestration again"},
    ).json()

    loaded = client.get(f"/sessions/{session['session_id']}").json()

    assert loaded["session"]["title"] == "Tesla analysis"
    assert [task["task_id"] for task in loaded["tasks"]] == [
        first["task_id"],
        second["task_id"],
    ]


def test_sessions_are_listed_by_recent_activity():
    app = create_app(testing=True)
    client = TestClient(app)
    created = client.post("/sessions", json={"title": "Long task"}).json()

    response = client.get("/sessions")

    assert response.status_code == 200
    assert response.json()[0]["session_id"] == created["session_id"]
