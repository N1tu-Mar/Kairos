"""Polite fetching, and the raw archive.

Everything the pipeline knows about the outside world enters through this
file. It does four things and nothing else:

1.  Asks `RobotsCache` for permission, and does not fetch without it.
2.  Sleeps between requests to the same host, so a run is a trickle rather
    than a burst.
3.  Writes the exact bytes received into `data/raw/`, next to a JSON sidecar
    recording the URL, the timestamp, the status and the robots decision.
4.  Reduces HTML to text, and refuses to pretend a JavaScript shell is a page.

Point 3 is requirement 9 — raw scraped data stays separate from verified
production data. Nothing downstream re-fetches; extraction reads the archive,
so a disagreement about what a page said is settled by opening a file.

**What this file will not do:** log in, submit a form, solve a CAPTCHA, or
request anything behind an authentication wall. There is no code path for it.
If a page needs any of those, the target is recorded as a failure and a human
decides what to do.
"""

from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol
from urllib.parse import urlsplit

import httpx
from bs4 import BeautifulSoup

from agent.scraping.models import FetchRecord, content_hash
from agent.scraping.netguard import BlockedAddress, assert_public_url
from agent.scraping.robots import USER_AGENT, RobotsCache

log = logging.getLogger("kairos.scraping.fetch")

#: Stripped before text extraction — they carry no page content and their
#: text pollutes every evidence span with menu items.
_CHROME_TAGS = ("script", "style", "noscript", "nav", "header", "footer", "form")

#: A page whose visible text is shorter than this almost certainly did not
#: render. Below it we look for a JavaScript-required banner rather than
#: extracting from an empty shell.
_MIN_USEFUL_TEXT = 400

_JS_REQUIRED = re.compile(
    r"requires javascript|enable javascript|javascript to be enabled|"
    r"please enable js|noscript",
    re.I,
)

#: Nothing on these sites is a 400KB page, and a surprise one is a
#: denial-of-wallet vector for whatever reads the archive later.
MAX_BYTES = 4_000_000

#: Redirect hops followed before giving up. Every hop is address-checked, so
#: this bounds work rather than exposure: a chain longer than this is a loop
#: or a tarpit, and neither is a page worth waiting for. httpx's own default
#: is 20; a funding programme that needs more than five is not real.
MAX_REDIRECTS = 5


class FetchRefused(RuntimeError):
    """We declined to fetch. Recorded on the run, never swallowed."""


class PageFetcher(Protocol):
    """Provider-neutral page retrieval boundary used by the scrape pipeline."""

    def fetch(self, url: str, *, allow_js: bool = False) -> tuple[str, FetchRecord]: ...


def html_to_text(html: str) -> str:
    """Visible text, whitespace-collapsed, chrome removed."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(_CHROME_TAGS):
        tag.decompose()
    text = soup.get_text(separator="\n")
    text = re.sub(r"[ \t\xa0]+", " ", text)
    text = re.sub(r"\n\s*\n\s*", "\n\n", text)
    return text.strip()


def _slug(url: str) -> str:
    """A filesystem-safe archive name for a URL.

    Both halves are sanitised, not just the path. `netloc` used to be
    interpolated raw, and a URL whose host is `..` — which `urlsplit` accepts
    — turned the archive write into a write one directory up. That was
    unreachable while only Rutgers domains and operator-named URLs got here;
    a search-API source hands this function attacker-influenced hosts, so the
    sanitising cannot depend on who the caller is.
    """
    parts = urlsplit(url)
    host = re.sub(r"[^a-zA-Z0-9.-]+", "-", parts.netloc).strip(".-") or "unknown-host"
    # A host of ".." survives the character filter as a dot run; collapse any
    # remaining relative segment so the result can only ever name one directory.
    host = re.sub(r"\.{2,}", ".", host).strip(".") or "unknown-host"
    path = re.sub(r"[^a-zA-Z0-9]+", "-", parts.path).strip("-") or "index"
    return f"{host}/{path}"[:180]


class PoliteFetcher:
    """One host at a time, one request every `crawl_delay` seconds."""

    def __init__(
        self,
        raw_dir: Path,
        timeout_s: float = 30.0,
        robots: RobotsCache | None = None,
    ) -> None:
        """`_last_request` is per-process in-memory state, so the crawl delay is only honoured within one sweep — two concurrent processes would not see each other's timings."""
        self.raw_dir = Path(raw_dir)
        self.timeout_s = timeout_s
        self.robots = robots or RobotsCache(self.raw_dir / "robots")
        self._last_request: dict[str, float] = {}

    # ── rate limiting ────────────────────────────────────────────────────

    def _wait_turn(self, host: str, delay_s: float) -> None:
        """Sleep until this host's crawl delay has elapsed since our last request.

        Uses `time.monotonic`, so a system clock change cannot make the delay
        appear to have passed. Blocking and synchronous: the sweep is sequential
        by design, and rate limiting a scraper with sleep is the honest version.

        The timestamp is recorded even when no sleep was needed, so the delay is
        measured request-to-request rather than sleep-to-sleep.
        """
        previous = self._last_request.get(host)
        if previous is not None:
            elapsed = time.monotonic() - previous
            if elapsed < delay_s:
                time.sleep(delay_s - elapsed)
        self._last_request[host] = time.monotonic()

    # ── the archive ──────────────────────────────────────────────────────

    def _archive(self, url: str, body: str, record: FetchRecord) -> Path:
        """Write the page body and a JSON sidecar, and return the body's path.

        The archive is what makes an extraction re-checkable months later — the
        claim "the page said this" is only as good as the copy of the page. The
        filename carries a slug and a UTC timestamp, so refetching the same URL
        adds a version rather than overwriting the evidence.

        `record.raw_path` is set before the sidecar is written, because a sidecar
        naming no body cannot be replayed.
        """
        stamp = record.fetched_at.strftime("%Y%m%dT%H%M%SZ")
        path = self.raw_dir / "pages" / f"{_slug(url)}.{stamp}.html"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
        # Set before the sidecar is written: a meta file whose `raw_path` is
        # empty cannot be replayed, which defeats the point of archiving.
        record.raw_path = str(path)
        path.with_suffix(".meta.json").write_text(
            record.model_dump_json(indent=2), encoding="utf-8"
        )
        return path

    # ── the one network call ─────────────────────────────────────────────

    def _get_following_redirects(self, url: str) -> httpx.Response:
        """GET `url`, checking every hop's address before opening a socket.

        httpx's own `follow_redirects=True` is what this replaces, and the
        reason is that it made the address check decorative: the check ran on
        the URL we were handed, and the redirect chain after it went wherever
        it liked. A page that 302s to `169.254.170.2` passed the front door
        and fetched the credential endpoint anyway.

        Raises `BlockedAddress` for a hop that must not be fetched, and
        `httpx.HTTPError` for an ordinary network failure. A chain longer
        than `MAX_REDIRECTS` is a loop or a tarpit; both are `httpx.
        TooManyRedirects`, which the caller already records as a fetch error.
        """
        current = url
        for _ in range(MAX_REDIRECTS + 1):
            assert_public_url(current)
            response = httpx.get(
                current,
                timeout=self.timeout_s,
                # Off on purpose. The loop is here so the guard above runs on
                # every hop; handing this back to httpx re-opens the hole.
                follow_redirects=False,
                headers={"User-Agent": USER_AGENT, "Accept": "text/html,*/*"},
            )
            if not response.has_redirect_location:
                return response
            # `.join` resolves a relative Location against the URL it came
            # from, which is what a browser does and what the spec requires.
            current = str(response.url.join(response.headers["location"]))

        raise httpx.TooManyRedirects(
            f"more than {MAX_REDIRECTS} redirects", request=response.request
        )

    def fetch(self, url: str, *, allow_js: bool = False) -> tuple[str, FetchRecord]:
        """Fetch one page. Returns `(text, record)`; text is "" on failure.

        Never raises for an ordinary failure — a dead target is data about
        the run, not an exception that stops it.
        """
        decision = self.robots.check(url)
        record = FetchRecord(
            url=url,
            robots_allowed=decision.allowed,
            robots_url=decision.robots_url,
            crawl_delay_s=decision.crawl_delay_s,
            fetched_at=datetime.now(timezone.utc),
        )

        if not decision.allowed:
            record.failure = f"ROBOTS_DISALLOWED: {decision.reason}"
            log.info("robots_disallowed", extra={"url": url})
            return "", record

        host = urlsplit(url).netloc
        self._wait_turn(host, decision.crawl_delay_s)

        try:
            response = self._get_following_redirects(url)
        except BlockedAddress as exc:
            # Refused before a socket was opened. Recorded like a robots
            # denial — a decision about the run, not an error in it.
            record.failure = f"BLOCKED_ADDRESS: {exc}"
            log.warning("blocked_address", extra={"url": url})
            return "", record
        except httpx.HTTPError as exc:
            record.failure = f"FETCH_ERROR: {type(exc).__name__}: {exc}"
            return "", record

        record.status_code = response.status_code
        record.final_url = str(response.url)
        record.bytes = len(response.content)

        if response.status_code != 200:
            record.failure = f"HTTP_{response.status_code}"
            return "", record

        if record.bytes > MAX_BYTES:
            record.failure = f"OVERSIZED: {record.bytes} bytes"
            return "", record

        body = response.text
        text = html_to_text(body)

        if len(text) < _MIN_USEFUL_TEXT and _JS_REQUIRED.search(body):
            # The honest outcome. This is the *only* condition under which
            # the pipeline reaches for a browser, and only when asked to.
            record.failure = "NEEDS_JS: page renders its content with JavaScript"
            record.content_hash = content_hash(text)
            record.raw_path = str(self._archive(url, body, record))
            if allow_js:
                return self._fetch_with_playwright(url, record)
            return "", record

        record.content_hash = content_hash(text)
        record.raw_path = str(self._archive(url, body, record))
        return text, record

    # ── the escape hatch, used as rarely as possible ─────────────────────

    def _fetch_with_playwright(
        self, url: str, record: FetchRecord
    ) -> tuple[str, FetchRecord]:
        """Render one page in a headless browser.

        Reached only when a static fetch already proved the page is a
        JavaScript shell, and only when the caller passed `allow_js=True`.
        Playwright is an optional dependency: if it is not installed the
        target stays a recorded failure rather than becoming a silent gap.
        """
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            record.failure = (
                "NEEDS_JS: page requires JavaScript and playwright is not installed "
                "(uv add --optional js playwright && uv run playwright install chromium)"
            )
            return "", record

        try:
            with sync_playwright() as play:
                browser = play.chromium.launch(headless=True)
                try:
                    page = browser.new_page(user_agent=USER_AGENT)
                    page.goto(url, wait_until="networkidle", timeout=self.timeout_s * 1000)
                    body = page.content()
                finally:
                    browser.close()
        except Exception as exc:  # noqa: BLE001 — a browser failure is a fetch failure
            record.failure = f"PLAYWRIGHT_ERROR: {type(exc).__name__}: {exc}"
            return "", record

        text = html_to_text(body)
        record.renderer = "playwright"
        record.failure = None
        record.bytes = len(body)
        record.content_hash = content_hash(text)
        record.raw_path = str(self._archive(url, body, record))
        return text, record


def load_archived(meta_path: Path) -> tuple[str, FetchRecord]:
    """Re-read a page from the archive instead of the network.

    What makes the extraction layer testable, and what makes a scrape
    reproducible without asking a university web server the same question
    twice.
    """
    record = FetchRecord.model_validate(json.loads(Path(meta_path).read_text()))
    body = Path(record.raw_path).read_text(encoding="utf-8")
    if record.content_format == "markdown":
        return body, record
    return html_to_text(body), record
