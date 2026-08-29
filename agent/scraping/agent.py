"""Search-backed web scrapers for funding opportunities.

These agents do one job: turn search results into scrape candidates. They do
not assess fit, draft applications, notify founders, or promote anything into
the seed catalog. Search-discovered pages are fetched once, extracted through
the existing evidence-first scraper, deduplicated, and left as
NEEDS_HUMAN_REVIEW candidates.

One `SearchClient` can feed multiple lanes. The lane decides the query set,
candidate filter, output file, and review label; the search API boundary stays
shared so swapping Brave for another provider is still one adapter change.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx

from agent.scraping.fetch import PoliteFetcher
from agent.scraping.models import ScrapedOpportunity, ScrapeRun
from agent.scraping.pipeline import RAW_DIR, scrape, write_candidates
from agent.scraping.registry import Target, Tier

log = logging.getLogger("kairos.scraping.agent")

BRAVE_SEARCH_URL = "https://api.search.brave.com/res/v1/web/search"
DEFAULT_GENERAL_WEB_CANDIDATES_PATH = RAW_DIR.parent / "opportunities.web.candidates.json"
DEFAULT_UNIVERSITY_WEB_CANDIDATES_PATH = (
    RAW_DIR.parent / "opportunities.university-web.candidates.json"
)
DEFAULT_WEB_CANDIDATES_PATH = DEFAULT_GENERAL_WEB_CANDIDATES_PATH

GENERAL_QUERIES: tuple[str, ...] = (
    '"startup grant" "student founder"',
    '"small business" grant startup non-dilutive',
    '"founder" "pitch competition" "cash prize"',
    '"entrepreneur" fellowship grant application',
)

UNIVERSITY_QUERIES: tuple[str, ...] = (
    '"student founder" grant OR prize OR pitch competition',
    '"undergraduate" startup grant application',
    '"university" entrepreneurship "pitch competition" prize',
    '"student entrepreneurs" "cash prize" application',
)
DEFAULT_QUERIES = GENERAL_QUERIES

_OPPORTUNITY_HINT = re.compile(
    r"\b(grant|grants|funding|fund|prize|prizes|award|awards|fellowship|"
    r"scholarship|competition|challenge|pitch|accelerator|incubator|"
    r"venture|startup|entrepreneur\w*|innovation|commercializ\w*|seed)\b",
    re.I,
)

_UNIVERSITY_HINT = re.compile(
    r"\b(university|college|campus|undergraduate|graduate|student founder|"
    r"student founders|student entrepreneur|student entrepreneurs|students|"
    r"entrepreneurship center|innovation center|business school|school of "
    r"business|pitch competition)\b|\.edu\b",
    re.I,
)

_SKIP_EXTENSIONS = re.compile(
    r"\.(?:pdf|doc|docx|ppt|pptx|xls|xlsx|zip|png|jpe?g|gif|webp|mp4|mov|mp3)$",
    re.I,
)

_TRACKING_PARAMS = {
    "fbclid",
    "gclid",
    "mc_cid",
    "mc_eid",
    "utm_campaign",
    "utm_content",
    "utm_medium",
    "utm_source",
    "utm_term",
}

_SKIP_HOSTS = (
    "facebook.com",
    "instagram.com",
    "linkedin.com",
    "tiktok.com",
    "twitter.com",
    "x.com",
    "youtube.com",
)


class SearchApiError(RuntimeError):
    """A search provider failed before any page fetch began."""


@dataclass(frozen=True)
class SearchResult:
    title: str
    url: str
    snippet: str = ""
    query: str = ""
    rank: int = 0
    source: str = "search"


class SearchClient(Protocol):
    def search(self, query: str, *, count: int) -> list[SearchResult]: ...


@dataclass(frozen=True)
class ScraperLane:
    """A named search lane over the shared search-provider boundary."""

    name: str
    label: str
    tier: Tier
    queries: tuple[str, ...]
    output_path: Path
    tags: tuple[str, ...]
    domains: tuple[str, ...] = ()
    priority: int = 2
    require_university_signal: bool = False


GENERAL_LANE = ScraperLane(
    name="general",
    label="general funding",
    tier="GENERAL_WEB_SEARCH",
    queries=GENERAL_QUERIES,
    output_path=DEFAULT_GENERAL_WEB_CANDIDATES_PATH,
    tags=("web search", "general funding"),
)

UNIVERSITY_LANE = ScraperLane(
    name="university",
    label="university funding",
    tier="UNIVERSITY_WEB_SEARCH",
    queries=UNIVERSITY_QUERIES,
    output_path=DEFAULT_UNIVERSITY_WEB_CANDIDATES_PATH,
    tags=("web search", "university", "student founder"),
    require_university_signal=True,
)

LANES = {lane.name: lane for lane in (GENERAL_LANE, UNIVERSITY_LANE)}


def lane_by_name(name: str) -> ScraperLane:
    try:
        return LANES[name]
    except KeyError as exc:
        available = ", ".join(sorted(LANES))
        raise ValueError(f"unknown scraper lane {name!r}; expected one of {available}") from exc


class BraveSearchClient:
    """Small adapter around Brave Web Search.

    Bedrock supplies the model. It does not supply web search results, so this
    adapter is the narrow boundary where an external search API enters.
    """

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = BRAVE_SEARCH_URL,
        timeout_s: float = 15.0,
        http_client: httpx.Client | None = None,
    ) -> None:
        self.api_key = api_key.strip()
        self.base_url = base_url
        self.timeout_s = timeout_s
        self.http_client = http_client or httpx.Client(timeout=timeout_s)

    @classmethod
    def from_env(cls) -> "BraveSearchClient":
        api_key = os.getenv("BRAVE_SEARCH_API_KEY") or os.getenv("KAIROS_SEARCH_API_KEY")
        if not api_key or not api_key.strip():
            raise SearchApiError(
                "BRAVE_SEARCH_API_KEY or KAIROS_SEARCH_API_KEY is not set."
            )
        return cls(api_key)

    def search(self, query: str, *, count: int) -> list[SearchResult]:
        if not self.api_key:
            raise SearchApiError("Brave search API key is empty.")

        response = self.http_client.get(
            self.base_url,
            headers={
                "Accept": "application/json",
                "X-Subscription-Token": self.api_key,
            },
            params={
                "q": query,
                "count": max(1, min(count, 20)),
                "safesearch": "moderate",
                "result_filter": "web",
            },
            timeout=self.timeout_s,
        )
        try:
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise SearchApiError(f"Brave search failed for {query!r}: {exc}") from exc

        results = ((payload.get("web") or {}).get("results")) or []
        parsed: list[SearchResult] = []
        for index, item in enumerate(results, start=1):
            url = str(item.get("url") or "").strip()
            if not url:
                continue
            parsed.append(
                SearchResult(
                    title=_clean_text(str(item.get("title") or "")),
                    url=url,
                    snippet=_clean_text(str(item.get("description") or "")),
                    query=query,
                    rank=index,
                    source="brave",
                )
            )
        return parsed


@dataclass(frozen=True)
class WebScraperConfig:
    lane: ScraperLane = GENERAL_LANE
    queries: tuple[str, ...] = GENERAL_QUERIES
    domains: tuple[str, ...] = ()
    max_results_per_query: int = 10
    max_pages: int = 25
    allow_js: bool = False
    require_opportunity_hint: bool = True
    raw_dir: Path = RAW_DIR

    @classmethod
    def for_lane(cls, lane: ScraperLane | str, **overrides: Any) -> "WebScraperConfig":
        selected = lane_by_name(lane) if isinstance(lane, str) else lane
        values: dict[str, Any] = {
            "lane": selected,
            "queries": selected.queries,
            "domains": selected.domains,
            "raw_dir": RAW_DIR,
        }
        values.update(overrides)
        return cls(**values)


@dataclass
class WebScraperAgent:
    """Discovers candidate pages, then delegates scraping to the pipeline."""

    search_client: SearchClient
    config: WebScraperConfig = field(default_factory=WebScraperConfig)
    fetcher: PoliteFetcher | None = None

    def discover_targets(self) -> tuple[list[Target], list[str]]:
        targets: list[Target] = []
        notes: list[str] = []
        seen: set[str] = set()

        for query in self.config.queries:
            try:
                results = self.search_client.search(
                    query, count=self.config.max_results_per_query
                )
            except Exception as exc:  # noqa: BLE001
                notes.append(f"search failed for {query!r}: {type(exc).__name__}: {exc}")
                continue

            for result in results:
                normalized = normalize_candidate_url(result.url)
                if normalized is None:
                    continue
                result = replace(result, url=normalized, query=result.query or query)

                if normalized in seen or not self._should_fetch(result):
                    continue
                seen.add(normalized)
                targets.append(target_from_result(result, lane=self.config.lane))

                if len(targets) >= self.config.max_pages:
                    return targets, notes

        return targets, notes

    def run(self) -> tuple[list[ScrapedOpportunity], ScrapeRun]:
        targets, notes = self.discover_targets()
        if not targets:
            run = ScrapeRun(run_id=f"web_scrape_{uuid.uuid4().hex[:12]}")
            run.finished_at = datetime.now(timezone.utc)
            run.notes.extend(notes)
            run.notes.append("search returned no fetchable candidate URLs")
            return [], run

        records, run = scrape(
            targets,
            raw_dir=self.config.raw_dir,
            allow_js=self.config.allow_js,
            discover=False,
            fetcher=self.fetcher,
        )
        run.notes.extend(notes)
        run.notes.append(
            f"{self.config.lane.label} search produced {len(targets)} "
            f"fetchable URL(s) from "
            f"{len(self.config.queries)} query/queries"
        )
        return records, run

    def write(
        self,
        *,
        path: Path | None = None,
        run_log: Path | None = None,
    ) -> tuple[Path, list[ScrapedOpportunity], ScrapeRun]:
        records, run = self.run()
        written = write_candidates(
            records,
            run,
            path=path or self.config.lane.output_path,
            run_log=run_log or self.config.raw_dir / "scrape_runs.jsonl",
        )
        return written, records, run

    def _should_fetch(self, result: SearchResult) -> bool:
        if self.config.domains and not host_matches_any(result.url, self.config.domains):
            return False

        if self.config.lane.require_university_signal and not is_university_search_result(result):
            return False

        host = urlsplit(result.url).netloc.lower().removeprefix("www.")
        if any(host == skipped or host.endswith(f".{skipped}") for skipped in _SKIP_HOSTS):
            return False

        if self.config.require_opportunity_hint:
            haystack = f"{result.title} {result.snippet} {result.url}"
            if not _OPPORTUNITY_HINT.search(haystack):
                return False

        return True


class GeneralWebScraperAgent(WebScraperAgent):
    """Broad public-web funding scraper over the shared search provider."""

    def __init__(
        self,
        search_client: SearchClient,
        config: WebScraperConfig | None = None,
        fetcher: PoliteFetcher | None = None,
    ) -> None:
        super().__init__(
            search_client=search_client,
            config=config or WebScraperConfig.for_lane(GENERAL_LANE),
            fetcher=fetcher,
        )


class UniversityWebScraperAgent(WebScraperAgent):
    """University and student-founder opportunity scraper over shared search."""

    def __init__(
        self,
        search_client: SearchClient,
        config: WebScraperConfig | None = None,
        fetcher: PoliteFetcher | None = None,
    ) -> None:
        super().__init__(
            search_client=search_client,
            config=config or WebScraperConfig.for_lane(UNIVERSITY_LANE),
            fetcher=fetcher,
        )


def normalize_candidate_url(url: str) -> str | None:
    raw = (url or "").strip()
    parts = urlsplit(raw)
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        return None
    if _SKIP_EXTENSIONS.search(parts.path):
        return None

    query = urlencode(
        [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k not in _TRACKING_PARAMS],
        doseq=True,
    )
    path = parts.path or "/"
    normalized = urlunsplit((parts.scheme, parts.netloc.lower(), path, query, ""))
    return normalized.rstrip("/") if path != "/" else normalized


def host_matches_any(url: str, domains: tuple[str, ...]) -> bool:
    host = urlsplit(url).netloc.lower().removeprefix("www.")
    for domain in domains:
        cleaned = domain.lower().strip().removeprefix("www.")
        if not cleaned:
            continue
        if host == cleaned or host.endswith(f".{cleaned}"):
            return True
    return False


def is_university_search_result(result: SearchResult) -> bool:
    """Whether a hit belongs in the university/student-founder lane."""
    host = urlsplit(result.url).netloc.lower().removeprefix("www.")
    if host.endswith(".edu"):
        return True
    haystack = f"{result.title} {result.snippet} {result.url}"
    return bool(_UNIVERSITY_HINT.search(haystack))


def target_from_result(result: SearchResult, *, lane: ScraperLane = GENERAL_LANE) -> Target:
    title = result.title or _title_from_url(result.url)
    note = (
        f"Discovered by {result.source} {lane.label} search query {result.query!r}"
        f" at rank {result.rank or 'unknown'}."
    )
    if result.snippet:
        note = f"{note} Search snippet: {result.snippet[:240]}"

    return Target(
        key=f"{lane.name}_web_{hashlib.sha1(result.url.encode('utf-8')).hexdigest()[:12]}",
        title=title,
        organization=_host_label(result.url),
        url=result.url,
        tier=lane.tier,
        priority=lane.priority,
        operator_note=note,
        tags=lane.tags,
    )


def _clean_text(value: str) -> str:
    value = re.sub(r"<[^>]+>", "", value or "")
    return re.sub(r"\s+", " ", value).strip()


def _title_from_url(url: str) -> str:
    path = urlsplit(url).path.strip("/").rsplit("/", 1)[-1]
    return path.replace("-", " ").replace("_", " ").strip().title() or url


def _host_label(url: str) -> str:
    host = urlsplit(url).netloc.lower().removeprefix("www.")
    return host or "unknown"
