import asyncio
import socket

import pytest

from app.core.config import Settings
from app.tools.builtin.calculator import CalculatorTool
from app.tools.builtin.web_fetch import WebFetchTool, is_public_http_url


class FakeResponse:
    def __init__(
        self,
        status_code: int = 200,
        *,
        content: bytes = b"ok",
        headers: dict[str, str] | None = None,
    ):
        self.status_code = status_code
        self.content = content
        self.headers = headers or {"content-type": "text/plain; charset=utf-8"}

    @property
    def text(self) -> str:
        return self.content.decode("utf-8")

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeAsyncClient:
    responses: list[FakeResponse] = []
    requested_urls: list[str] = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return None

    async def get(self, url: str, **kwargs):
        self.requested_urls.append(url)
        return self.responses.pop(0)


def install_public_dns(monkeypatch) -> None:
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))
        ],
    )


def install_http(monkeypatch, responses: list[FakeResponse]) -> None:
    FakeAsyncClient.responses = list(responses)
    FakeAsyncClient.requested_urls = []
    monkeypatch.setattr(
        "app.tools.builtin.web_fetch.httpx.AsyncClient", FakeAsyncClient
    )


def run_fetch(url: str, *, max_chars: int = 100) -> dict:
    tool = WebFetchTool(Settings(max_fetch_chars=max_chars))
    return asyncio.run(tool.run({"url": url}))


def run_calculator(expression: str) -> float:
    return asyncio.run(CalculatorTool().run({"expression": expression}))["result"]


def test_url_validation_rejects_embedded_credentials():
    assert not is_public_http_url("https://user:secret@example.com/report")


def test_web_fetch_rejects_hostname_resolving_to_non_global_ip(monkeypatch):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.8", 443))
        ],
    )
    install_http(monkeypatch, [FakeResponse()])

    with pytest.raises(ValueError, match="public"):
        run_fetch("https://example.com/report")

    assert FakeAsyncClient.requested_urls == []


def test_web_fetch_validates_every_redirect_target(monkeypatch):
    install_public_dns(monkeypatch)
    install_http(
        monkeypatch,
        [
            FakeResponse(
                302,
                headers={
                    "content-type": "text/plain",
                    "location": "http://127.0.0.1/private",
                },
            )
        ],
    )

    with pytest.raises(ValueError, match="public"):
        run_fetch("https://example.com/start")

    assert FakeAsyncClient.requested_urls == ["https://example.com/start"]


def test_web_fetch_allows_at_most_three_redirects(monkeypatch):
    install_public_dns(monkeypatch)
    install_http(
        monkeypatch,
        [
            FakeResponse(302, headers={"location": "https://example.com/one"}),
            FakeResponse(302, headers={"location": "https://example.com/two"}),
            FakeResponse(302, headers={"location": "https://example.com/three"}),
            FakeResponse(302, headers={"location": "https://example.com/four"}),
        ],
    )

    with pytest.raises(ValueError, match="redirect"):
        run_fetch("https://example.com/start")

    assert len(FakeAsyncClient.requested_urls) == 4


def test_web_fetch_rejects_non_text_content_type(monkeypatch):
    install_public_dns(monkeypatch)
    install_http(
        monkeypatch,
        [FakeResponse(content=b"\x89PNG", headers={"content-type": "image/png"})],
    )

    with pytest.raises(ValueError, match="content type"):
        run_fetch("https://example.com/image")


def test_web_fetch_rejects_body_over_size_limit(monkeypatch):
    install_public_dns(monkeypatch)
    install_http(
        monkeypatch,
        [
            FakeResponse(
                content=b"12345678901",
                headers={"content-type": "text/plain"},
            )
        ],
    )

    with pytest.raises(ValueError, match="size"):
        run_fetch("https://example.com/large", max_chars=10)


@pytest.mark.parametrize(
    "expression",
    [
        "1" * 201,
        "+".join(["1"] * 51),
        "-" * 21 + "1",
        "2 ** 101",
        "2 ** -101",
        "1e309",
        "1e100 * 10",
    ],
)
def test_calculator_rejects_unsafe_or_unbounded_expressions(expression):
    with pytest.raises(ValueError):
        run_calculator(expression)


def test_calculator_accepts_bounded_finite_arithmetic():
    assert run_calculator("(2 + 3) * 4 ** 2") == 80.0
