"""Firecrawl's isolated REST and archive boundary. No live network calls."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import httpx

from agent.scraping.fetch import MAX_BYTES, load_archived
from agent.scraping.firecrawl import (
    FirecrawlClient,
    FirecrawlConfigurationError,
    FirecrawlFallbackFetcher,
    FirecrawlResult,
)
from agent.scraping.models import FetchRecord, content_hash
from agent.scraping.pipeline import build_record
from agent.scraping.registry import Target


class FakeHttp:
    """Return canned responses while retaining provider request arguments."""

    def __init__(self, responses: list[httpx.Response]) -> None:
        self.responses = list(responses)
        self.calls: list[dict] = []

    def post(self, url, **kwargs):
        self.calls.append({"url": url, **kwargs})
        return self.responses.pop(0)


class FakeLocalFetcher:
    """Return one local outcome and record whether browser rendering was requested."""

    def __init__(self, text: str, record: FetchRecord) -> None:
        self.text = text
        self.record = record
        self.calls: list[tuple[str, bool]] = []

    def fetch(self, url: str, *, allow_js: bool = False):
        self.calls.append((url, allow_js))
        return self.text, self.record.model_copy(deep=True)


class FakeFirecrawlClient:
    """Return queued provider outcomes and retain fallback reasons."""

    def __init__(self, results: list[FirecrawlResult]) -> None:
        self.results = list(results)
        self.calls: list[tuple[str, str]] = []

    def scrape(self, url: str, *, local_record: FetchRecord, fallback_reason: str):
        self.calls.append((url, fallback_reason))
        return self.results.pop(0)


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


def firecrawl_result(
    *,
    text: str = "# Grant\n\nAwards up to $5,000 for student founders.",
    failure: str | None = None,
    attempts: int = 1,
    disable: bool = False,
) -> FirecrawlResult:
    record = FetchRecord(
        url="https://example.edu/grant",
        final_url="https://example.edu/grant",
        status_code=200,
        renderer="firecrawl",
        content_format="markdown",
        fallback_reason="NEEDS_JS",
        content_hash=content_hash(text) if text else "",
        failure=failure,
    )
    return FirecrawlResult(
        text=text if failure is None else "",
        record=record,
        attempts=attempts,
        retries=max(0, attempts - 1),
        disable_fallback=disable,
    )


def test_success_maps_request_and_archives_replayable_markdown(tmp_path):
    markdown = "# Student Grant\r\n\r\nAwards up to $5,000."
    raw_html = "<html>\r\n<body><h1>Student Grant</h1></body>\r\n</html>"
    provider_metadata = {
        "url": "https://example.edu/grant/current",
        "statusCode": 200,
        "title": "Student Grant",
    }
    http = FakeHttp(
        [
            response(
                200,
                payload={
                    "success": True,
                    "data": {
                        "markdown": markdown,
                        "rawHtml": raw_html,
                        "metadata": provider_metadata,
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
    assert Path(result.record.raw_path).read_bytes() == markdown.encode("utf-8")
    assert Path(result.record.source_raw_path).read_bytes() == raw_html.encode("utf-8")

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
    archived_metadata = json.loads(meta_path.read_bytes().decode("utf-8"))
    assert archived_metadata["provider_metadata"] == provider_metadata
    assert archived_metadata["local_fetch"]["failure"].startswith("NEEDS_JS")
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


def test_missing_raw_html_is_a_recorded_failure(tmp_path):
    result = FirecrawlClient(
        "fc-secret",
        tmp_path,
        http_client=FakeHttp(
            [response(200, payload={"success": True, "data": {"markdown": "Grant"}})]
        ),
    ).scrape(
        "https://example.edu/grant",
        local_record=local_record(),
        fallback_reason="NEEDS_JS",
    )

    assert result.record.failure == "FIRECRAWL_MALFORMED: response contained no raw HTML"
    assert list(tmp_path.rglob("*.firecrawl.md")) == []


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


def test_fallback_uses_firecrawl_for_javascript_shells():
    local = FakeLocalFetcher("", local_record())
    firecrawl = FakeFirecrawlClient([firecrawl_result()])
    fetcher = FirecrawlFallbackFetcher(local, firecrawl)

    text, record = fetcher.fetch("https://example.edu/grant", allow_js=True)

    assert text.startswith("# Grant")
    assert record.renderer == "firecrawl"
    assert local.calls == [("https://example.edu/grant", False)]
    assert firecrawl.calls == [("https://example.edu/grant", "NEEDS_JS")]


def test_fallback_uses_firecrawl_for_thin_http_200_content():
    local = FakeLocalFetcher(
        "Short grant page",
        local_record(failure=None, status_code=200),
    )
    firecrawl = FakeFirecrawlClient([firecrawl_result()])
    fetcher = FirecrawlFallbackFetcher(local, firecrawl)

    _, record = fetcher.fetch("https://example.edu/grant")

    assert record.renderer == "firecrawl"
    assert firecrawl.calls == [("https://example.edu/grant", "THIN_CONTENT")]


def test_fallback_does_not_call_firecrawl_for_usable_local_content():
    local = FakeLocalFetcher(
        "Useful opportunity content. " * 30,
        local_record(failure=None, status_code=200),
    )
    firecrawl = FakeFirecrawlClient([])
    fetcher = FirecrawlFallbackFetcher(local, firecrawl)

    text, record = fetcher.fetch("https://example.edu/grant")

    assert text.startswith("Useful opportunity")
    assert record.renderer == "httpx"
    assert firecrawl.calls == []


def test_fallback_never_bypasses_local_safety_or_access_failures():
    failures = [
        "ROBOTS_DISALLOWED: disallow",
        "BLOCKED_ADDRESS: non-public target",
        "HTTP_401",
        "HTTP_403",
        "OVERSIZED: 5000000 bytes",
        "FETCH_ERROR: ConnectError",
    ]
    for failure in failures:
        local = FakeLocalFetcher("", local_record(failure=failure))
        firecrawl = FakeFirecrawlClient([])

        text, record = FirecrawlFallbackFetcher(local, firecrawl).fetch(
            "https://example.edu/grant"
        )

        assert text == ""
        assert record.failure == failure
        assert firecrawl.calls == []


def test_fallback_cap_is_shared_by_every_fetch_on_the_wrapper():
    local = FakeLocalFetcher("", local_record())
    firecrawl = FakeFirecrawlClient([firecrawl_result()])
    fetcher = FirecrawlFallbackFetcher(local, firecrawl, max_pages=1)

    first_text, _ = fetcher.fetch("https://example.edu/grant")
    second_text, second_record = fetcher.fetch("https://example.edu/grant-2")

    assert first_text
    assert second_text == ""
    assert second_record.failure.startswith("NEEDS_JS")
    assert len(firecrawl.calls) == 1
    assert fetcher.stats.capped == 1


def test_auth_failure_disables_later_fallback_calls():
    local = FakeLocalFetcher("", local_record())
    firecrawl = FakeFirecrawlClient(
        [firecrawl_result(failure="FIRECRAWL_HTTP_401: invalid token", disable=True)]
    )
    fetcher = FirecrawlFallbackFetcher(local, firecrawl)

    _, first_record = fetcher.fetch("https://example.edu/grant")
    _, second_record = fetcher.fetch("https://example.edu/grant-2")

    assert first_record.failure.startswith("FIRECRAWL_HTTP_401")
    assert second_record.failure.startswith("NEEDS_JS")
    assert len(firecrawl.calls) == 1
    assert fetcher.stats.disabled == 1


def test_failed_fallback_preserves_thin_but_usable_local_text():
    local = FakeLocalFetcher(
        "Short but real grant page",
        local_record(failure=None, status_code=200),
    )
    firecrawl = FakeFirecrawlClient(
        [firecrawl_result(failure="FIRECRAWL_HTTP_503: unavailable", attempts=3)]
    )
    fetcher = FirecrawlFallbackFetcher(local, firecrawl)

    text, record = fetcher.fetch("https://example.edu/grant")

    assert text == "Short but real grant page"
    assert record.renderer == "httpx"
    assert fetcher.stats.provider_requests == 3
    assert fetcher.stats.retries == 2
    assert fetcher.stats.failed == 1


def test_firecrawl_candidate_remains_review_only_and_names_its_provenance():
    provider = firecrawl_result()
    target = Target(
        key="web_grant",
        title="Student Grant",
        organization="Example University",
        url="https://example.edu/grant",
        tier="UNIVERSITY_WEB_SEARCH",
    )

    candidate = build_record(target, provider.text, provider.record)

    assert candidate.review_status == "NEEDS_HUMAN_REVIEW"
    assert any("[firecrawl fallback]" in caveat for caveat in candidate.caveats)
