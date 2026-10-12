import os
from dataclasses import dataclass
from pathlib import Path


def _bool_env(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _int_env(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    return int(value)


def _env(name: str, default: str = "", aliases: tuple[str, ...] = ()) -> str:
    for key in (name, *aliases):
        value = os.getenv(key)
        if value is not None and value != "":
            return value
    return default


def load_env_file(path: str | Path = ".env") -> None:
    env_path = Path(path)
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


@dataclass
class Settings:
    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = ""
    llm_temperature: float = 0.2
    llm_max_tokens: int = 8192
    request_timeout_seconds: int = 120
    task_timeout_seconds: int = 600
    tavily_api_key: str = ""
    weather_api_key: str = ""
    weather_base_url: str = "https://api.weatherapi.com/v1"
    weather_provider: str = "weatherapi"
    database_url: str = "sqlite:///./orchestration.db"
    platform_repository_backend: str = "disabled"
    platform_database_url: str = ""
    artifact_root: str = "artifacts"
    artifact_max_files_per_task: int = 10
    artifact_max_file_bytes: int = 1024 * 1024
    artifact_max_archive_input_bytes: int = 5 * 1024 * 1024
    input_file_root: str = "input_files"
    input_file_max_bytes: int = 20 * 1024 * 1024
    document_context_max_chars: int = 50000
    max_swarm_rounds: int = 2
    hard_max_swarm_rounds: int = 3
    max_agents: int = 4
    hard_max_agents: int = 6
    max_concurrent_agents: int = 2
    hard_max_concurrent_agents: int = 3
    max_tool_calls: int = 12
    react_max_tool_calls: int = 5
    max_fetch_chars: int = 12000
    search_results_limit: int = 5
    research_max_queries: int = 5
    research_auto_fetch_pages: int = 3
    research_supplemental_searches: int = 1
    evidence_per_subtask: int = 3
    fetch_top_results: int = 2
    llm_retries: int = 2
    model_gateway_enabled: bool = False
    model_gateway_url: str = "http://127.0.0.1:8080"
    model_gateway_internal_token: str = ""
    model_gateway_workspace_id: str = ""
    model_gateway_variant_id: str = ""
    tavily_retries: int = 1
    fetch_retries: int = 1

    @classmethod
    def from_env_file(cls, path: str | Path = ".env") -> "Settings":
        load_env_file(path)
        return cls.from_environment()

    @classmethod
    def from_environment(cls) -> "Settings":
        return cls(
            llm_base_url=_env(
                "LLM_BASE_URL",
                "https://api.deepseek.com",
                aliases=("DEEPSEEK_API_BASE_URL",),
            ),
            llm_api_key=_env("LLM_API_KEY", aliases=("DEEPSEEK_API_KEY",)),
            llm_model=_env("LLM_MODEL", "deepseek-chat", aliases=("MODEL_NAME",)),
            llm_temperature=float(_env("LLM_TEMPERATURE", "0.2")),
            llm_max_tokens=_int_env("LLM_MAX_TOKENS", 8192),
            request_timeout_seconds=_int_env("REQUEST_TIMEOUT_SECONDS", 120),
            task_timeout_seconds=_int_env("TASK_TIMEOUT_SECONDS", 600),
            tavily_api_key=_env("TAVILY_API_KEY"),
            weather_api_key=_env("WEATHER_API_KEY"),
            weather_base_url=_env("WEATHER_BASE_URL", "https://api.weatherapi.com/v1").rstrip("/"),
            weather_provider=_env("WEATHER_PROVIDER", "weatherapi"),
            database_url=_env("DATABASE_URL", "sqlite:///./orchestration.db"),
            platform_repository_backend=_env("PLATFORM_REPOSITORY_BACKEND", "disabled").lower(),
            platform_database_url=_env("PLATFORM_DATABASE_URL"),
            artifact_root=_env("ARTIFACT_ROOT", "artifacts"),
            artifact_max_files_per_task=_int_env("ARTIFACT_MAX_FILES_PER_TASK", 10),
            artifact_max_file_bytes=_int_env("ARTIFACT_MAX_FILE_BYTES", 1024 * 1024),
            artifact_max_archive_input_bytes=_int_env(
                "ARTIFACT_MAX_ARCHIVE_INPUT_BYTES", 5 * 1024 * 1024
            ),
            input_file_root=_env("INPUT_FILE_ROOT", "input_files"),
            input_file_max_bytes=_int_env("INPUT_FILE_MAX_BYTES", 20 * 1024 * 1024),
            document_context_max_chars=_int_env("DOCUMENT_CONTEXT_MAX_CHARS", 50000),
            max_swarm_rounds=_int_env("MAX_SWARM_ROUNDS", 2),
            hard_max_swarm_rounds=_int_env("HARD_MAX_SWARM_ROUNDS", 3),
            max_agents=_int_env("MAX_AGENTS", 4),
            hard_max_agents=_int_env("HARD_MAX_AGENTS", 6),
            max_concurrent_agents=_int_env("MAX_CONCURRENT_AGENTS", 2),
            hard_max_concurrent_agents=_int_env("HARD_MAX_CONCURRENT_AGENTS", 3),
            max_tool_calls=_int_env("MAX_TOOL_CALLS", 12),
            react_max_tool_calls=_int_env("REACT_MAX_TOOL_CALLS", 5),
            max_fetch_chars=_int_env("MAX_FETCH_CHARS", 12000),
            search_results_limit=_int_env("SEARCH_RESULTS_LIMIT", 5),
            research_max_queries=_int_env("RESEARCH_MAX_QUERIES", 5),
            research_auto_fetch_pages=_int_env("RESEARCH_AUTO_FETCH_PAGES", 3),
            research_supplemental_searches=_int_env("RESEARCH_SUPPLEMENTAL_SEARCHES", 1),
            evidence_per_subtask=_int_env("EVIDENCE_PER_SUBTASK", 3),
            fetch_top_results=_int_env("FETCH_TOP_RESULTS", 2),
            llm_retries=_int_env("LLM_RETRIES", 2),
            model_gateway_enabled=_bool_env("MODEL_GATEWAY_ENABLED"),
            model_gateway_url=_env("MODEL_GATEWAY_URL", "http://127.0.0.1:8080"),
            model_gateway_internal_token=_env("INTERNAL_API_TOKEN"),
            model_gateway_workspace_id=_env("MODEL_GATEWAY_WORKSPACE_ID"),
            model_gateway_variant_id=_env("MODEL_GATEWAY_VARIANT_ID"),
            tavily_retries=_int_env("TAVILY_RETRIES", 1),
            fetch_retries=_int_env("FETCH_RETRIES", 1),
        )

    def __post_init__(self) -> None:
        if self.model_gateway_enabled and not self.model_gateway_internal_token:
            raise ValueError("model gateway requires INTERNAL_API_TOKEN")
        if self.platform_repository_backend not in {"disabled", "postgres"}:
            raise ValueError("unsupported platform repository backend")
        if self.platform_repository_backend == "postgres" and not self.platform_database_url:
            raise ValueError("PLATFORM_DATABASE_URL is required for postgres backend")
        if self.input_file_max_bytes < 1:
            raise ValueError("INPUT_FILE_MAX_BYTES must be a positive integer")
        if self.document_context_max_chars < 1:
            raise ValueError("DOCUMENT_CONTEXT_MAX_CHARS must be a positive integer")
        pairs = [
            ("max_swarm_rounds", self.max_swarm_rounds, self.hard_max_swarm_rounds),
            ("max_agents", self.max_agents, self.hard_max_agents),
            (
                "max_concurrent_agents",
                self.max_concurrent_agents,
                self.hard_max_concurrent_agents,
            ),
        ]
        for name, value, hard_value in pairs:
            if value > hard_value:
                raise ValueError(f"{name} cannot exceed hard limit {hard_value}")
