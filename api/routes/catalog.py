"""Public programme rows and scraper review queues."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request

from agent.models import Opportunity
from agent.scraping.agent import GENERAL_LANE, UNIVERSITY_LANE, ScraperLane
from agent.scraping.models import ScrapedOpportunity
from api.auth import SCOPE_FOUNDER_READ, Principal
from api.deps import ListLimit, ResourceId, principal
from api.schemas import CandidateLane, ScraperCandidateGroup

router = APIRouter()


SCRAPER_CANDIDATE_LANES: dict[str, ScraperLane] = {
    "university": UNIVERSITY_LANE,
    "general": GENERAL_LANE,
}


def _read_scraper_candidates(path: Path) -> list[ScrapedOpportunity]:
    """Read a candidate file. Missing means no run has written it yet."""
    if not path.exists():
        return []
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(500, "scraper candidate file is unreadable") from exc
    if not isinstance(rows, list):
        raise HTTPException(500, "scraper candidate file must contain a list")
    try:
        return [ScrapedOpportunity.model_validate(row) for row in rows]
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, "scraper candidate file is invalid") from exc


def _scraper_candidate_group(name: str, limit: int) -> ScraperCandidateGroup:
    """Read one lane's candidate file, newest first, capped at `limit`.

    `total` is the count before the cap, so the dashboard can say "showing 6
    of 41" rather than implying the file holds six rows.
    """
    lane = SCRAPER_CANDIDATE_LANES[name]
    candidates = sorted(
        _read_scraper_candidates(lane.output_path),
        key=lambda candidate: candidate.scraped_at,
        reverse=True,
    )
    return ScraperCandidateGroup(
        lane=name,  # type: ignore[arg-type]
        label=lane.label,
        source_file=lane.output_path.name,
        total=len(candidates),
        candidates=candidates[:limit],
    )


@router.get("/opportunities/{opportunity_id}")
def get_opportunity(
    request: Request,
    opportunity_id: ResourceId, actor: Principal = Depends(principal)
) -> Opportunity:
    """The row a verdict was made about.

    Award range, deadline and the extracted eligibility rules live here as
    structured fields. Anything that wants to sort or filter on them reads
    this rather than parsing the headline a run happened to compose.

    This is the one resource-id route with no ownership check, and the reason
    is that an opportunity is not founder data: it is a public funding
    programme, the same row for everyone, discovered from Grants.gov or a
    published catalogue. Which opportunities a *founder* was shown is founder
    data, and that lives in the inbox, which is scoped. Authentication is
    still required — an unauthenticated caller has no business enumerating
    the catalogue. A scheduler principal is authenticated and still 404s:
    listing programmes is a founder-read, not `run:trigger`.
    """
    state = request.app.state
    if not actor.has_scope(SCOPE_FOUNDER_READ):
        raise HTTPException(404, f"no opportunity {opportunity_id}")
    opportunity = state.repo.get_opportunity(opportunity_id)
    if opportunity is None:
        raise HTTPException(404, f"no opportunity {opportunity_id}")
    return opportunity


@router.get("/scraper/candidates")
def get_scraper_candidates(
    lane: CandidateLane = "both",
    limit: ListLimit = 6,
    actor: Principal = Depends(principal),
) -> dict[str, ScraperCandidateGroup]:
    """Search-discovered candidate rows, grouped by scraper lane.

    These are review queues, not the runtime opportunity catalog. The rows
    come from scraper candidate files and keep their `NEEDS_HUMAN_REVIEW`,
    `ACCEPTED`, or `REJECTED` status exactly as written there.
    """
    if not actor.has_scope(SCOPE_FOUNDER_READ):
        raise HTTPException(404, "no scraper candidates")
    names = SCRAPER_CANDIDATE_LANES.keys() if lane == "both" else (lane,)
    return {name: _scraper_candidate_group(name, limit) for name in names}
