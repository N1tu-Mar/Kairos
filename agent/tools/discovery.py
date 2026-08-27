"""Sources of funding opportunities (Section 5).

Three tiers, in priority order:

*   **Tier 1 — seeded catalog.** Hand-curated, works offline, demos
    reliably. The floor.
*   **Tier 2 — Grants.gov live.** Real unauthenticated API. Its main demo
    value is showing the agent correctly *rejecting* federal R&D money for a
    sophomore with an MVP, which is better proof of judgment than a match.
*   **Tier 3 — AgentCore Browser.** Behind a feature flag, degrades to
    Tiers 1+2 without breaking. Not architected around.

Every external call lives in one client class, with a timeout and a logged
failure. A source that dies is recorded in `RunReport.sources_failed` and the
run continues on the rest. A silent partial run is a lie (Section 9, rule 6).

## Grants.gov response shape

Confirmed by real calls on 2026-08-22 and 2026-08-26, recorded in
`tests/fixtures/grants_gov_*.json`. Nothing below was written from memory:

    POST /search2          -> {errorcode, msg, token, data:{hitCount, startRecord, oppHits:[...]}}
      oppHit: id, number, title, agency, agencyCode, openDate,
              closeDate, oppStatus, docType, cfdaList
      closeDate and openDate are "MM/DD/YYYY", or "" when absent.

    Pagination (documented at grants.gov/api/common/search2 and confirmed
    live on 2026-08-26, fixtures grants_gov_search2_page{1,2}.json):
    `rows` is the page size and `startRecordNum` the zero-based offset;
    `data.startRecord` echoes the offset and `data.hitCount` is the total.
    The documentation lists no server-side posted-date filter, so `since`
    is applied client-side against `openDate`.

    POST /fetchOpportunity -> {errorcode, msg, data:{..., synopsis:{...}}}
      synopsis: awardFloor, awardCeiling (digit strings),
                responseDateStr ("YYYY-MM-DD-HH-MM-SS"),
                applicantEligibilityDesc (HTML-escaped HTML),
                applicantTypes ([{id, description}]),
                synopsisDesc (HTML), costSharing (bool)

Note what is deliberately *not* mapped: `applicantEligibilityDesc` is free
text, so it becomes an `ExtractedCriterion` (verbatim, quotable) and never an
`EligibilityRules` field. Regexing a degree requirement out of federal prose
and feeding it to the deterministic filter would put a parser's guess where a
structured fact belongs. Grants.gov rows therefore arrive with mostly-UNKNOWN
eligibility, which is the honest answer.
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Protocol

import httpx

from agent.models import (
    EligibilityRules,
    ExtractedCriterion,
    Opportunity,
    SourceFailure,
    SourceName,
)
from agent.sanitize import ingest

log = logging.getLogger("kairos.discovery")

GRANTS_GOV_DETAIL_URL = "https://www.grants.gov/search-results-detail/{id}"


class SourceError(RuntimeError):
    """A source failed. Surfaced in the run report, never swallowed."""


class Source(Protocol):
    name: SourceName

    def fetch(self, since: datetime) -> list[Opportunity]: ...


# ── Tier 1: seeded catalog ───────────────────────────────────────────────────


#: Written by humans and by `scripts/verify_seed.py`, not part of the model.
#: Stripped explicitly rather than by relaxing `extra="forbid"` — strict
#: validation is what catches a typo in a hand-curated row, and that is worth
#: keeping.
_CURATION_KEYS = ("verification_note",)


def _strip_curation_keys(row: dict) -> dict:
    return {
        k: v
        for k, v in row.items()
        if not k.startswith("_") and k not in _CURATION_KEYS
    }


class SeedCatalog:
    """The curated floor.

    Every row needs a `source_url` someone actually opened and a
    `verified_at` timestamp. A row without them carries `verified: false` and
    is **excluded from runs** unless explicitly allowed — a plausible-sounding
    grant that does not exist is worse than twenty fewer entries.
    """

    name: SourceName = "seed"

    def __init__(self, path: Path, allow_unverified: bool = False) -> None:
        self.path = Path(path)
        self.allow_unverified = allow_unverified

    def fetch(self, since: datetime | None = None) -> list[Opportunity]:
        if not self.path.exists():
            raise SourceError(f"seed catalog not found at {self.path}")
        try:
            rows = json.loads(self.path.read_text())
        except json.JSONDecodeError as exc:
            raise SourceError(f"seed catalog is not valid JSON: {exc}") from exc

        opportunities: list[Opportunity] = []
        skipped_unverified = 0

        for row in rows:
            opportunity = Opportunity.model_validate(
                {**_strip_curation_keys(row), "source": "seed"}
            )
            if not opportunity.verified and not self.allow_unverified:
                skipped_unverified += 1
                continue
            opportunities.append(opportunity)

        if skipped_unverified:
            log.info(
                "seed_catalog_excluded_unverified",
                extra={"count": skipped_unverified, "path": str(self.path)},
            )
        return opportunities


# ── Tier 2: Grants.gov ───────────────────────────────────────────────────────


def _parse_close_date(raw: str | None) -> date | None:
    """`closeDate` is MM/DD/YYYY. Empty string means no stated deadline."""
    if not raw or not raw.strip():
        return None
    try:
        return datetime.strptime(raw.strip(), "%m/%d/%Y").date()
    except ValueError:
        log.warning("grants_gov_unparseable_close_date", extra={"raw": raw})
        return None


def _parse_response_date(raw: str | None) -> date | None:
    """`responseDateStr` is YYYY-MM-DD-HH-MM-SS."""
    if not raw or not raw.strip():
        return None
    try:
        return datetime.strptime(raw.strip(), "%Y-%m-%d-%H-%M-%S").date()
    except ValueError:
        log.warning("grants_gov_unparseable_response_date", extra={"raw": raw})
        return None


def _parse_money(raw: Any) -> int | None:
    """`awardFloor` / `awardCeiling` arrive as digit strings, or "" / "0"."""
    if raw in (None, "", "0", 0):
        return None
    try:
        value = int(float(str(raw).replace(",", "").replace("$", "")))
    except (TypeError, ValueError):
        return None
    return value or None


class GrantsGovClient:
    """The single place a Grants.gov network call happens.

    One timeout, one place to log a failure, one place to record a fixture.
    """

    def __init__(self, base_url: str, timeout_s: float = 15.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s

    def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        url = f"{self.base_url}/{path}"
        try:
            response = httpx.post(url, json=body, timeout=self.timeout_s)
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, json.JSONDecodeError) as exc:
            raise SourceError(f"{path} failed: {type(exc).__name__}: {exc}") from exc

        # The API returns HTTP 200 with an in-band error code. Checking the
        # status alone would read a failure as a success.
        if payload.get("errorcode") not in (0, "0"):
            raise SourceError(f"{path} returned errorcode={payload.get('errorcode')}: {payload.get('msg')}")
        return payload.get("data") or {}

    def search(
        self,
        keyword: str,
        rows: int = 25,
        statuses: str = "posted",
        start_record: int = 0,
    ) -> tuple[list[dict], int]:
        """One page. Returns `(hits, hit_count)`.

        `rows` and `startRecordNum` are the documented pagination parameters
        (grants.gov/api/common/search2), confirmed live on 2026-08-26.
        """
        data = self._post(
            "search2",
            {
                "keyword": keyword,
                "rows": rows,
                "oppStatuses": statuses,
                "startRecordNum": start_record,
            },
        )
        return data.get("oppHits") or [], int(data.get("hitCount") or 0)

    def fetch_opportunity(self, opportunity_id: str) -> dict[str, Any]:
        return self._post("fetchOpportunity", {"opportunityId": opportunity_id})


def keywords_for_profile(profile) -> tuple[str, ...]:
    """Search keywords derived from structured profile fields.

    Deterministic and conservative: the base set always runs, and the profile
    can only *add* terms that its structured fields justify. Free text never
    becomes a query — that would let a prompt injection steer discovery.
    """
    keywords = ["student", "entrepreneurship"]
    degree_terms = {
        "undergrad": "undergraduate",
        "masters": "graduate student",
        "phd": "graduate student",
        "postdoc": "postdoctoral",
    }
    term = degree_terms.get(getattr(profile, "degree_level", ""))
    if term:
        keywords.append(term)
    if getattr(profile, "entity_type", "none") != "none":
        keywords.append("small business innovation")
    seen: set[str] = set()
    return tuple(k for k in keywords if not (k in seen or seen.add(k)))


def _parse_open_date(raw: str | None) -> date | None:
    """`openDate` is MM/DD/YYYY like `closeDate` (fixture-confirmed)."""
    return _parse_close_date(raw)


class GrantsGovSource:
    """Tier 2. Federal money, mostly wrong for a student founder — and that
    is exactly what makes it a good demonstration of judgment.

    Pagination walks `startRecordNum` per keyword until `max_per_keyword`
    rows or the reported `hitCount`, whichever is first. Duplicates across
    pages and keywords collapse on opportunity id. `since` filters
    client-side on `openDate` because the documented API has no posted-date
    filter. Hydration is bounded (`hydrate_concurrency` threads) and every
    page or detail failure lands in `partial_failures`, which
    `discover_opportunities` folds into the run report — a partial source is
    reported, never smoothed over.
    """

    name: SourceName = "grants_gov"

    def __init__(
        self,
        client: GrantsGovClient,
        keywords: tuple[str, ...] = ("student", "undergraduate", "entrepreneurship"),
        rows_per_page: int = 25,
        max_per_keyword: int = 75,
        hydrate: bool = True,
        hydrate_concurrency: int = 4,
        skip_past_deadlines: bool = True,
    ) -> None:
        self.client = client
        self.keywords = keywords
        self.rows_per_page = max(1, rows_per_page)
        self.max_per_keyword = max(1, max_per_keyword)
        self.hydrate = hydrate
        self.hydrate_concurrency = max(1, hydrate_concurrency)
        self.skip_past_deadlines = skip_past_deadlines
        #: Per-page / per-detail failures from the most recent fetch. Read by
        #: `discover_opportunities` after each fetch; a dead page inside an
        #: otherwise-working source must still reach the run report.
        self.partial_failures: list[SourceFailure] = []

    # ── paging ───────────────────────────────────────────────────────────

    def _search_all_pages(self, keyword: str) -> list[dict]:
        hits: list[dict] = []
        start = 0
        while len(hits) < self.max_per_keyword:
            rows = min(self.rows_per_page, self.max_per_keyword - len(hits))
            try:
                page, hit_count = self.client.search(
                    keyword, rows=rows, start_record=start
                )
            except SourceError as exc:
                # A dead page is recorded; earlier pages are kept.
                self.partial_failures.append(
                    SourceFailure(
                        source=self.name,
                        detail=f"search2 keyword={keyword!r} startRecordNum={start}: {exc}",
                    )
                )
                break
            if not page:
                break
            # Trim in case the server returns more rows than asked for; the
            # cap is enforced here, not trusted to the API.
            hits.extend(page[: self.max_per_keyword - len(hits)])
            start += len(page)
            if start >= hit_count:
                break
        return hits

    # ── the fetch ────────────────────────────────────────────────────────

    def fetch(self, since: datetime | None = None) -> list[Opportunity]:
        self.partial_failures = []
        today = datetime.now(tz=timezone.utc).date()
        since_date = since.date() if since else None

        seen: dict[str, dict] = {}
        for keyword in self.keywords:
            for hit in self._search_all_pages(keyword):
                seen.setdefault(str(hit["id"]), hit)

        selected: dict[str, dict] = {}
        skipped_old, skipped_closed = 0, 0
        for opp_id, hit in seen.items():
            open_date = _parse_open_date(hit.get("openDate"))
            close_date = _parse_close_date(hit.get("closeDate"))
            # No server-side date filter exists, so `since` applies here: a
            # row opened before the watermark is not "new or updated". A row
            # with no openDate is kept — absence of a date is not evidence
            # that the row is old.
            if since_date and open_date and open_date < since_date:
                skipped_old += 1
                continue
            # A deadline already in the past cannot be applied to; skipping
            # it before hydration saves a detail call per dead row.
            if self.skip_past_deadlines and close_date and close_date < today:
                skipped_closed += 1
                continue
            selected[opp_id] = hit
        if skipped_old or skipped_closed:
            log.info(
                "grants_gov_date_filtered",
                extra={"opened_before_since": skipped_old, "deadline_passed": skipped_closed},
            )

        details: dict[str, dict] = {}
        if self.hydrate and selected:
            from concurrent.futures import ThreadPoolExecutor

            def _detail(opp_id: str) -> tuple[str, dict | None, str | None]:
                try:
                    return opp_id, self.client.fetch_opportunity(opp_id), None
                except SourceError as exc:
                    return opp_id, None, str(exc)

            with ThreadPoolExecutor(max_workers=self.hydrate_concurrency) as pool:
                for opp_id, detail, error in pool.map(_detail, selected):
                    if error is not None:
                        # One dead detail call does not kill the whole source,
                        # but it is reported, not swallowed.
                        log.warning(
                            "grants_gov_detail_failed",
                            extra={"id": opp_id, "error": error},
                        )
                        self.partial_failures.append(
                            SourceFailure(
                                source=self.name,
                                detail=f"fetchOpportunity id={opp_id}: {error}",
                            )
                        )
                    else:
                        details[opp_id] = detail

        return [
            self.to_opportunity(hit, details.get(opp_id, {}))
            for opp_id, hit in selected.items()
        ]

    @staticmethod
    def to_opportunity(hit: dict, detail: dict | None = None) -> Opportunity:
        """Map verified response fields onto our model. Nothing inferred."""
        synopsis = ((detail or {}).get("synopsis")) or {}

        title, _ = ingest(hit.get("title", ""))
        description, _ = ingest(synopsis.get("synopsisDesc") or "")

        criteria: list[ExtractedCriterion] = []
        eligibility_text = synopsis.get("applicantEligibilityDesc") or ""
        if eligibility_text:
            cleaned, _ = ingest(eligibility_text)
            criteria.append(
                ExtractedCriterion(
                    text=cleaned,
                    source_doc=f"grants.gov/{hit['id']}#applicantEligibilityDesc",
                )
            )
        for applicant_type in synopsis.get("applicantTypes") or []:
            criteria.append(
                ExtractedCriterion(
                    text=str(applicant_type.get("description", "")),
                    source_doc=f"grants.gov/{hit['id']}#applicantTypes",
                )
            )

        deadline = _parse_close_date(hit.get("closeDate")) or _parse_response_date(
            synopsis.get("responseDateStr")
        )

        return Opportunity(
            id=f"grants_gov:{hit['id']}",
            title=title,
            funder=hit.get("agency") or hit.get("agencyCode") or "unknown",
            source="grants_gov",
            source_url=GRANTS_GOV_DETAIL_URL.format(id=hit["id"]),
            award_min=_parse_money(synopsis.get("awardFloor")),
            award_max=_parse_money(synopsis.get("awardCeiling")),
            deadline=deadline,
            rolling=False,
            # Every structured rule stays None: the source states eligibility
            # in prose, and prose is not a structured fact.
            eligibility=EligibilityRules(),
            criteria=criteria,
            description_excerpt=description,
            # Live API data is real by construction; `verified` is about
            # human curation of the seed catalog, not about liveness.
            verified=True,
            verified_at=datetime.now(tz=None).astimezone(),
        )


# ── The tool ─────────────────────────────────────────────────────────────────


def discover_opportunities(
    sources: list[Source], since: datetime
) -> tuple[list[Opportunity], list[SourceFailure]]:
    """Pull new/updated opportunities from all enabled sources.

    Returns everything that answered, plus a record of everything that did
    not. Deduplicated by opportunity id, first source wins — the seeded
    catalog is curated and should beat a live row describing the same
    program.
    """
    found: dict[str, Opportunity] = {}
    failures: list[SourceFailure] = []

    for source in sources:
        try:
            for opportunity in source.fetch(since):
                found.setdefault(opportunity.id, opportunity)
        except Exception as exc:  # noqa: BLE001 — a dead source must not kill the run
            log.warning("source_failed", extra={"source": source.name, "error": str(exc)})
            failures.append(
                SourceFailure(source=source.name, detail=f"{type(exc).__name__}: {exc}")
            )
        # A source that answered but lost pages or detail calls is a partial
        # source. Its failures reach the run report too (Section 9, rule 6).
        failures.extend(getattr(source, "partial_failures", []))

    return list(found.values()), failures
