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
import json
import logging
import os
import re
import uuid
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx

from agent.scraping.fetch import PageFetcher
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

_CURRENT_YEAR = date.today().year
_NEXT_YEAR = _CURRENT_YEAR + 1

GENERAL_QUERIES: tuple[str, ...] = (
    f'"startup grant" founder "applications open" {_CURRENT_YEAR} OR {_NEXT_YEAR}',
    '"non-dilutive funding" startup "apply now" OR rolling',
    f'founder "pitch competition" "cash prize" application {_CURRENT_YEAR} OR {_NEXT_YEAR}',
    'entrepreneur fellowship grant "accepting applications"',
)

UNIVERSITY_QUERIES: tuple[str, ...] = (
    f'"student founder" grant OR prize "applications open" {_CURRENT_YEAR} OR {_NEXT_YEAR}',
    '"undergraduate" startup grant "apply now" OR rolling',
    f'university entrepreneurship "pitch competition" application {_CURRENT_YEAR} OR {_NEXT_YEAR}',
    '"student entrepreneurs" "cash prize" "accepting applications"',
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
    "f6s.com",
    "fastcompany.com",
    "builduplabs.com",
    "creditforstartups.com",
    "fundingcake.com",
    "forbes.com",
    "globenewswire.com",
    "grantwatch.com",
    "inc.com",
    "instagram.com",
    "instrumentl.com",
    "linkedin.com",
    "nerdwallet.com",
    "opengrants.io",
    "prnewswire.com",
    "tiktok.com",
    "techcrunch.com",
    "twitter.com",
    "yourstory.com",
    "x.com",
    "youtube.com",
)

_ACTIVE_RESULT_HINT = re.compile(
    r"\b(apply now|applications? open|accepting applications?|now accepting|"
    r"rolling|deadline|application portal|submit an application)\b",
    re.I,
)
_STALE_RESULT_HINT = re.compile(
    r"\b(archive|archived|winner|winners|finalist|finalists|recipient|recipients|"
    r"alumni|closed|applications? ended|deadline passed|past competition)\b",
    re.I,
)
_SECONDARY_RESULT_HINT = re.compile(
    r"\b(best|top\s+\d+|list of|roundup|directory|database|resources?|"
    r"grants and programs|grant programs for|funding programs|grants funding|"
    r"grants for small business|founder opportunities|credits?|perks?|how to find)\b|"
    r"\b\d{2,}\s+(?:ways|grants|programs|opportunities)\b",
    re.I,
)
_SECONDARY_PATH_HINT = re.compile(
    r"/(?:learn|blog|news|resources?|articles?)/|^/p/|grants-and-programs|"
    r"/20\d{2}/(?:0[1-9]|1[0-2])/",
    re.I,
)
_DIRECT_PATH_HINT = re.compile(
    r"/(?:apply|application|applications|competition|competitions|grant|grants|"
    r"fellowship|fellowships|challenge|challenges|program|programs)(?:/|$|-)",
    re.I,
)


class SearchApiError(RuntimeError):
    """A search provider failed before any page fetch began."""


@dataclass(frozen=True)
class SearchResult:
    """One hit from a search provider, normalised across providers.

    `rank` is the provider's ordering, kept so a candidate's operator note
    can say where it came from. Everything here is attacker-influenced text —
    a title and snippet are whatever the indexed page chose to say — so it is
    used for filtering and provenance notes, never trusted as fact.
    """

    title: str
    url: str
    snippet: str = ""
    query: str = ""
    rank: int = 0
    source: str = "search"


class SearchClient(Protocol):
    """The one boundary an external search API enters through.

    Implementations raise `SearchApiError` for a provider-level failure.
    `discover_targets` catches *any* exception per query and records a note,
    so a broken provider degrades the sweep rather than ending it.
    """

    #: Up to `count` hits for one query. Raises `SearchApiError` on a
    #: provider failure.
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
    """Resolve a lane name, or raise `ValueError` listing the valid ones.

    Unknown names fail loudly rather than falling back to the general lane —
    a typo'd lane silently writing to the wrong candidate file is the failure
    this prevents.
    """
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
        """An injected `http_client` is what lets tests exercise this without a network; otherwise one is created per client and lives as long as it does."""
        self.api_key = api_key.strip()
        self.base_url = base_url
        self.timeout_s = timeout_s
        self.http_client = http_client or httpx.Client(timeout=timeout_s)

    @classmethod
    def from_env(cls) -> "BraveSearchClient":
        """Build from `BRAVE_SEARCH_API_KEY`, falling back to `KAIROS_SEARCH_API_KEY`.

        A missing or blank key raises `SearchApiError` here rather than
        producing a client that fails on first use.
        """
        api_key = os.getenv("BRAVE_SEARCH_API_KEY") or os.getenv("KAIROS_SEARCH_API_KEY")
        if not api_key or not api_key.strip():
            raise SearchApiError(
                "BRAVE_SEARCH_API_KEY or KAIROS_SEARCH_API_KEY is not set."
            )
        return cls(api_key)

    def search(self, query: str, *, count: int) -> list[SearchResult]:
        """One provider call, mapped to `SearchResult`s.

        No retry and no rate limiting — the crawl delay in `PoliteFetcher`
        governs page fetches, not search calls, and a sweep issues one search per
        configured query.
        """
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
                "freshness": "py",
                "country": "US",
                "search_lang": "en",
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
    """What one sweep is allowed to do: which queries, how many pages, how far.

    `max_pages` is the hard ceiling on fetches for the whole sweep, not per
    query. `require_opportunity_hint` is a cheap pre-filter applied to the
    search result's own text, so a page is rejected before it is ever
    fetched.
    """

    lane: ScraperLane = GENERAL_LANE
    queries: tuple[str, ...] = GENERAL_QUERIES
    domains: tuple[str, ...] = ()
    max_results_per_query: int = 10
    max_pages: int = 25
    allow_js: bool = False
    require_opportunity_hint: bool = True
    raw_dir: Path = RAW_DIR
    today: date = field(default_factory=date.today)

    @classmethod
    def for_lane(cls, lane: ScraperLane | str, **overrides: Any) -> "WebScraperConfig":
        """Build a config from a lane, letting `overrides` win over the lane's defaults.

        Note `raw_dir` is forced to the module-level `RAW_DIR` before overrides
        apply, so a lane cannot redirect where evidence is archived but an
        explicit caller still can.
        """
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
    fetcher: PageFetcher | None = None
    stale_records: list[ScrapedOpportunity] = field(default_factory=list, init=False)

    def discover_targets(self) -> tuple[list[Target], list[str]]:
        """Search every query and return the fetchable targets, plus notes.

        Three things happen per hit, in order: the URL is normalised (which is
        also the dedupe key), it is filtered by `_should_fetch`, and it becomes a
        `Target`. Deduplication is by normalised URL across all queries, so the
        first query to find a page owns its provenance note.

        Every query is issued before selection. Results inside each query are
        ordered by current-application signals, then selected round-robin so an
        early broad query cannot consume the page budget. A failed query records
        a note and the sweep continues.
        """
        targets: list[Target] = []
        notes: list[str] = []
        seen: set[str] = set()
        batches: list[list[SearchResult]] = []

        for query in self.config.queries:
            try:
                results = self.search_client.search(
                    query, count=self.config.max_results_per_query
                )
            except Exception as exc:  # noqa: BLE001
                notes.append(f"search failed for {query!r}: {type(exc).__name__}: {exc}")
                continue

            batch: list[SearchResult] = []
            for result in results:
                normalized = normalize_candidate_url(result.url)
                if normalized is None:
                    continue
                result = replace(result, url=normalized, query=result.query or query)

                if not self._should_fetch(result):
                    continue
                batch.append(result)
            batch.sort(
                key=lambda result: search_result_priority(result, self.config.today),
                reverse=True,
            )
            batches.append(batch)

        batches.sort(
            key=lambda batch: (
                search_result_priority(batch[0], self.config.today)
                if batch
                else (-10_000, 0)
            ),
            reverse=True,
        )
        for result in _round_robin(batches):
            if result.url in seen:
                continue
            seen.add(result.url)
            targets.append(target_from_result(result, lane=self.config.lane))
            if len(targets) >= self.config.max_pages:
                break

        return targets, notes

    def run(self) -> tuple[list[ScrapedOpportunity], ScrapeRun]:
        """Discover targets, then hand them to the shared scrape pipeline.

        `discover=False`: the pipeline must not follow links out of these pages.
        Search chose what to fetch, and link-following would put pages nobody
        vetted into the candidate file.

        An empty target list still returns a `ScrapeRun` with notes, so "the
        search found nothing" is a recorded outcome rather than an empty result
        indistinguishable from a sweep that never ran.
        """
        self.stale_records = []
        targets, notes = self.discover_targets()
        if not targets:
            run = ScrapeRun(run_id=f"web_scrape_{uuid.uuid4().hex[:12]}")
            run.finished_at = datetime.now(timezone.utc)
            run.notes.extend(self._fetcher_run_notes())
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
        self.stale_records = [
            record
            for record in records
            if record.deadline_iso is not None and record.deadline_iso < self.config.today
        ]
        records = [record for record in records if record not in self.stale_records]
        run.opportunities_found = len(records)
        run.notes.extend(self._fetcher_run_notes())
        run.notes.extend(notes)
        if self.stale_records:
            run.notes.append(
                f"excluded {len(self.stale_records)} explicit past-deadline "
                "candidate(s) from active review; raw evidence and stale archive retained"
            )
        run.notes.append(
            f"{self.config.lane.label} search produced {len(targets)} "
            f"fetchable URL(s) from "
            f"{len(self.config.queries)} query/queries"
        )
        return records, run

    def _fetcher_run_notes(self) -> list[str]:
        """Return optional provider accounting without coupling to one fetcher."""
        run_notes = getattr(self.fetcher, "run_notes", None)
        return list(run_notes()) if callable(run_notes) else []

    def write(
        self,
        *,
        path: Path | None = None,
        run_log: Path | None = None,
    ) -> tuple[Path, list[ScrapedOpportunity], ScrapeRun]:
        """Run the sweep and write the candidates file plus a run-log line.

        Writes to the lane's `output_path` unless overridden. Everything written
        is `NEEDS_HUMAN_REVIEW` — this is a review queue, and nothing here can
        reach a founder until a person marks it ACCEPTED.
        """
        records, run = self.run()
        written = write_candidates(
            records,
            run,
            path=path or self.config.lane.output_path,
            run_log=run_log or self.config.raw_dir / "scrape_runs.jsonl",
            remove=self.stale_records,
        )
        if self.stale_records:
            _write_stale_archive(
                self.stale_records,
                stale_candidates_path(path or self.config.lane.output_path),
            )
        return written, records, run

    def _should_fetch(self, result: SearchResult) -> bool:
        """Whether a search hit is worth spending a fetch on.

        Four filters, cheapest first, all applied before any request: the domain
        allowlist when the lane has one, the university signal for that lane, a
        skip-list of hosts (aggregators and social sites), and the opportunity
        hint regex over the hit's own title, snippet and URL.

        The hint filter matches on provider-supplied text, so it is a heuristic
        about what search *said* the page is, not about what it contains.
        """
        if self.config.domains and not host_matches_any(result.url, self.config.domains):
            return False

        if self.config.lane.require_university_signal and not is_university_search_result(result):
            return False

        host = urlsplit(result.url).netloc.lower().removeprefix("www.")
        if any(host == skipped or host.endswith(f".{skipped}") for skipped in _SKIP_HOSTS):
            return False

        haystack = f"{result.title} {result.snippet}"
        if _SECONDARY_RESULT_HINT.search(haystack) or _SECONDARY_PATH_HINT.search(
            urlsplit(result.url).path
        ):
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
        fetcher: PageFetcher | None = None,
    ) -> None:
        """Preset for the general lane. Same behaviour as `WebScraperAgent` with `GENERAL_LANE` config."""
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
        fetcher: PageFetcher | None = None,
    ) -> None:
        """Preset for the university lane, which additionally requires a university signal per hit."""
        super().__init__(
            search_client=search_client,
            config=config or WebScraperConfig.for_lane(UNIVERSITY_LANE),
            fetcher=fetcher,
        )


def normalize_candidate_url(url: str) -> str | None:
    """Canonicalise a search hit's URL, or None if it is not worth fetching.

    Doing both jobs in one function is deliberate: the normalised form is the
    deduplication key, so two URLs that differ only by tracking parameters,
    trailing slash or host case must not be fetched twice.

    Returns None for non-HTTP schemes, hosts that failed to parse, and paths
    ending in a binary extension. Note the fragment is dropped and the query
    is kept minus tracking parameters — a page whose identity genuinely lives
    in its fragment would collapse into its parent here.
    """
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


def search_result_priority(
    result: SearchResult, today: date | None = None
) -> tuple[int, int]:
    """Prefer active application pages while retaining provider rank as a tie-breaker."""
    today = today or date.today()
    haystack = f"{result.title} {result.snippet} {result.url}"
    score = 0
    if _ACTIVE_RESULT_HINT.search(haystack):
        score += 6
    if _STALE_RESULT_HINT.search(haystack):
        score -= 8
    path = urlsplit(result.url).path
    if _DIRECT_PATH_HINT.search(path):
        score += 4
    host = urlsplit(result.url).netloc.lower()
    if host.endswith(".gov") or host.endswith(".edu"):
        score += 3

    years = {int(value) for value in re.findall(r"\b20\d{2}\b", haystack)}
    if today.year in years or today.year + 1 in years:
        score += 3
    if years and max(years) < today.year:
        score -= 5
    return score, -result.rank


def _round_robin(batches: list[list[SearchResult]]):
    """Yield one result per query per pass, preserving each batch's order."""
    depth = 0
    while True:
        emitted = False
        for batch in batches:
            if depth < len(batch):
                emitted = True
                yield batch[depth]
        if not emitted:
            return
        depth += 1


def stale_candidates_path(path: Path) -> Path:
    """Return the operator archive beside an active candidate file."""
    path = Path(path)
    return path.with_name(f"{path.stem}.stale{path.suffix}")


def _write_stale_archive(records: list[ScrapedOpportunity], path: Path) -> None:
    """Append or refresh stale records without making them an active review queue."""
    existing: dict[str, dict[str, Any]] = {}
    if path.exists():
        existing = {
            row.get("scrape_id", ""): row
            for row in json.loads(path.read_text())
            if row.get("scrape_id")
        }
    for record in records:
        row = json.loads(record.model_dump_json())
        previous = existing.get(record.scrape_id)
        if previous:
            row["review_status"] = previous.get("review_status", row["review_status"])
            row["founder_reviews"] = previous.get("founder_reviews", [])
        existing[record.scrape_id] = row
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(list(existing.values()), indent=2) + "\n")


def host_matches_any(url: str, domains: tuple[str, ...]) -> bool:
    """Whether the URL's host is, or is a subdomain of, any listed domain.

    Suffix matching is anchored on a dot, so `notexample.edu` does not match
    `example.edu`. `www.` is stripped from both sides.
    """
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
    """Turn a search hit into a scrape `Target` with its provenance recorded.

    The key is a hash of the URL, so the same page discovered by two lanes
    gets two keys — lane-scoped by design, since the lanes write to separate
    candidate files.

    `operator_note` carries the query and rank that found it, which is what a
    reviewer needs to judge whether the hit is spurious. The snippet is
    truncated to 240 characters; it is untrusted text from the indexed page.
    """
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
    """Strip tags and collapse whitespace. Not sanitisation — it makes provider text readable, nothing more."""
    value = re.sub(r"<[^>]+>", "", value or "")
    return re.sub(r"\s+", " ", value).strip()


def _title_from_url(url: str) -> str:
    """Fall back to a title derived from the URL's last path segment.

    Used when the provider gave no title. Returns the whole URL when the
    path is empty, so a target always has something a reviewer can read.
    """
    path = urlsplit(url).path.strip("/").rsplit("/", 1)[-1]
    return path.replace("-", " ").replace("_", " ").strip().title() or url


def _host_label(url: str) -> str:
    """The hostname, used as the organisation when nothing better is known."""
    host = urlsplit(url).netloc.lower().removeprefix("www.")
    return host or "unknown"
