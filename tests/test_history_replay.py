from fastapi.testclient import TestClient

from app.main import create_app


def test_task_history_lists_recent_tasks():
    app = create_app(testing=True)
    client = TestClient(app)
    created = client.post("/tasks", json={"input": "Explain orchestration"}).json()

    response = client.get("/tasks?limit=10")

    assert response.status_code == 200
    assert response.json()[0]["task_id"] == created["task_id"]
    assert response.json()[0]["input"] == "Explain orchestration"


def test_task_replay_returns_task_events_and_result():
    app = create_app(testing=True)
    client = TestClient(app)
    task_id = client.post("/tasks", json={"input": "Explain orchestration"}).json()[
        "task_id"
    ]

    response = client.get(f"/tasks/{task_id}/replay")

    assert response.status_code == 200
    payload = response.json()
    assert payload["task"]["task_id"] == task_id
    assert payload["events"]
    assert payload["result"]["answer"]
    assert "evidence" in payload
