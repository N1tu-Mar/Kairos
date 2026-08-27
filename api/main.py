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

import logging
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from agent.config import REPO_ROOT, settings
from agent.models import (
    FounderProfile,
    InboxState,
    Opportunity,
    RunJob,
    RunReport,
)
from agent.scheduler import RunLock, ScheduledRunFailureLog
from api import jobs as job_module
from api.jobs import LocalJobExecutor
from api.repository import SqliteRepository

log = logging.getLogger("kairos.api")

#: Vercel gives every preview deploy a generated subdomain, so the regex
#: matters as much as the literal origins. Without it, dashboard calls fail
#: silently in the browser while curl keeps working.
ALLOWED_ORIGINS = ["http://localhost:3000", "https://kairos.vercel.app"]
ALLOWED_ORIGIN_REGEX = r"https://kairos-[a-z0-9-]+\.vercel\.app"


class RunTrigger(BaseModel):
    """Run request. Same code path whether a person or the scheduler asks.

    `idempotency_key` is how a retry resolves to the same logical
    invocation: EventBridge sends its execution id, the dashboard sends a
    generated one per click. `source` is recorded on the job and on any
    failure-log entry, so "did last night's *scheduled* run fail?" is
    answerable.
    """

    use_demo_catalog: bool = False
    include_grants_gov: bool = True
    idempotency_key: str | None = None
    source: str = "unknown"


class InboxStateUpdate(BaseModel):
    """The one thing a person may change about a surfaced item."""

    state: InboxState


@asynccontextmanager
async def lifespan(app: FastAPI):
    config = settings()
    if not config.api_token:
        log.warning(
            "KAIROS_API_TOKEN is not set — the API is running open. "
            "Acceptable on localhost only; never deploy it this way."
        )
    app.state.repo = SqliteRepository(config.db_url)
    _seed_demo_profile(app.state.repo)

    # The async job machinery. The lease TTL is double the run timeout so a
    # live run's lease can never expire out from under it.
    app.state.failure_log = ScheduledRunFailureLog(
        config.state_dir / "scheduler_failures.jsonl"
    )
    app.state.run_lock = RunLock(
        config.state_dir / "locks", ttl_seconds=int(config.run_timeout_s * 2)
    )
    # Crash repair happens before the first request can create new jobs:
    # nothing may stay "running" with no process behind it.
    job_module.recover_orphaned_jobs(
        app.state.repo, app.state.failure_log, app.state.run_lock
    )
    app.state.executor = LocalJobExecutor(app.state.repo, app.state.failure_log)
    yield


app = FastAPI(title="Kairos", lifespan=lifespan)


#: Paths that stay reachable without a token, so a load balancer can probe
#: without holding a credential. Both are deliberately uninformative about
#: the deployment: liveness is a constant, readiness names which check failed
#: and never what it was configured with.
AUTH_EXEMPT_PATHS = {"/health", "/ready"}


@app.middleware("http")
async def require_api_token(request: Request, call_next):
    """Bearer-token gate over every endpoint, reads included.

    Reads leak as much as writes here — a profile is citizenship, degree
    level and traction numbers — so the gate is not writes-only. The token
    comes from `KAIROS_API_TOKEN`. When it is unset the API runs open for
    the local single-founder demo, and the lifespan hook logs that exposure
    at startup. When it is set, a missing or wrong credential is a 401 with
    no hint as to which of the two it was.
    """
    token = settings().api_token
    if (
        not token
        or request.url.path in AUTH_EXEMPT_PATHS
        # CORS preflights carry no Authorization header by design; the
        # browser sends the real header only on the actual request.
        or request.method == "OPTIONS"
    ):
        return await call_next(request)

    supplied = request.headers.get("authorization", "")
    expected = f"Bearer {token}"
    if not secrets.compare_digest(supplied.encode(), expected.encode()):
        return JSONResponse(
            {"detail": "missing or invalid API token"},
            status_code=401,
            headers={"WWW-Authenticate": "Bearer"},
        )
    return await call_next(request)


app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_origin_regex=ALLOWED_ORIGIN_REGEX,
    allow_methods=["GET", "POST", "PATCH", "PUT", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)


def _seed_demo_profile(repo: SqliteRepository) -> None:
    path = REPO_ROOT / "data" / "demo_founder.json"
    if not path.exists():
        return
    profile = FounderProfile.model_validate_json(path.read_text())
    if repo.get_profile(profile.founder_id) is None:
        repo.save_profile(profile)


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
    """Liveness. The process is up and serving; nothing more is claimed.

    Deliberately dependency-free. A liveness probe that checks the database
    is a liveness probe that restarts a healthy container because storage
    hiccuped, and restarting rarely fixes storage.
    """
    return {"status": "ok"}


@app.get("/ready")
def ready(response: Response) -> dict:
    """Readiness: can this process actually serve a request right now?

    Checks what a request needs and nothing that costs money:

    *   **storage** — one trivial query against the database, and a write
        probe against the state directory. Both are the failure modes an
        EFS mount produces, and neither is visible from `/health`.
    *   **configuration** — that model IDs resolve at all, and in production
        mode that the deployment is not accidentally open or unpriced.

    No model is invoked. A readiness check that costs a Bedrock call is a
    readiness check that bills you per probe interval.

    The body names *which* check failed and never what it was configured
    with: no model IDs, no paths, no token. `/ready` is reachable without a
    credential, so it must not describe the deployment to a stranger.
    """
    checks: dict[str, str] = {}

    try:
        app.state.repo.get_profile("__readiness_probe__")
        checks["database"] = "ok"
    except Exception:  # noqa: BLE001
        checks["database"] = "unavailable"

    try:
        config = settings()
    except Exception:  # noqa: BLE001
        # A missing model ID raises here by design — the process is up but
        # cannot run anything.
        checks["configuration"] = "invalid"
        response.status_code = 503
        return {"status": "not_ready", "checks": checks}

    try:
        state_dir = Path(config.state_dir)
        state_dir.mkdir(parents=True, exist_ok=True)
        probe = state_dir / ".readiness"
        probe.write_text("ok")
        probe.unlink(missing_ok=True)
        checks["state_storage"] = "ok"
    except OSError:
        checks["state_storage"] = "unwritable"

    checks["configuration"] = "ok"

    # Production mode is opt-in and strict. In local single-founder mode an
    # open API and zero prices are the documented demo posture, not a fault.
    if config.production:
        if not config.api_token:
            checks["authentication"] = "missing"
        else:
            checks["authentication"] = "ok"
        if config.daily_usd_cap > 0 and not config.prices.configured:
            # Zero prices make the daily USD cap unenforceable: every call
            # costs $0.00, so the cap can never trip.
            checks["spend_cap"] = "unenforceable"
        else:
            checks["spend_cap"] = "ok"

    ok = all(value == "ok" for value in checks.values())
    if not ok:
        response.status_code = 503
    return {"status": "ready" if ok else "not_ready", "checks": checks}


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


@app.post("/founders/{founder_id}/runs", status_code=202)
async def trigger_run(
    founder_id: str, trigger: RunTrigger, response: Response
) -> RunJob:
    """Accept a run and return immediately. EventBridge calls this too.

    Three outcomes, all fast:

    *   **202** — a job was created and is now running in the background.
        Poll `GET /founders/{id}/jobs/{job_id}` for its state.
    *   **200** — this idempotency key already landed; here is that job
        again. A scheduler retry or a double-submitted form resolves to the
        same logical invocation instead of a second run.
    *   **409** — another run holds the lease for this founder. The body
        names the running job when it is known.

    The connection no longer spans the run. A run takes minutes; sockets,
    load balancers and browsers all have opinions about minutes.
    """
    profile = app.state.repo.get_profile(founder_id)
    if profile is None:
        raise HTTPException(404, f"no profile for {founder_id}")

    if trigger.idempotency_key:
        existing = app.state.repo.get_job_by_key(founder_id, trigger.idempotency_key)
        if existing is not None:
            response.status_code = 200
            return existing

    source = trigger.source if trigger.source in ("manual", "scheduled") else "unknown"
    lease = app.state.run_lock.acquire(
        founder_id=founder_id, run_kind=job_module.RUN_KIND
    )
    if not lease.acquired:
        running = next(
            (
                j
                for j in app.state.repo.list_jobs(founder_id, limit=5)
                if not j.terminal()
            ),
            None,
        )
        raise HTTPException(
            409,
            {
                "detail": f"a run is already in progress for {founder_id}",
                "running_job_id": running.job_id if running else None,
            },
        )

    job = job_module.new_job(
        founder_id=founder_id,
        idempotency_key=trigger.idempotency_key,
        source=source,
        use_demo_catalog=trigger.use_demo_catalog,
        include_grants_gov=trigger.include_grants_gov,
    )
    try:
        app.state.repo.save_job(job)
    except Exception:
        # The unique index on the idempotency key fired: a concurrent
        # duplicate beat us to the insert. Return its job, not a second run.
        lease.release()
        if trigger.idempotency_key:
            existing = app.state.repo.get_job_by_key(
                founder_id, trigger.idempotency_key
            )
            if existing is not None:
                response.status_code = 200
                return existing
        raise

    app.state.executor.submit(job, lease)
    return job


@app.get("/founders/{founder_id}/jobs")
def list_jobs(founder_id: str, limit: int = 20) -> list[RunJob]:
    return app.state.repo.list_jobs(founder_id, limit)


@app.get("/founders/{founder_id}/jobs/{job_id}")
def get_job(founder_id: str, job_id: str) -> dict:
    """One job, with its report once the run has one.

    The poll target for the dashboard's manual-run button. `report` is null
    until the run finishes; a halted run has a report too — halting is a
    reported outcome, not an error.
    """
    job = app.state.repo.get_job(job_id)
    if job is None or job.founder_id != founder_id:
        raise HTTPException(404, f"no job {job_id} for {founder_id}")
    report = app.state.repo.get_run(job.run_id) if job.run_id else None
    return {"job": job, "report": report}


@app.post("/founders/{founder_id}/jobs/{job_id}/cancel")
def cancel_job(founder_id: str, job_id: str) -> dict:
    """Ask the executor to stop a running job.

    Cooperative: the run stops at its next await point and the job records
    `cancelled`. What the run already persisted stays persisted — cancel
    stops future work, it does not rewrite history. A job that is already
    terminal, or running in a process this API cannot reach, reports
    `cancelled: false`.
    """
    job = app.state.repo.get_job(job_id)
    if job is None or job.founder_id != founder_id:
        raise HTTPException(404, f"no job {job_id} for {founder_id}")
    if job.terminal():
        return {"cancelled": False, "status": job.status}
    return {"cancelled": app.state.executor.cancel(job_id), "status": job.status}


@app.get("/founders/{founder_id}/scheduler/failures")
def scheduler_failures(founder_id: str, limit: int = 20) -> list:
    """Recent invocations that failed to start or finish, newest first.

    Sanitised before persistence — no credentials, no prompts, no stack
    traces. CloudWatch keeps the archive; this answers "did last night's
    run fail?" from the dashboard.
    """
    return app.state.failure_log.recent(founder_id, limit=limit)
