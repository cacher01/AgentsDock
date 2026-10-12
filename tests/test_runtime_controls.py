import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from app.core.config import Settings
from app.core.enums import TaskStatus
from app.core.errors import TaskCancelledError, TaskTimeoutError
from app.db.database import init_database
from app.db.repositories import Repository
from app.llm.client import LLMResponse, MockLLMClient
from app.llm.task_client import TaskAwareLLMClient


def _repository() -> Repository:
    return Repository(init_database(":memory:"))


def test_task_llm_client_accumulates_response_tokens():
    repo = _repository()
    task = repo.create_task("count tokens")
    client = TaskAwareLLMClient(
        MockLLMClient([LLMResponse(content="ok", token_estimate=17)]),
        repo,
        checkpoint=lambda task_id: None,
    )

    token = client.bind_task(task["task_id"])
    try:
        response = asyncio.run(client.chat([{"role": "user", "content": "x"}]))
    finally:
        client.reset_task(token)

    assert response.token_estimate == 17
    assert repo.get_task(task["task_id"])["token_estimate"] == 17


def test_task_llm_client_collects_gateway_fallback_warnings():
    repo = _repository()
    task = repo.create_task("fallback")
    client = TaskAwareLLMClient(
        MockLLMClient(
            [
                LLMResponse(
                    content="ok",
                    raw={
                        "fallback_from": "model_primary",
                        "warnings": ["Primary model failed; fallback model was used."],
                    },
                )
            ]
        ),
        repo,
        checkpoint=lambda task_id: None,
    )

    token = client.bind_task(task["task_id"])
    try:
        asyncio.run(client.chat([{"role": "user", "content": "x"}]))
    finally:
        client.reset_task(token)

    assert client.consume_warnings(task["task_id"]) == [
        "Primary model failed; fallback model was used."
    ]
    assert client.consume_warnings(task["task_id"]) == []


def test_task_llm_client_keeps_calls_without_bound_task_unaccounted():
    repo = _repository()
    client = TaskAwareLLMClient(
        MockLLMClient([LLMResponse(content="ok", token_estimate=17)]),
        repo,
        checkpoint=lambda task_id: None,
    )

    response = asyncio.run(client.chat([{"role": "user", "content": "x"}]))

    assert response.token_estimate == 17


def test_repository_runtime_checkpoint_rejects_cancelled_task():
    repo = _repository()
    task = repo.create_task("cancelled")
    repo.update_task_status(task["task_id"], TaskStatus.CANCELLED)

    with pytest.raises(TaskCancelledError):
        repo.check_task_runtime(task["task_id"], timeout_seconds=600)


def test_repository_runtime_checkpoint_rejects_timed_out_task():
    repo = _repository()
    task = repo.create_task("timed out")
    old_timestamp = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()
    repo.connection.execute(
        "update tasks set created_at = ? where task_id = ?",
        (old_timestamp, task["task_id"]),
    )
    repo.connection.commit()

    with pytest.raises(TaskTimeoutError):
        repo.check_task_runtime(task["task_id"], timeout_seconds=1)


def test_task_timeout_setting_loads_from_environment(monkeypatch):
    monkeypatch.setenv("TASK_TIMEOUT_SECONDS", "321")

    settings = Settings.from_environment()

    assert settings.task_timeout_seconds == 321
