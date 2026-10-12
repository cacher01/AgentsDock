import json
from pathlib import Path

from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from app.main import create_app


ROOT = Path(__file__).parents[1]
CONTRACT = json.loads(
    (ROOT / "contracts/openapi/legacy-v1.json").read_text(encoding="utf-8")
)


def _business_routes():
    app = create_app(testing=True)
    routes = set()
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        if route.endpoint.__module__ != "app.api.routes":
            continue
        for method in route.methods or set():
            if method not in {"HEAD", "OPTIONS"}:
                routes.add((method, route.path, route.name))
    return routes


def test_legacy_business_route_contract_is_exact():
    expected = {
        (item["method"], item["path"], item["name"])
        for item in CONTRACT["routes"]
    }
    assert len(expected) == 23
    assert _business_routes() == expected


def test_task_result_and_replay_fields_remain_compatible():
    app = create_app(testing=True)
    with TestClient(app) as client:
        created = client.post("/tasks", json={"input": "contract task"}).json()
        task = client.get(f"/tasks/{created['task_id']}").json()
        result = client.get(f"/tasks/{created['task_id']}/result").json()
        replay = client.get(f"/tasks/{created['task_id']}/replay").json()

    fields = CONTRACT["response_fields"]
    assert set(created) == set(fields["task_create"])
    assert set(task) == set(fields["task"])
    assert set(result) == set(fields["result"])
    assert set(replay) == set(fields["replay"])


def test_sse_terminal_status_mapping_is_stable():
    from app.api.routes import _sse

    for event_type, status in CONTRACT["sse_terminal_status"].items():
        encoded = _sse({"type": event_type, "payload": {}})
        assert f'"status": "{status}"' in encoded
