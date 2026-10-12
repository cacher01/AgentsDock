from fastapi.testclient import TestClient

from app.main import create_app


def test_api_creates_task_and_serves_result_with_mock_execution():
    app = create_app(testing=True)
    client = TestClient(app)

    response = client.post("/tasks", json={"input": "Explain orchestration"})
    assert response.status_code == 200
    task_id = response.json()["task_id"]

    task_response = client.get(f"/tasks/{task_id}")
    assert task_response.status_code == 200
    assert task_response.json()["task_id"] == task_id

    result_response = client.get(f"/tasks/{task_id}/result")
    assert result_response.status_code == 200
    assert "answer" in result_response.json()


def test_api_lists_events_and_serves_ui():
    app = create_app(testing=True)
    client = TestClient(app)

    task_id = client.post("/tasks", json={"input": "Explain orchestration"}).json()[
        "task_id"
    ]
    events_response = client.get(f"/tasks/{task_id}/events")
    ui_response = client.get("/ui")

    assert events_response.status_code == 200
    assert events_response.json()
    assert ui_response.status_code == 200
    assert "AgentsDock" in ui_response.text
