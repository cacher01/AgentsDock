import asyncio
from pathlib import Path

from fastapi.testclient import TestClient

from app.core.enums import WorkflowType
from app.main import create_app
from app.schemas.workflow import FinalSynthesis


def test_artifact_list_and_download_are_task_scoped(tmp_path):
    app = create_app(testing=True)
    app.state.artifact_service.root = tmp_path / "artifacts"
    client = TestClient(app)
    first = app.state.repository.create_task("first")
    second = app.state.repository.create_task("second")
    artifact = app.state.artifact_service.write(
        first["task_id"],
        "report.md",
        "md",
        content="# Report",
    )

    listed = client.get(f"/tasks/{first['task_id']}/artifacts")
    downloaded = client.get(
        f"/tasks/{first['task_id']}/artifacts/{artifact['artifact_id']}"
    )
    cross_task = client.get(
        f"/tasks/{second['task_id']}/artifacts/{artifact['artifact_id']}"
    )

    assert listed.status_code == 200
    assert listed.json()[0]["artifact_id"] == artifact["artifact_id"]
    assert downloaded.status_code == 200
    assert downloaded.content == b"# Report"
    assert "attachment" in downloaded.headers["content-disposition"]
    assert cross_task.status_code == 404


def test_artifact_download_rejects_missing_file(tmp_path):
    app = create_app(testing=True)
    app.state.artifact_service.root = tmp_path / "artifacts"
    client = TestClient(app)
    task = app.state.repository.create_task("missing")
    artifact = app.state.artifact_service.write(
        task["task_id"], "report.md", "md", content="content"
    )
    path = app.state.artifact_service.resolve_artifact_path(artifact)
    Path(path).unlink()

    response = client.get(
        f"/tasks/{task['task_id']}/artifacts/{artifact['artifact_id']}"
    )

    assert response.status_code == 404


def test_explicit_report_request_creates_downloadable_artifact(tmp_path):
    app = create_app(testing=True)
    app.state.artifact_service.root = tmp_path / "artifacts"
    task = app.state.repository.create_task("请生成报告并保存为文件")
    app.state.repository.update_task_workflow(
        task["task_id"], "direct", task["input"]
    )
    synthesis = FinalSynthesis(
        answer="# Generated report",
        citations=[],
        limitations=[],
        confidence=0.8,
        used_workflow=WorkflowType.DIRECT,
        web_used=False,
    )

    asyncio.run(
        app.state.orchestrator._finish(
            task["task_id"], synthesis, degraded=False
        )
    )

    artifacts = app.state.repository.list_artifacts(task["task_id"])
    assert len(artifacts) == 1
    assert artifacts[0]["filename"] == "task-report.md"
