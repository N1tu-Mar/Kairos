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


@dataclass(frozen=True)
class RobotsDecision:
    allowed: bool
    robots_url: str
    crawl_delay_s: float
    reason: str


class RobotsCache:
    """One robots.txt per host, cached on disk and in memory."""

    def __init__(self, cache_dir: Path, timeout_s: float = 15.0) -> None:
        self.cache_dir = Path(cache_dir)
        self.timeout_s = timeout_s
        self._parsers: dict[str, RobotFileParser | None] = {}

    def _robots_url(self, url: str) -> tuple[str, str]:
        parts = urlsplit(url)
        return parts.netloc, f"{parts.scheme}://{parts.netloc}/robots.txt"

    def _load(self, host: str, robots_url: str) -> RobotFileParser | None:
        if host in self._parsers:
            return self._parsers[host]

        parser: RobotFileParser | None = None
        try:
            response = httpx.get(
                robots_url,
                timeout=self.timeout_s,
                follow_redirects=True,
                headers={"User-Agent": USER_AGENT},
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
        except (httpx.HTTPError, UnicodeDecodeError) as exc:
            log.warning("robots_fetch_failed", extra={"host": host, "error": str(exc)})
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
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        (self.cache_dir / self._cache_name(host)).write_text(text)

    def check(self, url: str) -> RobotsDecision:
        """May we fetch this URL, and how slowly?"""
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
