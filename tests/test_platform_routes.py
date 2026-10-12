import asyncio

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request

from app.api.platform_routes import _read_limited_body, register_platform_routes
from app.core.config import Settings
from app.persistence.provider import create_platform_repository_provider


def test_platform_routes_reject_untrusted_headers() -> None:
    settings = Settings(model_gateway_internal_token="trusted")
    app = FastAPI()
    register_platform_routes(
        app,
        provider=create_platform_repository_provider(settings),
        settings=settings,
    )
    client = TestClient(app)

    response = client.get(
        "/internal/v1/platform/tasks",
        headers={
            "X-Internal-Token": "wrong",
            "X-Request-ID": "request_1",
            "X-User-ID": "user_1",
            "X-Workspace-ID": "workspace_1",
            "X-Workspace-Role": "member",
        },
    )
    assert response.status_code == 401


def test_platform_routes_fail_closed_when_repository_is_disabled() -> None:
    settings = Settings(model_gateway_internal_token="trusted")
    app = FastAPI()
    register_platform_routes(
        app,
        provider=create_platform_repository_provider(settings),
        settings=settings,
    )
    client = TestClient(app)

    response = client.get(
        "/internal/v1/platform/tasks",
        headers={
            "X-Internal-Token": "trusted",
            "X-Request-ID": "request_1",
            "X-User-ID": "user_1",
            "X-Workspace-ID": "workspace_1",
            "X-Workspace-Role": "member",
        },
    )
    assert response.status_code == 503


def _streaming_request(chunks: list[bytes], content_length: str | None = None) -> Request:
    messages = [
        {
            "type": "http.request",
            "body": chunk,
            "more_body": index < len(chunks) - 1,
        }
        for index, chunk in enumerate(chunks)
    ]
    calls = 0

    async def receive():
        nonlocal calls
        calls += 1
        if not messages:
            return {"type": "http.request", "body": b"", "more_body": False}
        return messages.pop(0)

    headers = []
    if content_length is not None:
        headers.append((b"content-length", content_length.encode("ascii")))
    request = Request({"type": "http", "method": "POST", "path": "/", "headers": headers}, receive)
    request.state.receive_calls = lambda: calls
    return request


@pytest.mark.parametrize("size", [3, 4])
def test_limited_body_accepts_payload_at_or_below_limit(size: int) -> None:
    request = _streaming_request([b"x" * size])

    assert asyncio.run(_read_limited_body(request, 4)) == b"x" * size


def test_limited_body_rejects_declared_oversize_without_reading_stream() -> None:
    request = _streaming_request([b"x"], content_length="5")

    with pytest.raises(HTTPException) as exc:
        asyncio.run(_read_limited_body(request, 4))

    assert exc.value.status_code == 413
    assert request.state.receive_calls() == 0


def test_limited_body_rejects_chunked_oversize_despite_forged_length() -> None:
    request = _streaming_request([b"123", b"45"], content_length="1")

    with pytest.raises(HTTPException) as exc:
        asyncio.run(_read_limited_body(request, 4))

    assert exc.value.status_code == 413


def test_limited_body_rejects_invalid_content_length() -> None:
    request = _streaming_request([b"x"], content_length="invalid")

    with pytest.raises(HTTPException) as exc:
        asyncio.run(_read_limited_body(request, 4))

    assert exc.value.status_code == 400