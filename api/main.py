"""FastAPI — the read surface over what the agent already did.

Deliberately thin. The product is a scheduled run, not an HTTP request from
a user (Section 2), so almost everything here is a GET. The one POST exists
to trigger a run manually during a demo, and it does exactly what the
scheduler does.

The three writes are narrow on purpose. `PUT /founders/{id}` replaces a
profile wholesale, `PATCH /inbox/{item_id}` sets the one field a person owns,
and neither can touch a recorded verdict. Nothing here edits a RunReport, a
Rejection, a SkipRecord or a Draft after the fact — those are what the run
decided, and an audit trail you can edit is not one.

The endpoint that matters most to a sceptical judge is
`GET /runs/{run_id}/skips`. "How do I know it isn't just hiding things?"
should have a one-click answer (Section 9, rule 5).
"""

from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from agent.budget import RunBudget
from agent.config import REPO_ROOT, settings
from agent.models import (
    ApplicationForm,
    FounderProfile,
    InboxState,
    Opportunity,
    RunReport,
)
from agent.runtime import SubAgents
from agent.scout import new_run_context, run_once
from agent.tools.discovery import GrantsGovClient, GrantsGovSource, SeedCatalog
from api.repository import SqliteRepository

log = logging.getLogger("kairos.api")

#: Vercel gives every preview deploy a generated subdomain, so the regex
#: matters as much as the literal origins. Without it, dashboard calls fail
#: silently in the browser while curl keeps working.
ALLOWED_ORIGINS = ["http://localhost:3000", "https://kairos.vercel.app"]
ALLOWED_ORIGIN_REGEX = r"https://kairos-[a-z0-9-]+\.vercel\.app"


class RunTrigger(BaseModel):
    """Manual run request. Same code path as the scheduled run."""

    use_demo_catalog: bool = False
    include_grants_gov: bool = True


class InboxStateUpdate(BaseModel):
    """The one thing a person may change about a surfaced item."""

    state: InboxState


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.repo = SqliteRepository(settings().db_url)
    _seed_demo_profile(app.state.repo)
    yield


app = FastAPI(title="Kairos", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_origin_regex=ALLOWED_ORIGIN_REGEX,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _seed_demo_profile(repo: SqliteRepository) -> None:
    path = REPO_ROOT / "data" / "demo_founder.json"
    if not path.exists():
        return
    profile = FounderProfile.model_validate_json(path.read_text())
    if repo.get_profile(profile.founder_id) is None:
        repo.save_profile(profile)


def _forms() -> dict[str, ApplicationForm]:
    directory = REPO_ROOT / "data" / "forms"
    if not directory.exists():
        return {}
    forms = {}
    for path in sorted(directory.glob("*.json")):
        form = ApplicationForm.model_validate(json.loads(path.read_text()))
        forms[form.opportunity_id] = form
    return forms


def _sources(trigger: RunTrigger):
    config = settings()
    catalog = "opportunities.demo.json" if trigger.use_demo_catalog else "opportunities.seed.json"
    sources = [
        SeedCatalog(
            config.data_dir / catalog,
            # The demo catalog is synthetic and unverified by construction,
            # so loading it at all is an explicit opt-in.
            allow_unverified=trigger.use_demo_catalog or config.allow_unverified_seed,
        )
    ]
    if trigger.include_grants_gov:
        sources.append(
            GrantsGovSource(
                GrantsGovClient(config.grants_gov_base_url, config.http_timeout_s)
            )
        )
    return sources


def _skips_payload(report: RunReport) -> dict:
    """Everything a run threw away, in one shape.

    Shared by the `latest` and by-id routes so the two can never drift into
    telling different stories about the same run.
    """
    return {
        "run_id": report.run_id,
        "headline": report.headline(),
        "rejections": report.rejections,
        "skips": report.skips,
        "sources_failed": report.sources_failed,
        "notes": report.notes,
    }


def _run_for_founder(founder_id: str, run_id: str) -> RunReport:
    """One run, scoped to the founder in the path.

    There is no auth in this repository, so scoping is not a security control
    — it is here so a mistyped id 404s instead of quietly returning somebody
    else's run.
    """
    report = app.state.repo.get_run(run_id)
    if report is None or report.founder_id != founder_id:
        raise HTTPException(404, f"no run {run_id} for {founder_id}")
    return report


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/founders/{founder_id}")
def get_founder(founder_id: str) -> FounderProfile:
    profile = app.state.repo.get_profile(founder_id)
    if profile is None:
        raise HTTPException(404, f"no profile for {founder_id}")
    return profile


@app.put("/founders/{founder_id}")
def put_founder(founder_id: str, profile: FounderProfile) -> FounderProfile:
    """Create or replace a founder profile.

    A full replace, not a patch. These fields are what the deterministic
    eligibility filter compares against, so a half-applied update is the one
    outcome worth ruling out entirely — `citizenship` set without
    `degree_level` is how a founder gets told they are eligible for something
    they are not.
    """
    if profile.founder_id != founder_id:
        raise HTTPException(
            400,
            f"founder_id in the body ({profile.founder_id!r}) does not match "
            f"the path ({founder_id!r})",
        )
    app.state.repo.save_profile(profile)
    # Read back rather than echoing the request: what is stored has been
    # through redaction, and that is what every other endpoint will serve.
    stored = app.state.repo.get_profile(founder_id)
    if stored is None:  # pragma: no cover - only reachable if the write vanished
        raise HTTPException(500, "profile was not persisted")
    return stored


@app.get("/founders/{founder_id}/inbox")
def get_inbox(founder_id: str, include_passive: bool = True) -> list:
    items = app.state.repo.list_inbox(founder_id)
    return items if include_passive else [i for i in items if not i.passive]


@app.get("/founders/{founder_id}/runs")
def list_runs(founder_id: str, limit: int = 20) -> list[RunReport]:
    return app.state.repo.list_runs(founder_id, limit)


@app.get("/founders/{founder_id}/runs/latest")
def latest_run(founder_id: str) -> RunReport:
    report = app.state.repo.latest_run(founder_id)
    if report is None:
        raise HTTPException(404, f"no runs recorded for {founder_id}")
    return report


@app.get("/founders/{founder_id}/runs/latest/skips")
def latest_skips(founder_id: str) -> dict:
    """Everything the agent threw away, and why.

    The founder does not see this by default. A judge asking "how do I know
    it isn't hiding things?" gets it in one click.
    """
    report = app.state.repo.latest_run(founder_id)
    if report is None:
        raise HTTPException(404, f"no runs recorded for {founder_id}")
    return _skips_payload(report)


@app.get("/founders/{founder_id}/runs/{run_id}")
def get_run(founder_id: str, run_id: str) -> RunReport:
    """One run by id, however old.

    `list_runs` is capped, so without this a link to an older run resolves to
    nothing and the transparency trail has a horizon.
    """
    return _run_for_founder(founder_id, run_id)


@app.get("/founders/{founder_id}/runs/{run_id}/skips")
def get_run_skips(founder_id: str, run_id: str) -> dict:
    """The silent path for one specific run."""
    return _skips_payload(_run_for_founder(founder_id, run_id))


@app.get("/opportunities/{opportunity_id}")
def get_opportunity(opportunity_id: str) -> Opportunity:
    """The row a verdict was made about.

    Award range, deadline and the extracted eligibility rules live here as
    structured fields. Anything that wants to sort or filter on them reads
    this rather than parsing the headline a run happened to compose.
    """
    opportunity = app.state.repo.get_opportunity(opportunity_id)
    if opportunity is None:
        raise HTTPException(404, f"no opportunity {opportunity_id}")
    return opportunity


@app.patch("/inbox/{item_id}")
def patch_inbox_item(item_id: str, update: InboxStateUpdate):
    """Record what the founder did with an item: opened, dismissed, applied.

    `state` is the only mutable field. Everything else on an inbox item is
    what the run decided, and letting a later edit rewrite it would turn the
    audit trail into a record of the most recent opinion.
    """
    item = app.state.repo.set_inbox_state(item_id, update.state)
    if item is None:
        raise HTTPException(404, f"no inbox item {item_id}")
    return item


@app.get("/founders/{founder_id}/drafts")
def list_drafts(founder_id: str, opportunity_id: str | None = None) -> list[dict]:
    """Every draft for a founder, newest form first.

    Counts come from `Draft.counts()` — computed in Python, never by a model
    (Section 9, rule 8).
    """
    drafts = app.state.repo.list_drafts(founder_id, opportunity_id)
    return [{"draft": d, "counts": d.counts()} for d in drafts]


@app.get("/drafts/{draft_id}")
def get_draft(draft_id: str) -> dict:
    draft = app.state.repo.get_draft(draft_id)
    if draft is None:
        raise HTTPException(404, f"no draft {draft_id}")
    # Counts are computed in Python, never by a model (Section 9, rule 8).
    return {"draft": draft, "counts": draft.counts()}


@app.post("/founders/{founder_id}/runs")
async def trigger_run(founder_id: str, trigger: RunTrigger) -> RunReport:
    """Run now. Identical to what EventBridge invokes on a schedule."""
    profile = app.state.repo.get_profile(founder_id)
    if profile is None:
        raise HTTPException(404, f"no profile for {founder_id}")

    ctx = new_run_context(
        profile=profile,
        repo=app.state.repo,
        budget=RunBudget.from_settings(settings()),
        agents=SubAgents.build(),
    )
    ctx.forms = _forms()
    return await run_once(ctx, _sources(trigger))
