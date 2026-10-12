import asyncio
import ipaddress
import socket
from urllib.parse import urljoin, urlparse

import httpx

from app.core.config import Settings

_REDIRECT_STATUSES = {301, 302, 303, 307, 308}
_MAX_REDIRECTS = 3


def is_public_http_url(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return False
    if parsed.username is not None or parsed.password is not None:
        return False
    host = parsed.hostname
    if not host:
        return False
    if host.lower() == "localhost":
        return False
    try:
        parsed.port
    except ValueError:
        return False
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return True
    return ip.is_global


async def _validate_public_url(url: str) -> None:
    if not is_public_http_url(url):
        raise ValueError("Only public HTTP/HTTPS URLs are allowed")

    parsed = urlparse(url)
    host = parsed.hostname
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        addresses = await asyncio.to_thread(
            socket.getaddrinfo,
            host,
            port,
            type=socket.SOCK_STREAM,
        )
    except OSError as exc:
        raise ValueError("URL hostname could not be resolved") from exc

    if not addresses:
        raise ValueError("URL hostname could not be resolved")
    for address in addresses:
        try:
            resolved_ip = ipaddress.ip_address(address[4][0])
        except ValueError as exc:
            raise ValueError("URL resolved to an invalid IP address") from exc
        if not resolved_ip.is_global:
            raise ValueError("Only public HTTP/HTTPS URLs are allowed")


def _is_text_content_type(content_type: str) -> bool:
    media_type = content_type.split(";", 1)[0].strip().lower()
    return (
        media_type.startswith("text/")
        or media_type
        in {
            "application/json",
            "application/javascript",
            "application/xml",
            "application/xhtml+xml",
        }
        or media_type.endswith("+json")
        or media_type.endswith("+xml")
    )


class WebFetchTool:
    name = "web_fetch"
    description = "Fetch a public HTTP/HTTPS page and return truncated text."

    def __init__(self, settings: Settings):
        self.settings = settings

    async def run(self, arguments: dict) -> dict:
        current_url = str(arguments["url"])
        async with httpx.AsyncClient(
            timeout=self.settings.request_timeout_seconds,
            follow_redirects=False,
        ) as client:
            for redirect_count in range(_MAX_REDIRECTS + 1):
                await _validate_public_url(current_url)
                response = await client.get(current_url, follow_redirects=False)

                if response.status_code in _REDIRECT_STATUSES:
                    if redirect_count == _MAX_REDIRECTS:
                        raise ValueError("Too many redirects")
                    location = response.headers.get("location")
                    if not location:
                        raise ValueError("Redirect response is missing a location")
                    current_url = urljoin(current_url, location)
                    continue

                response.raise_for_status()
                break

        content_type = response.headers.get("content-type", "")
        if not _is_text_content_type(content_type):
            raise ValueError("Unsupported content type")

        content_length = response.headers.get("content-length")
        if content_length:
            try:
                if int(content_length) > self.settings.max_fetch_chars:
                    raise ValueError("Response exceeds size limit")
            except ValueError as exc:
                if str(exc) == "Response exceeds size limit":
                    raise

        if len(response.content) > self.settings.max_fetch_chars:
            raise ValueError("Response exceeds size limit")

        text = response.text
        return {"url": current_url, "text": text, "summary": text[:500]}
