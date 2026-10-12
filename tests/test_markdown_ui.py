from pathlib import Path


def test_ui_markdown_renderer_supports_tables_and_ordered_lists():
    script = Path("app/static/app.js").read_text(encoding="utf-8")

    assert "renderMarkdownTable" in script
    assert "<table>" in script
    assert "<ol>" in script


def test_ui_loads_task_artifact_download_links():
    script = Path("app/static/app.js").read_text(encoding="utf-8")
    markup = Path("app/static/index.html").read_text(encoding="utf-8")

    assert "fetchArtifacts" in script
    assert "/artifacts" in script
    assert 'id="artifacts"' in markup


def test_ui_surfaces_failed_or_interrupted_tasks():
    script = Path("app/static/app.js").read_text(encoding="utf-8")

    assert "refreshTaskOutcome" in script
    assert "showTaskFailure" in script
    assert "任务在生成结果前失败。" in script
