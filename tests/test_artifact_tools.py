import asyncio
import csv
import json
import zipfile
from pathlib import Path

import pytest

from app.core.config import Settings
from app.db.database import init_database
from app.db.repositories import Repository
from app.services.artifact_service import ArtifactService
from app.tools.builtin.artifacts import ArtifactArchiverTool, ArtifactWriterTool


def _setup(tmp_path: Path, **settings_overrides):
    settings = Settings(artifact_root=str(tmp_path / "artifacts"), **settings_overrides)
    repository = Repository(init_database(":memory:"))
    service = ArtifactService(settings, repository)
    task = repository.create_task("create artifacts")
    return service, repository, task


def _run(tool, arguments):
    return asyncio.run(tool.run(arguments))


def test_writer_creates_markdown_inside_task_directory(tmp_path):
    service, repository, task = _setup(tmp_path)
    tool = ArtifactWriterTool(service)

    result = _run(
        tool,
        {
            "task_id": task["task_id"],
            "filename": "report",
            "format": "md",
            "content": "# Report\n\nResult",
        },
    )

    artifact = repository.get_artifact(task["task_id"], result["artifact_id"])
    path = service.resolve_artifact_path(artifact)
    assert artifact["filename"] == "report.md"
    assert artifact["media_type"] == "text/markdown"
    assert path.read_text(encoding="utf-8") == "# Report\n\nResult"
    assert path.parent == tmp_path / "artifacts" / task["task_id"]
    assert repository.artifact_usage(task["task_id"]) == {
        "count": 1,
        "total_size_bytes": len("# Report\n\nResult".encode("utf-8")),
    }


def test_writer_serializes_json_and_csv_structurally(tmp_path):
    service, repository, task = _setup(tmp_path)
    tool = ArtifactWriterTool(service)
    json_rows = [{"name": "Tesla", "value": 1}, {"name": "小米", "value": 2}]

    json_result = _run(
        tool,
        {
            "task_id": task["task_id"],
            "filename": "data.json",
            "format": "json",
            "rows": json_rows,
        },
    )
    csv_result = _run(
        tool,
        {
            "task_id": task["task_id"],
            "filename": "data.csv",
            "format": "csv",
            "rows": json_rows,
        },
    )

    json_path = service.resolve_artifact_path(
        repository.get_artifact(task["task_id"], json_result["artifact_id"])
    )
    csv_path = service.resolve_artifact_path(
        repository.get_artifact(task["task_id"], csv_result["artifact_id"])
    )
    assert json.loads(json_path.read_text(encoding="utf-8")) == json_rows
    with csv_path.open(encoding="utf-8", newline="") as handle:
        assert list(csv.DictReader(handle)) == [
            {"name": "Tesla", "value": "1"},
            {"name": "小米", "value": "2"},
        ]


def test_writer_escapes_spreadsheet_formula_cells(tmp_path):
    service, repository, task = _setup(tmp_path)
    tool = ArtifactWriterTool(service)

    result = _run(
        tool,
        {
            "task_id": task["task_id"],
            "filename": "safe.csv",
            "format": "csv",
            "rows": [{"value": "=1+1"}, {"value": "@SUM(A1:A2)"}],
        },
    )
    path = service.resolve_artifact_path(
        repository.get_artifact(task["task_id"], result["artifact_id"])
    )

    with path.open(encoding="utf-8", newline="") as handle:
        assert list(csv.DictReader(handle)) == [
            {"value": "'=1+1"},
            {"value": "'@SUM(A1:A2)"},
        ]


@pytest.mark.parametrize(
    "filename",
    [
        "/tmp/report.md",
        "C:\\temp\\report.md",
        "../report.md",
        "folder/report.md",
        "folder\\report.md",
        ".hidden.md",
        "CON.md",
        "CON.report.md",
        "nul.txt",
        "LPT1.csv",
    ],
)
def test_writer_rejects_unsafe_filenames(tmp_path, filename):
    service, _, task = _setup(tmp_path)

    with pytest.raises(ValueError):
        service.write(task["task_id"], filename, "md", content="unsafe")


def test_writer_never_overwrites_existing_file(tmp_path):
    service, repository, task = _setup(tmp_path)

    first = service.write(task["task_id"], "report.md", "md", content="first")
    second = service.write(task["task_id"], "report.md", "md", content="second")

    assert first["filename"] == "report.md"
    assert second["filename"] == "report-2.md"
    paths = [
        service.resolve_artifact_path(item)
        for item in repository.list_artifacts(task["task_id"])
    ]
    assert [path.read_text(encoding="utf-8") for path in paths] == [
        "first",
        "second",
    ]


def test_writer_enforces_count_limit(tmp_path):
    service, repository, task = _setup(
        tmp_path, artifact_max_files_per_task=2
    )
    service.write(task["task_id"], "one.txt", "txt", content="1")
    service.write(task["task_id"], "two.txt", "txt", content="2")

    with pytest.raises(ValueError, match="limit"):
        service.write(task["task_id"], "three.txt", "txt", content="3")

    assert repository.artifact_usage(task["task_id"])["count"] == 2


def test_writer_enforces_single_file_size_and_removes_partial_output(tmp_path):
    service, repository, task = _setup(tmp_path, artifact_max_file_bytes=4)

    with pytest.raises(ValueError, match="size"):
        service.write(task["task_id"], "large.txt", "txt", content="12345")

    assert repository.list_artifacts(task["task_id"]) == []
    assert list((tmp_path / "artifacts" / task["task_id"]).iterdir()) == []


def test_archiver_contains_only_same_task_registered_non_zip_artifacts(tmp_path):
    service, repository, task = _setup(tmp_path)
    first = service.write(task["task_id"], "report.md", "md", content="report")
    second = service.write(task["task_id"], "data.json", "json", rows={"value": 1})
    archive = ArtifactArchiverTool(service)

    result = _run(
        archive,
        {
            "task_id": task["task_id"],
            "filename": "bundle.zip",
            "artifact_ids": [first["artifact_id"], second["artifact_id"]],
        },
    )
    archive_artifact = repository.get_artifact(
        task["task_id"], result["artifact_id"]
    )
    archive_path = service.resolve_artifact_path(archive_artifact)

    with zipfile.ZipFile(archive_path) as handle:
        assert handle.namelist() == ["report.md", "data.json"]
        assert handle.read("report.md").decode("utf-8") == "report"
    assert archive_artifact["media_type"] == "application/zip"


def test_archiver_rejects_cross_task_artifacts(tmp_path):
    service, repository, first_task = _setup(tmp_path)
    second_task = repository.create_task("second task")
    foreign = service.write(
        second_task["task_id"], "foreign.txt", "txt", content="secret"
    )

    with pytest.raises(ValueError, match="not found"):
        service.archive(
            first_task["task_id"],
            "bundle.zip",
            artifact_ids=[foreign["artifact_id"]],
        )


def test_archiver_rejects_registered_symlink_and_path_escape(tmp_path):
    service, repository, task = _setup(tmp_path)
    task_dir = tmp_path / "artifacts" / task["task_id"]
    task_dir.mkdir(parents=True)
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    link = task_dir / "linked.txt"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("Symbolic links are not available in this environment")
    artifact = repository.register_artifact(
        task_id=task["task_id"],
        filename="linked.txt",
        media_type="text/plain",
        size_bytes=outside.stat().st_size,
        relative_path=f"{task['task_id']}/linked.txt",
    )

    with pytest.raises(ValueError, match="symbolic link"):
        service.archive(
            task["task_id"],
            "bundle.zip",
            artifact_ids=[artifact["artifact_id"]],
        )


def test_resolver_rejects_registered_path_escape(tmp_path):
    service, repository, task = _setup(tmp_path)
    artifact = repository.register_artifact(
        task_id=task["task_id"],
        filename="outside.txt",
        media_type="text/plain",
        size_bytes=1,
        relative_path="../outside.txt",
    )

    with pytest.raises(ValueError, match="registered metadata"):
        service.resolve_artifact_path(artifact)


def test_archiver_enforces_total_input_size(tmp_path):
    service, _, task = _setup(tmp_path, artifact_max_archive_input_bytes=5)
    first = service.write(task["task_id"], "one.txt", "txt", content="123")
    second = service.write(task["task_id"], "two.txt", "txt", content="456")

    with pytest.raises(ValueError, match="archive input"):
        service.archive(
            task["task_id"],
            "bundle.zip",
            artifact_ids=[first["artifact_id"], second["artifact_id"]],
        )
