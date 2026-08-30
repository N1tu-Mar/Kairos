"""Firecrawl page extraction behind Kairos' evidence-first fetch contract.

This module does not search or crawl sites. It submits one already-approved
public URL to Firecrawl's scrape endpoint, archives the exact markdown handed
to extraction plus the returned raw HTML, and reports every failure as data.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit

import httpx

from agent.scraping.fetch import MAX_BYTES, MIN_USEFUL_TEXT, PageFetcher, _slug
from agent.scraping.models import FetchRecord, content_hash

FIRECRAWL_BASE_URL = "https://api.firecrawl.dev/v2"
DEFAULT_FIRECRAWL_TIMEOUT_S = 60.0
DEFAULT_FIRECRAWL_ATTEMPTS = 3
_RETRY_AFTER_CEILING_S = 60.0


class FirecrawlConfigurationError(RuntimeError):
    """Firecrawl was explicitly requested without usable configuration."""


@dataclass(frozen=True)
class FirecrawlResult:
    """One provider outcome plus the request accounting needed by a fallback."""

    text: str
    record: FetchRecord
    attempts: int
    retries: int
    disable_fallback: bool = False

    @property
    def succeeded(self) -> bool:
        return bool(self.text) and self.record.failure is None


@dataclass
class FirecrawlFallbackStats:
    """Paid-provider accounting for one CLI invocation."""

    pages_attempted: int = 0
    provider_requests: int = 0
    retries: int = 0
    succeeded: int = 0
    failed: int = 0
    capped: int = 0
    disabled: int = 0


class FirecrawlFallbackFetcher:
    """Try a local fetch first and spend Firecrawl credits only when justified."""

    def __init__(
        self,
        local: PageFetcher,
        firecrawl: "FirecrawlClient",
        *,
        max_pages: int = 5,
    ) -> None:
        if max_pages <= 0:
            raise ValueError("max_pages must be greater than zero")
        self.local = local
        self.firecrawl = firecrawl
        self.max_pages = max_pages
        self.stats = FirecrawlFallbackStats()
        self.disabled_reason = ""

    def fetch(self, url: str, *, allow_js: bool = False) -> tuple[str, FetchRecord]:
        """Return local content unless the narrow fallback policy permits a call."""
        # Firecrawl and local Playwright are intentionally separate operator
        # choices. The CLI rejects enabling both, and this wrapper always makes
        # its first attempt a plain HTTP fetch.
        local_text, local_record = self.local.fetch(url, allow_js=False)
        reason = self._fallback_reason(local_text, local_record)
        if reason is None:
            return local_text, local_record

        if self.disabled_reason:
            self.stats.disabled += 1
            return local_text, local_record
        if self.stats.pages_attempted >= self.max_pages:
            self.stats.capped += 1
            return local_text, local_record

        self.stats.pages_attempted += 1
        result = self.firecrawl.scrape(
            url,
            local_record=local_record,
            fallback_reason=reason,
        )
        self.stats.provider_requests += result.attempts
        self.stats.retries += result.retries
        if result.succeeded:
            self.stats.succeeded += 1
            return result.text, result.record

        self.stats.failed += 1
        if result.disable_fallback:
            self.disabled_reason = result.record.failure or "provider disabled"

        # Thin local text is still evidence. A provider outage must not turn a
        # usable local page into a failed target merely because enrichment was
        # unavailable.
        if local_text and local_record.failure is None:
            return local_text, local_record
        return "", result.record

    def run_notes(self) -> list[str]:
        """A secret-free cumulative cost and outcome line for the scrape log."""
        note = (
            "Firecrawl fallback invocation totals: "
            f"pages attempted {self.stats.pages_attempted}; "
            f"provider requests {self.stats.provider_requests}; "
            f"retries {self.stats.retries}; succeeded {self.stats.succeeded}; "
            f"failed {self.stats.failed}; capped {self.stats.capped}; "
            f"disabled skips {self.stats.disabled}."
        )
        return [note]

    @staticmethod
    def _fallback_reason(text: str, record: FetchRecord) -> str | None:
        failure = record.failure or ""
        if failure.startswith("NEEDS_JS"):
            return "NEEDS_JS"
        if record.failure is None and record.status_code == 200 and len(text.strip()) < MIN_USEFUL_TEXT:
            return "THIN_CONTENT"
        return None


class FirecrawlClient:
    """Small synchronous adapter around ``POST /v2/scrape``."""

    def __init__(
        self,
        api_key: str,
        raw_dir: Path,
        *,
        base_url: str = FIRECRAWL_BASE_URL,
        timeout_s: float = DEFAULT_FIRECRAWL_TIMEOUT_S,
        max_attempts: int = DEFAULT_FIRECRAWL_ATTEMPTS,
        http_client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.api_key = api_key.strip()
        self.raw_dir = Path(raw_dir)
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s
        self.max_attempts = max(1, max_attempts)
        self.http_client = http_client or httpx.Client(timeout=timeout_s)
        self.sleep = sleep

    @classmethod
    def from_env(
        cls,
        raw_dir: Path,
        *,
        http_client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> "FirecrawlClient":
        api_key = os.getenv("FIRECRAWL_API_KEY", "").strip()
        if not api_key:
            raise FirecrawlConfigurationError("FIRECRAWL_API_KEY is not set.")

        base_url = os.getenv("FIRECRAWL_BASE_URL", FIRECRAWL_BASE_URL).strip()
        timeout_raw = os.getenv("FIRECRAWL_TIMEOUT_S", "").strip()
        try:
            timeout_s = float(timeout_raw) if timeout_raw else DEFAULT_FIRECRAWL_TIMEOUT_S
        except ValueError as exc:
            raise FirecrawlConfigurationError(
                "FIRECRAWL_TIMEOUT_S must be a number."
            ) from exc
        if timeout_s <= 0:
            raise FirecrawlConfigurationError(
                "FIRECRAWL_TIMEOUT_S must be greater than zero."
            )

        return cls(
            api_key,
            raw_dir,
            base_url=base_url,
            timeout_s=timeout_s,
            http_client=http_client,
            sleep=sleep,
        )

    def scrape(
        self,
        url: str,
        *,
        local_record: FetchRecord,
        fallback_reason: str,
    ) -> FirecrawlResult:
        """Extract one URL that the local fetcher already approved via robots."""
        record = FetchRecord(
            url=url,
            final_url=url,
            robots_allowed=local_record.robots_allowed,
            robots_url=local_record.robots_url,
            crawl_delay_s=local_record.crawl_delay_s,
            fetched_at=datetime.now(timezone.utc),
            renderer="firecrawl",
            content_format="markdown",
            fallback_reason=fallback_reason,
        )
        if not local_record.robots_allowed:
            return self._failure(
                record,
                "FIRECRAWL_REFUSED: local robots decision did not allow this URL",
                0,
            )
        if not self.api_key:
            return self._failure(record, "FIRECRAWL_CONFIG: API key is empty", 0)

        response: httpx.Response | None = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                response = self.http_client.post(
                    f"{self.base_url}/scrape",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "url": url,
                        "formats": ["markdown", "rawHtml"],
                        "onlyMainContent": True,
                        "onlyCleanContent": False,
                        "maxAge": 0,
                        "skipTlsVerification": False,
                        "storeInCache": False,
                        "timeout": max(1_000, min(int(self.timeout_s * 1_000), 300_000)),
                    },
                    timeout=self.timeout_s,
                )
            except httpx.HTTPError as exc:
                return self._failure(
                    record,
                    f"FIRECRAWL_ERROR: {type(exc).__name__}: {exc}",
                    attempt,
                )

            status = response.status_code
            record.status_code = status
            retryable = status == 429 or 500 <= status < 600
            if retryable and attempt < self.max_attempts:
                self.sleep(self._retry_delay(response, attempt))
                continue
            break

        assert response is not None
        attempts = attempt
        status = response.status_code
        if status >= 400:
            detail = self._error_detail(response)
            return self._failure(
                record,
                f"FIRECRAWL_HTTP_{status}: {detail}",
                attempts,
                disable_fallback=status in {401, 402},
            )

        if len(response.content) > MAX_BYTES:
            return self._failure(
                record,
                f"FIRECRAWL_OVERSIZED: {len(response.content)} bytes",
                attempts,
            )

        try:
            payload = response.json()
        except ValueError as exc:
            return self._failure(
                record,
                f"FIRECRAWL_MALFORMED: {type(exc).__name__}",
                attempts,
            )

        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(payload, dict) or payload.get("success") is not True:
            return self._failure(
                record,
                f"FIRECRAWL_MALFORMED: {self._payload_error(payload)}",
                attempts,
            )
        if not isinstance(data, dict):
            return self._failure(
                record,
                "FIRECRAWL_MALFORMED: response data is missing",
                attempts,
            )

        markdown = data.get("markdown")
        raw_html = data.get("rawHtml")
        if not isinstance(markdown, str) or not markdown.strip():
            return self._failure(
                record,
                "FIRECRAWL_EMPTY: response contained no markdown",
                attempts,
            )
        if not isinstance(raw_html, str):
            return self._failure(
                record,
                "FIRECRAWL_MALFORMED: response contained no raw HTML",
                attempts,
            )

        metadata = data.get("metadata")
        if isinstance(metadata, dict):
            page_status = metadata.get("statusCode")
            if isinstance(page_status, int):
                record.status_code = page_status
            record.final_url = self._safe_final_url(metadata, url)

        record.bytes = len(markdown.encode("utf-8")) + len(raw_html.encode("utf-8"))
        record.content_hash = content_hash(markdown)
        try:
            self._archive(
                url,
                markdown,
                raw_html,
                record,
                local_record=local_record,
                provider_metadata=metadata if isinstance(metadata, dict) else {},
            )
        except OSError as exc:
            return self._failure(
                record,
                f"FIRECRAWL_ARCHIVE_ERROR: {type(exc).__name__}: {exc}",
                attempts,
            )
        return FirecrawlResult(
            text=markdown,
            record=record,
            attempts=attempts,
            retries=attempts - 1,
        )

    def _archive(
        self,
        url: str,
        markdown: str,
        raw_html: str,
        record: FetchRecord,
        *,
        local_record: FetchRecord,
        provider_metadata: dict,
    ) -> None:
        stamp = record.fetched_at.strftime("%Y%m%dT%H%M%SZ")
        base = self.raw_dir / "pages" / f"{_slug(url)}.{stamp}.firecrawl"
        markdown_path = Path(f"{base}.md")
        raw_html_path = Path(f"{base}.raw.html")
        meta_path = Path(f"{base}.meta.json")
        markdown_path.parent.mkdir(parents=True, exist_ok=True)
        markdown_path.write_bytes(markdown.encode("utf-8"))
        record.raw_path = str(markdown_path)
        raw_html_path.write_bytes(raw_html.encode("utf-8"))
        record.source_raw_path = str(raw_html_path)
        archive_metadata = {
            "fetch": record.model_dump(mode="json"),
            "local_fetch": local_record.model_dump(mode="json"),
            "provider_metadata": provider_metadata,
        }
        meta_path.write_bytes(
            (json.dumps(archive_metadata, indent=2, ensure_ascii=False) + "\n").encode(
                "utf-8"
            )
        )

    @staticmethod
    def _safe_final_url(metadata: dict, original: str) -> str:
        candidate = str(metadata.get("url") or metadata.get("sourceURL") or "").strip()
        parts = urlsplit(candidate)
        if parts.scheme in {"http", "https"} and parts.netloc:
            return candidate
        return original

    @staticmethod
    def _retry_delay(response: httpx.Response, attempt: int) -> float:
        raw = response.headers.get("Retry-After", "").strip()
        try:
            delay = float(raw) if raw else float(2 ** (attempt - 1))
        except ValueError:
            delay = float(2 ** (attempt - 1))
        return max(0.0, min(delay, _RETRY_AFTER_CEILING_S))

    @staticmethod
    def _error_detail(response: httpx.Response) -> str:
        try:
            payload = response.json()
        except ValueError:
            return "provider request failed"
        return FirecrawlClient._payload_error(payload)

    @staticmethod
    def _payload_error(payload) -> str:
        if isinstance(payload, dict):
            detail = payload.get("error")
            if isinstance(detail, str) and detail.strip():
                return detail.strip()[:240]
        return "provider returned an invalid response"

    @staticmethod
    def _failure(
        record: FetchRecord,
        failure: str,
        attempts: int,
        *,
        disable_fallback: bool = False,
    ) -> FirecrawlResult:
        record.failure = failure
        return FirecrawlResult(
            text="",
            record=record,
            attempts=attempts,
            retries=max(0, attempts - 1),
            disable_fallback=disable_fallback,
        )
