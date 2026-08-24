"""Fetch, extract, deduplicate, and write a review file.

The whole sweep, in one place, with the ordering that keeps it honest:

    registry target -> PoliteFetcher (robots + rate limit + raw archive)
                    -> to_blocks -> extractors -> ScrapedOpportunity
                    -> deduplicate -> data/opportunities.rutgers.candidates.json

**What this deliberately does not do:** write to
`data/opportunities.seed.json`. Nothing here promotes anything. The output is
a candidate file whose every row carries `review_status:
NEEDS_HUMAN_REVIEW`, and the existing `scripts/verify_seed.py` path stays the
only way a row becomes production data — after a human has read it.

Link discovery is off by default and, when enabled, expands only within
`registry.RUTGERS_DOMAINS`, one level deep, on anchors whose text or href
looks like funding. An external target is fetched at exactly the URL the
operator supplied and never crawled.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

from agent.scraping import extract
from agent.scraping.fetch import PoliteFetcher
from agent.scraping.models import (
    Evidence,
    FetchRecord,
    ScrapedOpportunity,
    ScrapeRun,
)
from agent.scraping.registry import TARGETS, Target, is_rutgers_domain

log = logging.getLogger("kairos.scraping.pipeline")

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
RAW_DIR = REPO_ROOT / "data" / "raw"
CANDIDATES_PATH = REPO_ROOT / "data" / "opportunities.rutgers.candidates.json"
RUN_LOG_PATH = REPO_ROOT / "data" / "raw" / "scrape_runs.jsonl"

#: Anchors worth following during discovery. Everything else is ignored, so a
#: run stays a handful of requests rather than a crawl of a university.
_FUNDING_ANCHOR = re.compile(
    r"\b(grant|grants|funding|fund|prize|prizes|award|awards|fellowship|"
    r"scholarship|competition|challenge|pitch|accelerator|incubator|"
    r"venture|startup|entrepreneur\w*|innovation|commercializ\w*|seed)\b",
    re.I,
)
MAX_DISCOVERED_LINKS = 12


# ── One target -> one record ─────────────────────────────────────────────────


def build_record(
    target: Target, text: str, record: FetchRecord, *, source_url: str | None = None
) -> ScrapedOpportunity:
    """Turn page text into a `ScrapedOpportunity`.

    Every field goes through `set_field`, so a field the extractors could not
    find lands in `unknown_fields` and nowhere else. There is no branch in
    this function that fills a value without an `Evidence` behind it.
    """
    url = source_url or record.final_url or target.url
    blocks = extract.to_blocks(text)

    organization, org_evidence = extract.find_organization(blocks, url, target.organization)

    opportunity = ScrapedOpportunity(
        scrape_id=f"{target.key}:{record.content_hash[:12] or uuid.uuid4().hex[:12]}",
        title=target.title,
        organization=organization,
        source_url=url,
        fetch=record,
        scraped_at=record.fetched_at,
    )
    opportunity.evidence["organization"] = org_evidence

    awards = extract.find_awards(blocks, url)
    opportunity.set_field("award_min", *_pair(awards.get("award_min")))
    opportunity.set_field("award_max", *_pair(awards.get("award_max")))
    opportunity.set_field("award_type", *_pair(awards.get("award_type")))
    opportunity.caveats.extend(awards.get("caveats", []))

    deadline = extract.find_deadline(blocks, url)
    if deadline is not None:
        verbatim, parsed, evidence = deadline
        opportunity.set_field("deadline", verbatim, evidence)
        opportunity.deadline_iso = parsed
    else:
        opportunity.mark_unknown("deadline")

    ambiguity = extract.deadline_is_ambiguous(blocks)
    if ambiguity:
        opportunity.caveats.append(ambiguity)

    opportunity.set_field("degree_levels", *_pair(extract.find_degree_levels(blocks, url)))
    opportunity.set_field("institution", *_pair(extract.find_institutions(blocks, url)))
    opportunity.set_field("applicant_type", *_pair(extract.find_applicant_types(blocks, url)))
    opportunity.set_field("equity_required", *_pair(extract.find_equity(blocks, url)))

    team = extract.find_team_size(blocks, url)
    opportunity.set_field("team_size_min", *_pair(team.get("team_size_min")))
    opportunity.set_field("team_size_max", *_pair(team.get("team_size_max")))

    opportunity.caveats.extend(extract.find_caveats(blocks, url))
    if target.operator_note:
        opportunity.caveats.append(f"[operator note] {target.operator_note}")
    if target.tier == "PROVIDED_EXTERNAL":
        opportunity.caveats.append(
            "[off-domain] This page is not on a Rutgers-owned domain. It was fetched "
            "because it was named explicitly in the target list, at exactly that URL, "
            "and it was not crawled."
        )

    # Never scraped, never inferred. Stated on every record so its emptiness
    # cannot be mistaken for "no reviews exist".
    opportunity.caveats.append(
        "[founder reviews] None. No target page publishes reviews from past student "
        "applicants, so this field is empty by construction rather than by omission. "
        "Anything here must be typed in by a human."
    )
    return opportunity


def _pair(found) -> tuple:
    """Normalise an extractor result into `(value, evidence)`."""
    if found is None:
        return (None, None)
    return found


def placeholder_record(target: Target) -> ScrapedOpportunity:
    """A target with no URL to fetch, recorded with everything UNKNOWN.

    An opportunity that is real but undocumented should appear in the review
    file as a job for a human, not vanish because a parser had nothing to
    parse.
    """
    now = datetime.now(timezone.utc)
    record = FetchRecord(
        url="",
        fetched_at=now,
        failure="NO_STABLE_URL: no application page was found for this program",
    )
    opportunity = ScrapedOpportunity(
        scrape_id=f"{target.key}:no-url",
        title=target.title,
        organization=target.organization,
        source_url="",
        fetch=record,
        scraped_at=now,
    )
    for name in (
        "award_type", "award_min", "award_max", "institution", "degree_levels",
        "applicant_type", "equity_required", "team_size_min", "team_size_max",
        "deadline",
    ):
        opportunity.mark_unknown(name)
    opportunity.caveats.append(f"[operator note] {target.operator_note}")
    opportunity.caveats.append(
        "[no source url] Nothing on this row was extracted from a page. Every field is "
        "UNKNOWN until someone finds the application URL and re-runs the scraper."
    )
    return opportunity


# ── Discovery, bounded and Rutgers-only ──────────────────────────────────────


def discover_links(html: str, base_url: str, limit: int = MAX_DISCOVERED_LINKS) -> list[str]:
    """Same-domain funding-shaped links, one level deep.

    Returns nothing at all for a non-Rutgers base URL. That is the rule from
    the brief, enforced here rather than trusted to a caller.
    """
    if not is_rutgers_domain(base_url):
        return []

    soup = BeautifulSoup(html, "html.parser")
    base_host = urlsplit(base_url).netloc.lower()
    found: list[str] = []
    seen: set[str] = {base_url.rstrip("/")}

    for anchor in soup.find_all("a", href=True):
        href = anchor["href"].strip()
        if href.startswith(("mailto:", "tel:", "javascript:", "#")):
            continue
        absolute = urljoin(base_url, href).split("#")[0].rstrip("/")
        if absolute in seen:
            continue
        if urlsplit(absolute).netloc.lower() != base_host:
            continue
        label = f"{anchor.get_text(' ', strip=True)} {absolute}"
        if not _FUNDING_ANCHOR.search(label):
            continue
        seen.add(absolute)
        found.append(absolute)
        if len(found) >= limit:
            break
    return found


# ── Deduplication ────────────────────────────────────────────────────────────


def deduplicate(records: list[ScrapedOpportunity]) -> tuple[list[ScrapedOpportunity], int]:
    """Collapse rows describing the same program.

    Matching is `ScrapedOpportunity.same_program`: identical page content, or
    the same title reached on the same host or under the same organisation.
    The survivor is the one with the most evidence — a thinner duplicate never
    overwrites a richer record — and the loser's URL is kept as a caveat so
    nothing is silently discarded.
    """
    kept: list[ScrapedOpportunity] = []
    merged = 0

    for record in records:
        match_index = next(
            (i for i, existing in enumerate(kept) if existing.same_program(record)), None
        )
        if match_index is None:
            kept.append(record)
            continue

        merged += 1
        existing = kept[match_index]
        winner, loser = (
            (record, existing)
            if len(record.evidence) > len(existing.evidence)
            else (existing, record)
        )
        if loser.source_url and loser.source_url != winner.source_url:
            winner.caveats.append(
                f"[duplicate merged] The same program was also found at "
                f"{loser.source_url} (scraped {loser.scraped_at.isoformat()})."
            )
        kept[match_index] = winner

    return kept, merged


# ── The sweep ────────────────────────────────────────────────────────────────


def scrape(
    targets: list[Target] | None = None,
    *,
    raw_dir: Path = RAW_DIR,
    allow_js: bool = False,
    discover: bool = False,
    fetcher: PoliteFetcher | None = None,
) -> tuple[list[ScrapedOpportunity], ScrapeRun]:
    """Run the sweep. Returns `(records, run)`; failures live on the run."""
    targets = list(targets if targets is not None else TARGETS)
    fetcher = fetcher or PoliteFetcher(raw_dir)
    run = ScrapeRun(run_id=f"scrape_{uuid.uuid4().hex[:12]}")
    records: list[ScrapedOpportunity] = []

    for target in targets:
        run.targets_attempted += 1

        if not target.url:
            records.append(placeholder_record(target))
            run.notes.append(f"{target.key}: no URL to fetch — recorded as all-UNKNOWN")
            continue

        text, record = fetcher.fetch(target.url, allow_js=allow_js and target.requires_js)

        if record.failure or not text:
            run.failures.append(record)
            log.info("target_failed", extra={"key": target.key, "failure": record.failure})
            continue

        run.pages_fetched += 1
        records.append(build_record(target, text, record))

        if discover and target.tier == "RUTGERS" and record.raw_path:
            html = Path(record.raw_path).read_text(encoding="utf-8")
            for link in discover_links(html, record.final_url or target.url):
                child_text, child_record = fetcher.fetch(link)
                if child_record.failure or not child_text:
                    run.failures.append(child_record)
                    continue
                run.pages_fetched += 1
                child_target = Target(
                    key=f"{target.key}__discovered",
                    title=_page_title(child_text, link),
                    organization=target.organization,
                    url=link,
                    tier=target.tier,
                    priority=target.priority,
                    operator_note=(
                        f"Discovered by following a funding-shaped link from "
                        f"{target.url}. Nobody has read this page yet."
                    ),
                )
                records.append(build_record(child_target, child_text, child_record))

    records, merged = deduplicate(records)
    run.duplicates_merged = merged
    run.opportunities_found = len(records)
    run.finished_at = datetime.now(timezone.utc)
    return records, run


def _page_title(text: str, url: str) -> str:
    for line in text.split("\n"):
        line = line.strip()
        if 3 < len(line) < 120:
            return line.split("|")[0].strip()
    return urlsplit(url).path.rsplit("/", 1)[-1] or url


# ── Output ───────────────────────────────────────────────────────────────────


def write_candidates(
    records: list[ScrapedOpportunity],
    run: ScrapeRun,
    path: Path = CANDIDATES_PATH,
    run_log: Path = RUN_LOG_PATH,
) -> Path:
    """Write the review file. Never `opportunities.seed.json`.

    Merges with whatever is already in the file rather than replacing it, so
    a human's `review_status` decisions and hand-typed founder reviews
    survive the next scrape. A re-scraped row refreshes the scraped fields
    and keeps the human ones.
    """
    path = Path(path)
    existing: dict[str, dict] = {}
    if path.exists():
        for row in json.loads(path.read_text()):
            existing[row.get("scrape_id", "")] = row

    payload = []
    for record in records:
        row = json.loads(record.model_dump_json())
        previous = existing.get(record.scrape_id)
        if previous:
            # Human decisions are never overwritten by a scraper.
            row["review_status"] = previous.get("review_status", row["review_status"])
            row["founder_reviews"] = previous.get("founder_reviews", [])
        payload.append(row)

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=False) + "\n")

    run_log = Path(run_log)
    run_log.parent.mkdir(parents=True, exist_ok=True)
    with run_log.open("a") as handle:
        handle.write(run.model_dump_json() + "\n")
    return path
