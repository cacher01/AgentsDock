from app.core.config import Settings
from app.db.database import init_database
from app.db.repositories import Repository
from app.llm.client import MockLLMClient
from app.runtime.legacy_factory import build_legacy_orchestrator


def test_legacy_runtime_tool_registry_respects_platform_allowlist(tmp_path):
    connection = init_database(":memory:")
    try:
        orchestrator = build_legacy_orchestrator(
            Settings(artifact_root=str(tmp_path)),
            Repository(connection),
            llm=MockLLMClient([]),
            allowed_tools={"calculator", "artifact_writer"},
        )
        assert set(orchestrator.tool_executor.registry.list_names()) == {
            "calculator",
            "artifact_writer",
        }
    finally:
        connection.close()


def test_legacy_runtime_keeps_all_compatible_tools_without_allowlist(tmp_path):
    connection = init_database(":memory:")
    try:
        orchestrator = build_legacy_orchestrator(
            Settings(artifact_root=str(tmp_path)),
            Repository(connection),
            llm=MockLLMClient([]),
        )
        names = set(orchestrator.tool_executor.registry.list_names())
        assert {"calculator", "current_time", "artifact_writer"} <= names
    finally:
        connection.close()
