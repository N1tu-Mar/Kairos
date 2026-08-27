"""Campus discovery as a runtime source, with the review boundary intact.

`agent/scraping/` collects funding pages a university publishes and writes
`data/opportunities.rutgers.candidates.json`. Until now nothing in a Scout
run could see that file, and `KAIROS_ENABLE_BROWSER` set a flag nobody read.

This module connects the two without dissolving the wall between them:

    scraper  ->  candidates file  ->  [a human sets review_status]  ->  Scout

**Only `review_status == "ACCEPTED"` rows become opportunities.** A row the
scraper wrote five seconds ago is `NEEDS_HUMAN_REVIEW`, and a
`NEEDS_HUMAN_REVIEW` row is invisible to the runtime no matter how good its
evidence looks. That is the whole point of the flag existing: turning the
source on adds *reviewed* campus rows, never fresh parser output.

Three behaviours follow from that, and each is tested:

*   **Disabled is silent, not broken.** The source returns nothing and the
    run completes on its other sources.
*   **Missing or malformed data is a reported failure, not an empty result.**
    An empty list and a file that failed to parse look identical in a run
    report unless one of them raises, so the malformed one raises.
*   **A live sweep is opt-in twice.** Scraping during a run happens only when
    the flag *and* `allow_live_scrape` are set, it goes through the same
    `PoliteFetcher` (robots.txt fails closed, per-host rate limit, raw
    archive), and what it writes is the review file. Nothing it collects can
    reach the same run's recommendations, because the rows it writes are
    unreviewed by construction.

Eligibility is mapped through `agent.tools.extraction`, so a scraped field
still has to survive the same verification the model-facing boundary uses:
controlled vocabulary, negation, exception clauses, and conflicts. A row
whose evidence does not support a field arrives with that field UNKNOWN.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from agent.models import (
    ExtractedCriterion,
    Opportunity,
    SourceFailure,
    SourceName,
)
from agent.tools.discovery import SourceError
from agent.tools.extraction import (
    EligibilityClaim,
    EligibilityExtraction,
    extract_and_verify,
)

log = logging.getLogger("kairos.discovery.campus")

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
CAMPUS_CANDIDATES = REPO_ROOT / "data" / "opportunities.rutgers.candidates.json"

#: The scraper's vocabulary is the page's; the runtime's is the profile's.
#: A term with no mapping is dropped rather than guessed — "alumni" is not a
#: degree level, and inventing one would put a wrong value where the
#: deterministic filter reads.
_DEGREE_MAP = {
    "undergraduate": ["undergrad"],
    "graduate": ["masters", "phd"],
    "masters": ["masters"],
    "mba": ["masters"],
    "phd": ["phd"],
    "postdoc": ["postdoc"],
}


def _degree_levels(values: list[str] | None) -> list[str] | None:
    if not values:
        return None
    mapped: list[str] = []
    for value in values:
        for level in _DEGREE_MAP.get(str(value).strip().lower(), []):
            if level not in mapped:
                mapped.append(level)
    return mapped or None


def _claims(row: dict) -> list[EligibilityClaim]:
    """Turn a scraped row's evidence-backed fields into extraction claims.

    A field with no entry in `evidence` produces no claim, so it stays
    UNKNOWN. This is the same rule `ScrapedOpportunity.set_field` enforces
    one layer earlier, re-applied here rather than trusted.
    """
    evidence = row.get("evidence") or {}
    claims: list[EligibilityClaim] = []

    def add(field: str, value, evidence_key: str) -> None:
        span = evidence.get(evidence_key)
        if value is None or not span or not span.get("text"):
            return
        claims.append(
            EligibilityClaim(
                field=field,
                value=value,
                evidence=span["text"],
                source_ref=span.get("source_url", row.get("source_url", "")),
            )
        )

    add("degree_levels", _degree_levels(row.get("degree_levels")), "degree_levels")
    add("institutions", row.get("institution"), "institution")
    add("min_team_size", row.get("team_size_min"), "team_size_min")
    add("max_team_size", row.get("team_size_max"), "team_size_max")
    add("takes_equity", row.get("equity_required"), "equity_required")
    return claims


def _source_text(row: dict) -> str:
    """What the claims are verified against.

    The archived page if it is still on disk — that is the honest source —
    and otherwise the row's own evidence spans joined together. The fallback
    cannot catch a fabricated span (it is made of the spans), but it still
    enforces vocabulary, polarity, exception and conflict checks, and the
    degraded mode is logged rather than hidden.
    """
    raw_path = ((row.get("fetch") or {}).get("raw_path")) or ""
    if raw_path:
        path = Path(raw_path)
        if path.exists():
            try:
                return path.read_text(encoding="utf-8", errors="ignore")
            except OSError as exc:
                log.warning(
                    "campus_archive_unreadable",
                    extra={"path": raw_path, "error": str(exc)},
                )
    log.info(
        "campus_verifying_against_evidence_spans",
        extra={"scrape_id": row.get("scrape_id", "")},
    )
    return "\n".join(
        span.get("text", "") for span in (row.get("evidence") or {}).values()
    )


def to_opportunity(row: dict) -> Opportunity:
    """Map one ACCEPTED scraped row onto the runtime contract."""
    source_url = row.get("source_url", "")
    rules, verified = extract_and_verify(
        EligibilityExtraction(claims=_claims(row)), _source_text(row)
    )
    if verified.dropped:
        log.info(
            "campus_claims_dropped",
            extra={
                "scrape_id": row.get("scrape_id", ""),
                "reasons": sorted({d.reason for d in verified.dropped}),
            },
        )

    criteria = [
        ExtractedCriterion(
            text=span.get("text", ""),
            source_doc=f"{span.get('source_url', source_url)}#{field}",
        )
        for field, span in (row.get("evidence") or {}).items()
        if span.get("text")
    ]

    scraped_at = row.get("scraped_at")
    return Opportunity(
        id=f"campus:{row.get('scrape_id', '')}",
        title=row.get("title", ""),
        funder=row.get("organization", ""),
        source="browser",
        source_url=source_url,
        award_min=row.get("award_min"),
        award_max=row.get("award_max"),
        # `deadline_iso` is set only where the page gave one unambiguous
        # date. A verbatim "Dec. 21st" with no year stays UNKNOWN here too.
        deadline=row.get("deadline_iso"),
        rolling=False,
        eligibility=rules,
        criteria=criteria,
        description_excerpt="",
        # A human read this row and accepted it. That is what `verified`
        # means in this codebase — human curation, not liveness.
        verified=True,
        verified_at=datetime.fromisoformat(scraped_at.replace("Z", "+00:00"))
        if isinstance(scraped_at, str) and scraped_at
        else datetime.now(tz=timezone.utc),
    )


class CampusDiscoverySource:
    """Tier 3. Reviewed campus rows, behind `KAIROS_ENABLE_BROWSER`.

    Disabled by default and disabled cleanly: `fetch` returns nothing and the
    run proceeds on its other sources.
    """

    name: SourceName = "browser"

    def __init__(
        self,
        path: Path = CAMPUS_CANDIDATES,
        *,
        enabled: bool = False,
        allow_live_scrape: bool = False,
        targets=None,
        scrape_fn=None,
    ) -> None:
        self.path = Path(path)
        self.enabled = enabled
        self.allow_live_scrape = allow_live_scrape
        self.targets = targets
        #: Injected in tests. Production passes None and the real pipeline is
        #: imported lazily, so `agent.scraping`'s dependencies stay optional.
        self.scrape_fn = scrape_fn
        self.partial_failures: list[SourceFailure] = []

    # ── the optional live sweep ──────────────────────────────────────────

    def _sweep(self) -> None:
        """Refresh the review file. Never the seed, never this run's output.

        Anything collected here lands as `NEEDS_HUMAN_REVIEW`, which this
        source ignores, so a sweep can only affect a *later* run and only
        after a person has read it.
        """
        try:
            if self.scrape_fn is not None:
                scrape_fn = self.scrape_fn
            else:  # pragma: no cover - exercised by the operator script
                from agent.scraping.pipeline import scrape as scrape_fn

            records, run = scrape_fn(self.targets)
            for failure in run.failures:
                self.partial_failures.append(
                    SourceFailure(
                        source=self.name,
                        detail=f"campus sweep: {failure.url} — {failure.failure}",
                    )
                )
            log.info(
                "campus_sweep_complete",
                extra={
                    "records": len(records),
                    "failures": len(run.failures),
                    "note": "all rows NEEDS_HUMAN_REVIEW; none usable this run",
                },
            )
        except Exception as exc:  # noqa: BLE001 — a dead sweep must not kill the run
            self.partial_failures.append(
                SourceFailure(
                    source=self.name,
                    detail=f"campus sweep failed: {type(exc).__name__}: {exc}",
                )
            )

    # ── the source protocol ──────────────────────────────────────────────

    def fetch(self, since: datetime | None = None) -> list[Opportunity]:
        self.partial_failures = []

        if not self.enabled:
            log.info("campus_source_disabled", extra={"flag": "KAIROS_ENABLE_BROWSER"})
            return []

        if self.allow_live_scrape:
            self._sweep()

        if not self.path.exists():
            raise SourceError(f"campus candidates file not found at {self.path}")
        try:
            rows = json.loads(self.path.read_text())
        except json.JSONDecodeError as exc:
            raise SourceError(f"campus candidates file is not valid JSON: {exc}") from exc

        opportunities: list[Opportunity] = []
        held_back = 0
        for row in rows:
            if row.get("review_status") != "ACCEPTED":
                held_back += 1
                continue
            if not row.get("source_url"):
                # An accepted row with no page is a curation mistake, not a
                # recommendation. Reported so it gets fixed.
                self.partial_failures.append(
                    SourceFailure(
                        source=self.name,
                        detail=(
                            f"{row.get('scrape_id', '?')} is ACCEPTED but has no "
                            f"source_url; skipped"
                        ),
                    )
                )
                continue
            opportunities.append(to_opportunity(row))

        log.info(
            "campus_source_loaded",
            extra={"accepted": len(opportunities), "awaiting_review": held_back},
        )
        return opportunities
