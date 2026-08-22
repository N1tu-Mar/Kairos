"""FastAPI — the read surface over what the agent already did.

Deliberately thin. The product is a scheduled run, not an HTTP request from
a user (Section 2), so almost everything here is a GET. The one POST exists
to trigger a run manually during a demo, and it does exactly what the
scheduler does.

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
from agent.models import ApplicationForm, FounderProfile, RunReport
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


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/founders/{founder_id}")
def get_founder(founder_id: str) -> FounderProfile:
    profile = app.state.repo.get_profile(founder_id)
    if profile is None:
        raise HTTPException(404, f"no profile for {founder_id}")
    return profile


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
    return {
        "run_id": report.run_id,
        "headline": report.headline(),
        "rejections": report.rejections,
        "skips": report.skips,
        "sources_failed": report.sources_failed,
        "notes": report.notes,
    }


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
