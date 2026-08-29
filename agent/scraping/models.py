"""Contracts for the scraping pipeline.

Kept separate from `agent/models.py` on purpose. That file is the contract
between the deterministic layer and the model layer of the running agent.
This one describes something upstream and more provisional: what a scraper
saw on a page at a moment in time, before any human has agreed it is true.

Three rules shape every model here.

1.  **Never infer a missing field.** A field the page did not state is
    `None`, and its name is listed in `unknown_fields`. There is no
    "probably undergraduate" tier. `set_field()` is the only way to populate
    an eligibility field and it refuses to do so without evidence.
2.  **Every populated field carries the sentence it came from**, with the
    URL that sentence was on. A value with no `Evidence` is not a fact, it
    is a guess with better formatting.
3.  **Nothing here is production data.** `review_status` starts at
    `NEEDS_HUMAN_REVIEW` and only a human moves it. These records land in a
    candidate file, never in `opportunities.seed.json`.
"""

from __future__ import annotations

import hashlib
import re
from datetime import date, datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

#: Fields that describe who may apply. Every one of them is allowed to be
#: UNKNOWN, and each must carry evidence when it is not.
ELIGIBILITY_FIELDS = (
    "degree_levels",
    "institution",
    "applicant_type",
    "equity_required",
    "team_size_min",
    "team_size_max",
    "deadline",
    "award_min",
    "award_max",
    "award_type",
)

#: Controlled vocabulary. The scraper matches page text against these; a
#: phrase that maps to nothing leaves the field UNKNOWN rather than inventing
#: a new level.
DegreeLevel = Literal[
    "undergraduate", "graduate", "masters", "mba", "phd", "postdoc", "alumni"
]

ReviewStatus = Literal["NEEDS_HUMAN_REVIEW", "ACCEPTED", "REJECTED"]


def _now() -> datetime:
    """Timezone-aware UTC now, for `scraped_at` and fetch timestamps."""
    return datetime.now(timezone.utc)


class Evidence(BaseModel):
    """The verbatim span a field was read from, and where it was read.

    `text` is quoted, never paraphrased and never summarised. If a span
    cannot be located on the page, the caller must leave the field UNKNOWN —
    there is no inferred tier (mirrors `agent.models.SourceSpan`).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    text: str = Field(description="Verbatim sentence or clause from the page.")
    source_url: str
    #: How it was found, e.g. "regex:prize_sentence". Written so a reviewer
    #: can tell a strong signal from a weak one without re-reading the code.
    method: str = ""


class FetchRecord(BaseModel):
    """What happened when we asked for the page. Failures are recorded, not swallowed."""

    model_config = ConfigDict(extra="forbid")

    url: str
    final_url: str = ""
    status_code: int | None = None
    robots_allowed: bool = True
    robots_url: str = ""
    crawl_delay_s: float | None = None
    fetched_at: datetime = Field(default_factory=_now)
    content_hash: str = ""
    raw_path: str = ""
    renderer: Literal["httpx", "playwright"] = "httpx"
    #: Set when the fetch did not produce usable text. `NEEDS_JS` means the
    #: page returned HTML that says it requires JavaScript.
    failure: str | None = None
    bytes: int = 0


class FounderReview(BaseModel):
    """A past applicant's account of the program.

    **Never scraped.** None of the target pages publish student reviews, and
    inventing one would be the worst possible failure for a file whose whole
    job is to help someone decide where to spend eight hours. This list stays
    empty until a human types into it.
    """

    model_config = ConfigDict(extra="forbid")

    text: str
    attribution: str = Field(description='e.g. "2025 finalist, RBS" — or "anonymous"')
    source_url: str | None = None
    added_by: str = Field(description="The human who entered this. Never a scraper.")
    added_at: datetime = Field(default_factory=_now)


class ScrapedOpportunity(BaseModel):
    """One funding opportunity as a page stated it.

    The shape follows the schema the request specified. Two additions carry
    the honesty requirements: `unknown_fields` names every field the page did
    not answer, and `evidence` holds the span behind every field it did.
    """

    model_config = ConfigDict(extra="forbid")

    scrape_id: str
    title: str
    organization: str
    source_url: str

    award_type: str | None = None
    award_min: int | None = None
    award_max: int | None = None

    institution: list[str] | None = None
    degree_levels: list[str] | None = None
    applicant_type: list[str] | None = None
    equity_required: bool | None = None
    team_size_min: int | None = None
    team_size_max: int | None = None

    #: Verbatim as written on the page ("Applications open November 1").
    deadline: str | None = None
    #: Set only when `deadline` parses to one unambiguous calendar date.
    deadline_iso: date | None = None

    evidence: dict[str, Evidence] = Field(default_factory=dict)
    #: Every field the page did not state. Rendered as UNKNOWN, never guessed.
    unknown_fields: list[str] = Field(default_factory=list)

    #: Free-text notes for the reviewer: conditional eligibility, indirect
    #: access routes, anything that does not fit a structured field.
    caveats: list[str] = Field(default_factory=list)

    founder_reviews: list[FounderReview] = Field(default_factory=list)

    fetch: FetchRecord
    scraped_at: datetime = Field(default_factory=_now)
    review_status: ReviewStatus = "NEEDS_HUMAN_REVIEW"

    # ── The only supported way to populate a field ────────────────────────

    def set_field(self, name: str, value, evidence: Evidence | None) -> bool:
        """Set `name` to `value`, but only with evidence behind it.

        Returns True when the field was set. A `None` value or a missing
        `Evidence` leaves the field UNKNOWN and says so. This is the single
        chokepoint that makes "never infer missing eligibility" mechanical
        rather than aspirational.
        """
        if value is None or evidence is None:
            self.mark_unknown(name)
            return False
        setattr(self, name, value)
        self.evidence[name] = evidence
        if name in self.unknown_fields:
            self.unknown_fields.remove(name)
        return True

    def mark_unknown(self, name: str) -> None:
        """Record a field as unstated by the page. Idempotent, so calling it twice does not duplicate the entry."""
        if name not in self.unknown_fields:
            self.unknown_fields.append(name)

    @property
    def is_unknown(self) -> dict[str, bool]:
        """Per-field UNKNOWN flags for every eligibility field.

        Covers `ELIGIBILITY_FIELDS` rather than only the fields anyone touched,
        so a field nobody attempted reads as unknown rather than missing from the
        map entirely.
        """
        return {f: f in self.unknown_fields for f in ELIGIBILITY_FIELDS}

    @property
    def dedupe_key(self) -> str:
        """Normalised title. The coarse half of the duplicate test.

        Deliberately *not* title-plus-organisation. The organisation is
        extracted from the page, so a rich parse and a thin parse of the same
        program disagree about it — and a duplicate test that keys on a field
        the parser might miss stops detecting exactly the duplicates it
        exists to catch. `same_program` supplies the discriminator.
        """
        return _slug(self.title)

    @property
    def host(self) -> str:
        """Lowercased hostname of `source_url`, without `www.`.

        Used as one of the three duplicate signals in `same_program`. Stripping
        `www.` means `www.example.edu` and `example.edu` count as one host;
        subdomains still differ, so `grants.example.edu` does not match.
        """
        from urllib.parse import urlsplit

        return urlsplit(self.source_url).netloc.lower().removeprefix("www.")

    def same_program(self, other: "ScrapedOpportunity") -> bool:
        """Do these two rows describe the same funding opportunity?

        Any one of three signals is enough, because each fails differently:
        an identical page hash catches the same URL reached twice, a shared
        host catches one program with two paths, and a shared organisation
        catches the same program hosted in two places.
        """
        digest = self.fetch.content_hash
        if digest and digest == other.fetch.content_hash:
            return True
        if self.dedupe_key != other.dedupe_key:
            return False
        if self.host and self.host == other.host:
            return True
        return _slug(self.organization) == _slug(other.organization)


def _slug(value: str) -> str:
    """Lowercase hyphenated slug — the normalisation behind title and organisation comparison."""
    return re.sub(r"[^a-z0-9]+", "-", (value or "").lower()).strip("-")


def content_hash(text: str) -> str:
    """Stable hash of page text, for change detection between scrapes."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class ScrapeRun(BaseModel):
    """The whole sweep. A partial run that looks complete is a lie."""

    model_config = ConfigDict(extra="forbid")

    run_id: str
    started_at: datetime = Field(default_factory=_now)
    finished_at: datetime | None = None

    targets_attempted: int = 0
    pages_fetched: int = 0
    opportunities_found: int = 0
    duplicates_merged: int = 0

    #: Targets that produced nothing, with the reason. Never omitted.
    failures: list[FetchRecord] = Field(default_factory=list)
    #: Targets skipped before any request was made, e.g. robots.txt disallow.
    skipped: list[dict] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)

    def headline(self) -> str:
        """One line of counters for the sweep, computed in Python.

        Same shape as `RunReport.headline` and for the same reason: the number
        that matters is how much was attempted versus how much survived, and a
        sweep that failed halfway must not read as a complete one.
        """
        return (
            f"Attempted {self.targets_attempted}. Fetched {self.pages_fetched}. "
            f"Extracted {self.opportunities_found}. "
            f"Merged {self.duplicates_merged}. Failed {len(self.failures)}."
        )
