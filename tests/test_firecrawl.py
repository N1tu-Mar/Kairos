"""Firecrawl's isolated REST and archive boundary. No live network calls."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import httpx

from agent.scraping.fetch import MAX_BYTES, load_archived
from agent.scraping.firecrawl import FirecrawlClient, FirecrawlConfigurationError
from agent.scraping.models import FetchRecord


class FakeHttp:
    """Return canned responses while retaining provider request arguments."""

    def __init__(self, responses: list[httpx.Response]) -> None:
        self.responses = list(responses)
        self.calls: list[dict] = []

    def post(self, url, **kwargs):
        self.calls.append({"url": url, **kwargs})
        return self.responses.pop(0)


def response(status: int, *, payload=None, body: bytes | None = None, headers=None):
    request = httpx.Request("POST", "https://api.firecrawl.dev/v2/scrape")
    if body is not None:
        return httpx.Response(status, request=request, content=body, headers=headers)
    return httpx.Response(status, request=request, json=payload, headers=headers)


def local_record(**overrides) -> FetchRecord:
    values = {
        "url": "https://example.edu/grant",
        "final_url": "https://example.edu/grant",
        "status_code": 200,
        "robots_allowed": True,
        "robots_url": "https://example.edu/robots.txt",
        "crawl_delay_s": 2.0,
        "fetched_at": datetime(2026, 8, 30, tzinfo=timezone.utc),
        "failure": "NEEDS_JS: page renders its content with JavaScript",
    }
    values.update(overrides)
    return FetchRecord(**values)


def test_success_maps_request_and_archives_replayable_markdown(tmp_path):
    markdown = "# Student Grant\n\nAwards up to $5,000."
    raw_html = "<html><body><h1>Student Grant</h1></body></html>"
    http = FakeHttp(
        [
            response(
                200,
                payload={
                    "success": True,
                    "data": {
                        "markdown": markdown,
                        "rawHtml": raw_html,
                        "metadata": {
                            "url": "https://example.edu/grant/current",
                            "statusCode": 200,
                        },
                    },
                },
            )
        ]
    )
    client = FirecrawlClient("fc-secret", tmp_path, http_client=http)

    result = client.scrape(
        "https://example.edu/grant",
        local_record=local_record(),
        fallback_reason="NEEDS_JS",
    )

    assert result.succeeded
    assert result.text == markdown
    assert result.record.renderer == "firecrawl"
    assert result.record.content_format == "markdown"
    assert result.record.fallback_reason == "NEEDS_JS"
    assert result.record.final_url == "https://example.edu/grant/current"
    assert Path(result.record.raw_path).read_text(encoding="utf-8") == markdown
    assert Path(result.record.source_raw_path).read_text(encoding="utf-8") == raw_html

    call = http.calls[0]
    assert call["url"] == "https://api.firecrawl.dev/v2/scrape"
    assert call["headers"]["Authorization"] == "Bearer fc-secret"
    assert call["json"] == {
        "url": "https://example.edu/grant",
        "formats": ["markdown", "rawHtml"],
        "onlyMainContent": True,
        "onlyCleanContent": False,
        "maxAge": 0,
        "skipTlsVerification": False,
        "storeInCache": False,
        "timeout": 60_000,
    }

    meta_path = next(tmp_path.rglob("*.firecrawl.meta.json"))
    replayed, replayed_record = load_archived(meta_path)
    assert replayed == markdown
    assert replayed_record == result.record


def test_retryable_responses_honor_retry_after_then_back_off(tmp_path):
    sleeps: list[float] = []
    http = FakeHttp(
        [
            response(429, payload={"success": False, "error": "slow down"}, headers={"Retry-After": "0.25"}),
            response(503, payload={"success": False, "error": "unavailable"}),
            response(
                200,
                payload={
                    "success": True,
                    "data": {"markdown": "Useful grant content", "rawHtml": "<p>Useful</p>"},
                },
            ),
        ]
    )
    client = FirecrawlClient("fc-secret", tmp_path, http_client=http, sleep=sleeps.append)

    result = client.scrape(
        "https://example.edu/grant",
        local_record=local_record(),
        fallback_reason="NEEDS_JS",
    )

    assert result.succeeded
    assert result.attempts == 3
    assert result.retries == 2
    assert sleeps == [0.25, 2.0]


def test_auth_failure_is_not_retried_and_disables_fallback(tmp_path):
    http = FakeHttp(
        [response(401, payload={"success": False, "error": "Unauthorized: Invalid token"})]
    )
    client = FirecrawlClient("fc-do-not-leak", tmp_path, http_client=http)

    result = client.scrape(
        "https://example.edu/grant",
        local_record=local_record(),
        fallback_reason="NEEDS_JS",
    )

    assert not result.succeeded
    assert result.disable_fallback is True
    assert result.attempts == 1
    assert result.record.failure == "FIRECRAWL_HTTP_401: Unauthorized: Invalid token"
    assert "fc-do-not-leak" not in result.record.failure
    assert len(http.calls) == 1


def test_payment_failure_also_disables_fallback(tmp_path):
    http = FakeHttp(
        [response(402, payload={"success": False, "error": "Insufficient credits"})]
    )
    result = FirecrawlClient("fc-secret", tmp_path, http_client=http).scrape(
        "https://example.edu/grant",
        local_record=local_record(),
        fallback_reason="THIN_CONTENT",
    )

    assert result.disable_fallback is True
    assert result.record.failure.startswith("FIRECRAWL_HTTP_402")


def test_client_refuses_a_local_robots_denial_without_provider_call(tmp_path):
    http = FakeHttp([])
    result = FirecrawlClient("fc-secret", tmp_path, http_client=http).scrape(
        "https://example.edu/grant",
        local_record=local_record(robots_allowed=False),
        fallback_reason="NEEDS_JS",
    )

    assert result.attempts == 0
    assert result.record.failure.startswith("FIRECRAWL_REFUSED")
    assert http.calls == []


def test_malformed_or_empty_success_is_a_recorded_failure(tmp_path):
    malformed = FirecrawlClient(
        "fc-secret",
        tmp_path,
        http_client=FakeHttp([response(200, body=b"not json")]),
    ).scrape(
        "https://example.edu/grant",
        local_record=local_record(),
        fallback_reason="NEEDS_JS",
    )
    empty = FirecrawlClient(
        "fc-secret",
        tmp_path,
        http_client=FakeHttp(
            [response(200, payload={"success": True, "data": {"markdown": ""}})]
        ),
    ).scrape(
        "https://example.edu/grant",
        local_record=local_record(),
        fallback_reason="NEEDS_JS",
    )

    assert malformed.record.failure.startswith("FIRECRAWL_MALFORMED")
    assert empty.record.failure == "FIRECRAWL_EMPTY: response contained no markdown"


def test_oversized_provider_response_is_not_archived(tmp_path):
    result = FirecrawlClient(
        "fc-secret",
        tmp_path,
        http_client=FakeHttp([response(200, body=b"x" * (MAX_BYTES + 1))]),
    ).scrape(
        "https://example.edu/grant",
        local_record=local_record(),
        fallback_reason="NEEDS_JS",
    )

    assert result.record.failure == f"FIRECRAWL_OVERSIZED: {MAX_BYTES + 1} bytes"
    assert list(tmp_path.rglob("*.firecrawl.md")) == []


def test_from_env_validates_key_and_timeout(monkeypatch, tmp_path):
    monkeypatch.delenv("FIRECRAWL_API_KEY", raising=False)
    try:
        FirecrawlClient.from_env(tmp_path)
    except FirecrawlConfigurationError as exc:
        assert str(exc) == "FIRECRAWL_API_KEY is not set."
    else:  # pragma: no cover - assertion branch
        raise AssertionError("missing Firecrawl key should fail")

    monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-test")
    monkeypatch.setenv("FIRECRAWL_TIMEOUT_S", "not-a-number")
    try:
        FirecrawlClient.from_env(tmp_path)
    except FirecrawlConfigurationError as exc:
        assert str(exc) == "FIRECRAWL_TIMEOUT_S must be a number."
    else:  # pragma: no cover - assertion branch
        raise AssertionError("invalid Firecrawl timeout should fail")
