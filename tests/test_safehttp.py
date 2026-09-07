"""The common scraper transport validates destinations and bounds downloads."""

from __future__ import annotations

import httpx
import pytest

from agent.scraping.netguard import BlockedAddress
from agent.scraping.safehttp import ResponseTooLarge, guarded_get


class ChunkedBody(httpx.SyncByteStream):
    def __init__(self, chunks: list[bytes]) -> None:
        self.chunks = chunks

    def __iter__(self):
        yield from self.chunks


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_rejects_embedded_credentials_before_request():
    calls = []
    with _client(lambda request: calls.append(request)) as client:
        with pytest.raises(BlockedAddress, match="credentials"):
            guarded_get("https://user:password@93.184.216.34/", client=client)
    assert calls == []


@pytest.mark.parametrize(
    "destination",
    [
        "http://127.0.0.1/private",
        "http://10.0.0.1/private",
        "http://169.254.169.254/latest/meta-data/",
        "http://[::1]/private",
    ],
)
def test_rejects_private_redirect_before_destination_request(destination):
    calls: list[str] = []

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(302, headers={"location": destination})

    with _client(handler) as client:
        with pytest.raises(BlockedAddress):
            guarded_get("https://93.184.216.34/start", client=client)

    assert calls == ["https://93.184.216.34/start"]


def test_follows_bounded_public_redirects():
    calls: list[str] = []

    def handler(request):
        calls.append(str(request.url))
        if request.url.path == "/start":
            return httpx.Response(302, headers={"location": "/final"})
        return httpx.Response(200, content=b"safe")

    with _client(handler) as client:
        response = guarded_get("https://93.184.216.34/start", client=client)

    assert response.content == b"safe"
    assert calls == ["https://93.184.216.34/start", "https://93.184.216.34/final"]


def test_stops_redirect_loops_at_configured_limit():
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        return httpx.Response(302, headers={"location": "/loop"})

    with _client(handler) as client:
        with pytest.raises(httpx.TooManyRedirects):
            guarded_get(
                "https://93.184.216.34/loop", client=client, max_redirects=2
            )
    assert calls == 3


def test_rejects_oversized_declared_body_before_streaming():
    body = ChunkedBody([b"never-read"])
    with _client(
        lambda request: httpx.Response(
            200, headers={"content-length": "11"}, stream=body
        )
    ) as client:
        with pytest.raises(ResponseTooLarge):
            guarded_get("https://93.184.216.34/file", client=client, max_bytes=10)


def test_rejects_chunked_body_as_soon_as_limit_is_crossed():
    body = ChunkedBody([b"12345", b"67890", b"x", b"never-read"])
    with _client(lambda request: httpx.Response(200, stream=body)) as client:
        with pytest.raises(ResponseTooLarge):
            guarded_get("https://93.184.216.34/file", client=client, max_bytes=10)
