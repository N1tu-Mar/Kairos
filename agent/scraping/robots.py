"""robots.txt, fetched once per host and written into the repo.

Two reasons the file is cached to disk rather than only held in memory:

*   **Auditability.** A `FetchRecord` says `robots_allowed=True`. The copy of
    the robots.txt that decision was made against sits next to the raw HTML,
    so the claim can be checked months later against what the host actually
    said at the time, not what it says now.
*   **Politeness.** One robots.txt request per host per run, not one per page.

Fail closed. If robots.txt cannot be fetched or cannot be parsed, the host is
treated as **disallowed**. A scraper that reads a network error as permission
is a scraper that ignores robots.txt on exactly the days the host is having
trouble.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import httpx

from agent.sanitize import sanitize_logged_url
from agent.scraping.netguard import BlockedAddress, assert_public_url
from agent.scraping.safehttp import guarded_get

log = logging.getLogger("kairos.scraping.robots")

#: Identifies the crawler and says what it is for. A host that wants to block
#: us must be able to name us.
USER_AGENT = (
    "kairos-funding-research/1.0 (student funding opportunity research; "
    "contact via repository issues)"
)

#: Used when a host publishes no Crawl-delay. Deliberately slower than a
#: browser; these are small university sites and nothing here is urgent.
DEFAULT_CRAWL_DELAY_S = 2.0
MAX_ROBOTS_BYTES = 512_000


@dataclass(frozen=True)
class RobotsDecision:
    """The answer for one URL: whether to fetch, how slowly, and why.

    `reason` is recorded so a skip is inspectable after the fact — "we did
    not fetch this" is only useful alongside which rule said so. Frozen; a
    decision is evidence, not a working value.
    """

    allowed: bool
    robots_url: str
    crawl_delay_s: float
    reason: str


class RobotsCache:
    """One robots.txt per host, cached on disk and in memory."""

    def __init__(
        self,
        cache_dir: Path,
        timeout_s: float = 15.0,
        http_client: httpx.Client | None = None,
    ) -> None:
        """`_parsers` caches per host for the life of the process, including negative results — a host whose robots.txt failed to load is not retried within one sweep."""
        self.cache_dir = Path(cache_dir)
        self.timeout_s = timeout_s
        self.http_client = http_client
        self._parsers: dict[str, RobotFileParser | None] = {}

    def _robots_url(self, url: str) -> tuple[str, str]:
        """`(host, robots.txt URL)` for a page URL, preserving its scheme."""
        parts = urlsplit(url)
        return parts.netloc, f"{parts.scheme}://{parts.netloc}/robots.txt"

    def _load(self, host: str, robots_url: str) -> RobotFileParser | None:
        """Fetch and parse a host's robots.txt, or return None meaning "do not fetch".

        The status mapping is the standard's, and the two directions differ:
        401/403 means the whole host is off limits, while any other 4xx means no
        robots.txt is published and everything is allowed. 5xx and transport
        errors return None — fail closed, because a server that cannot tell us
        its rules is not a server that has none.

        Cached per host including the None, so one failed load disallows that
        host for the rest of the process rather than being retried per URL.
        """
        if host in self._parsers:
            return self._parsers[host]

        parser: RobotFileParser | None = None
        try:
            response = guarded_get(
                robots_url,
                client=self.http_client,
                timeout=self.timeout_s,
                headers={"User-Agent": USER_AGENT},
                max_bytes=MAX_ROBOTS_BYTES,
            )
            if response.status_code == 200:
                parser = RobotFileParser()
                parser.parse(response.text.splitlines())
                self._write_cache(host, response.text)
            elif response.status_code in (401, 403):
                # An access-controlled robots.txt means the whole host is off
                # limits, per the standard.
                parser = None
            elif 400 <= response.status_code < 500:
                # No robots.txt published. The standard reads that as "allow",
                # and we record an empty file so the decision is inspectable.
                parser = RobotFileParser()
                parser.parse([])
                self._write_cache(host, f"# HTTP {response.status_code} — no robots.txt published\n")
            else:
                parser = None
        except (BlockedAddress, httpx.HTTPError, UnicodeDecodeError) as exc:
            log.warning(
                "robots_fetch_failed",
                extra={
                    "url": sanitize_logged_url(robots_url),
                    "error_type": type(exc).__name__,
                },
            )
            parser = None

        self._parsers[host] = parser
        return parser

    @staticmethod
    def _cache_name(host: str) -> str:
        """A host is untrusted input once a search API can supply one.

        `urlsplit` will hand back `..` as a netloc, and `cache_dir / ".."`
        escapes the cache directory. Sanitise rather than trust the caller.
        """
        safe = re.sub(r"[^a-zA-Z0-9.-]+", "-", host).strip(".-")
        safe = re.sub(r"\.{2,}", ".", safe).strip(".")
        return f"{safe or 'unknown-host'}.robots.txt"

    def _write_cache(self, host: str, text: str) -> None:
        """Archive the robots.txt we acted on, under a sanitised filename.

        The archive is the evidence for a skip. The filename goes through
        `_cache_name` because a host from a search API is untrusted input — see
        that method for the traversal it prevents.
        """
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        (self.cache_dir / self._cache_name(host)).write_text(text)

    def check(self, url: str) -> RobotsDecision:
        """May we fetch this URL, and how slowly?"""
        try:
            # Validate the page target before even requesting its robots file.
            # This prevents a credential-bearing or internal URL from causing
            # any network activity at all.
            assert_public_url(url)
        except BlockedAddress:
            return RobotsDecision(
                allowed=False,
                robots_url=sanitize_logged_url(url),
                crawl_delay_s=DEFAULT_CRAWL_DELAY_S,
                reason="target address is not a public web destination",
            )
        host, robots_url = self._robots_url(url)
        parser = self._load(host, robots_url)

        if parser is None:
            return RobotsDecision(
                allowed=False,
                robots_url=robots_url,
                crawl_delay_s=DEFAULT_CRAWL_DELAY_S,
                reason="robots.txt unreachable or access-controlled — treated as disallow",
            )

        allowed = parser.can_fetch(USER_AGENT, url)
        stated = parser.crawl_delay(USER_AGENT)
        delay = max(float(stated), DEFAULT_CRAWL_DELAY_S) if stated else DEFAULT_CRAWL_DELAY_S
        return RobotsDecision(
            allowed=allowed,
            robots_url=robots_url,
            crawl_delay_s=delay,
            reason="allowed by robots.txt" if allowed else "disallowed by robots.txt",
        )
