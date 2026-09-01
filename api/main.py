"""FastAPI — the read surface over what the agent already did.

Deliberately thin. The product is a scheduled run, not an HTTP request from
a user (Section 2), so almost everything here is a GET. The one POST exists
to trigger a run manually during a demo, and it does exactly what the
scheduler does.

The writes are narrow on purpose. `PUT /founders/{id}` replaces a profile,
`PATCH /inbox/{item_id}` changes founder-owned state, and the eligibility
answer route saves founder-owned facts. None can touch a recorded verdict.
Nothing here edits a RunReport, a
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
from datetime import datetime, timezone
from pathlib import Path

from typing import Annotated, Literal

from fastapi import Depends, FastAPI, HTTPException, Path as PathParam, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field as PydanticField

from agent.config import REPO_ROOT, settings, validate_runtime_posture, ConfigError
from agent.models import (
    EligibilityAnswerValue,
    EligibilityQuestion,
    FounderProfile,
    InboxState,
    Opportunity,
    RunJob,
    RunReport,
)
from agent.scraping.agent import GENERAL_LANE, UNIVERSITY_LANE, ScraperLane
from agent.scraping.models import ScrapedOpportunity
from agent.scheduler import RunLock, ScheduledRunFailureLog
from api import jobs as job_module
from api.auth import (
    AuthError,
    Forbidden,
    Principal,
    SCOPE_ELIGIBILITY_ANSWER,
    SCOPE_FOUNDER_READ,
    SCOPE_INBOX_WRITE,
    SCOPE_RUN_CANCEL,
    SCOPE_RUN_TRIGGER,
    audit_event,
    authorize,
    build_authenticator,
)
from api.jobs import LocalJobExecutor
from api.repository import SqliteRepository

log = logging.getLogger("kairos.api")

#: Vercel gives every preview deploy a generated subdomain, so the regex
#: matters as much as the literal origins. Without it, dashboard calls fail
#: silently in the browser while curl keeps working.
ALLOWED_ORIGINS = ["http://localhost:3000", "https://kairos.vercel.app"]
ALLOWED_ORIGIN_REGEX = r"https://kairos-[a-z0-9-]+\.vercel\.app"


# ── Input bounds ─────────────────────────────────────────────────────────────
#
# Every parameter that reaches the database is bounded here rather than in
# each route, so a route added later inherits the bound by using the type
# instead of remembering the rule.
#
# The numbers are chosen against real callers, not invented: the dashboard's
# runs page asks for 50 (`frontend/src/app/runs/page.tsx`), so the ceiling
# sits well above it. What the ceiling actually prevents is `?limit=10000000`
# — a request to serialise the whole table, which is a denial-of-service
# written in query-string form — and `?limit=-1`, which SQL reads as
# "no limit at all".

#: Longest identifier any route accepts. Real ids are short: a run id is
#: `run_` plus 12 hex characters, an inbox item id is `{run_id}:{opp_id}`.
#: 200 is generous for all of them and still bounds what reaches an index.
MAX_ID_LENGTH = 200

#: Most rows a list endpoint will return in one response.
MAX_LIST_LIMIT = 1_000

#: Largest request body this API will read, in bytes.
#:
#: The parameter bounds above stop a caller asking for the whole table back.
#: This is the same argument pointed the other way: uvicorn imposes no ceiling
#: of its own, so a `PUT /founders/{id}` was buffered in full before Pydantic
#: saw it, and `knowledge_base` is a list with no length limit whose entries
#: had no length limit either. A body is refused on its `Content-Length`
#: before it is read; `FounderProfile`'s own field bounds catch what is small
#: enough to admit and still absurd as a profile.
#:
#: 2 MB against a real caller: the largest thing anyone legitimately sends is
#: a profile with a full knowledge base, which is tens of kilobytes.
MAX_BODY_BYTES = 2 * 1024 * 1024

#: A list limit: at least one row, at most `MAX_LIST_LIMIT`.
ListLimit = Annotated[int, Query(ge=1, le=MAX_LIST_LIMIT)]

#: An identifier in a path. Non-empty and length-bounded. Deliberately not a
#: character allowlist: ids come from several generators and a wrong pattern
#: would 422 a legitimate row, which is worse than the unbounded-length
#: problem this exists to solve. Traversal and injection are handled where
#: they matter — ids are parameter-bound in SQL and never used as paths.
ResourceId = Annotated[str, PathParam(min_length=1, max_length=MAX_ID_LENGTH)]

#: An identifier in a query string. The default belongs on the parameter
#: (`= None`), not in the annotation — FastAPI rejects a `Query` default
#: inside `Annotated`.
OptionalResourceId = Annotated[
    str | None, Query(min_length=1, max_length=MAX_ID_LENGTH)
]

#: Who asked for a run. Closed set: it is recorded on the job and on failure
#: log entries, so "did last night's *scheduled* run fail?" depends on the
#: value being trustworthy. An unrecognised value used to be silently
#: rewritten to "unknown", which threw away the caller's mistake.
RunSource = Literal["manual", "scheduled", "unknown"]


class RunTrigger(BaseModel):
    """Run request. Same code path whether a person or the scheduler asks.

    `idempotency_key` is how a retry resolves to the same logical
    invocation: EventBridge sends its execution id, the dashboard sends a
    generated one per click. `source` is recorded on the job and on any
    failure-log entry, so "did last night's *scheduled* run fail?" is
    answerable.

    `extra="forbid"`: a misspelled flag is a caller who thinks they asked for
    something. Accepting `use_demo_catalogue` and silently running against
    the real catalogue is the failure this prevents.
    """

    model_config = ConfigDict(extra="forbid")

    use_demo_catalog: bool = False
    include_grants_gov: bool = True
    idempotency_key: str | None = PydanticField(
        default=None, min_length=1, max_length=MAX_ID_LENGTH
    )
    source: RunSource = "unknown"


class InboxStateUpdate(BaseModel):
    """The one thing a person may change about a surfaced item."""

    model_config = ConfigDict(extra="forbid")

    state: InboxState


class EligibilityAnswerUpdate(BaseModel):
    """The founder's editable answer to one eligibility requirement."""

    model_config = ConfigDict(extra="forbid")

    answer: EligibilityAnswerValue


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Build everything the process needs, once, before the first request.

    Order matters and is not arbitrary: the authenticator exists before any
    request can be authenticated, the repository before the demo profile is
    seeded, and `recover_orphaned_jobs` runs before the executor is installed
    so no new job can be created while the crash repair is deciding which
    rows are orphans.

    Everything lands on `app.state`, which makes it per-application rather
    than global — a test builds a second app with its own repository without
    disturbing this one.
    """
    config = settings()
    try:
        # Production with local auth, incomplete Supabase, or Playwright
        # enabled must not serve. A readiness probe can still describe a
        # host that was started in local mode and later had KAIROS_ENV
        # flipped; a process that *boots* as production has to be safe.
        validate_runtime_posture(config)
    except ConfigError:
        log.exception("refusing to start: runtime posture is unsafe")
        raise
    if (
        config.auth_mode != "supabase"
        and not config.supabase_issuer
        and not config.api_token
        and not config.credentials_file
        and not config.scheduler_token
    ):
        if config.allow_open_api:
            log.warning(
                "KAIROS_ALLOW_OPEN_API is on and no credential is configured — "
                "the API is running open and every request has write access. "
                "Acceptable on localhost only; never deploy it this way."
            )
        else:
            # Fails closed, so this is not a hole — but every request will 401
            # and the operator should hear why at startup rather than deduce
            # it from a wall of 401s.
            log.error(
                "No credential is configured — every request will be refused. "
                "Set KAIROS_API_TOKEN, or KAIROS_ALLOW_OPEN_API=1 for a local demo."
            )
    # In production the schema belongs to `alembic upgrade head`, run at
    # deploy time. create_all() cannot evolve one — it fills in missing
    # tables and says nothing about a table whose shape has drifted — so a
    # deployment that skipped its migration must fail readiness loudly
    # rather than boot on a half-invented schema.
    app.state.repo = SqliteRepository(
        config.db_url, create_schema=not config.production
    )
    _seed_demo_profile(app.state.repo)
    # After the repository, not before: Supabase authorization reads its
    # memberships from it, so the authenticator cannot be built first.
    app.state.authenticator = build_authenticator(config, app.state.repo)

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
async def bound_request_body(request: Request, call_next):
    """Refuse an oversized body before anything reads it.

    Registered before `authenticate` so it runs *after* it — Starlette applies
    HTTP middleware in reverse registration order — which is deliberate: an
    unauthenticated caller should not be able to make the server buffer two
    megabytes before being told to go away, but neither should the size check
    be the thing that leaks whether a credential was valid. Authentication
    first, then the size ceiling, then the route.

    `Content-Length` only. A chunked upload arrives without one, and reading
    the stream to measure it is the work this exists to avoid; Starlette will
    still buffer such a body, which is why `FounderProfile` carries its own
    field bounds rather than trusting this to be the only wall.
    """
    declared = request.headers.get("content-length")
    if declared is not None:
        try:
            size = int(declared)
        except ValueError:
            return JSONResponse({"detail": "malformed content-length"}, status_code=400)
        if size > MAX_BODY_BYTES:
            return JSONResponse(
                {"detail": f"request body exceeds {MAX_BODY_BYTES} bytes"},
                status_code=413,
            )
    return await call_next(request)


@app.middleware("http")
async def authenticate(request: Request, call_next):
    """Resolve a credential to a principal before anything else runs.

    Reads leak as much as writes here — a profile is citizenship, degree
    level and traction numbers — so the gate is not writes-only. A missing
    and a wrong credential are both a 401 with no hint as to which it was.

    Authentication only says *who*. Authorization — which founders this
    principal may touch — is `authorize()`, called per endpoint, because the
    founder id is in the path and middleware has no business parsing paths.
    """
    # A CORS preflight carries no Authorization header by design — the browser
    # sends the real header only on the actual request. Scoped to genuine
    # preflights rather than to the method: `request.method == "OPTIONS"` alone
    # exempts every OPTIONS request to every route, which is a wider hole than
    # the one it was opened for.
    is_preflight = (
        request.method == "OPTIONS"
        and "access-control-request-method" in request.headers
        and "origin" in request.headers
    )
    if request.url.path in AUTH_EXEMPT_PATHS or is_preflight:
        return await call_next(request)

    authenticator = getattr(request.app.state, "authenticator", None)
    if authenticator is None:  # pragma: no cover - lifespan always sets it
        return JSONResponse({"detail": "server is not ready"}, status_code=503)

    try:
        request.state.principal = authenticator.authenticate(
            request.headers.get("authorization")
        )
    except AuthError:
        audit_event(
            actor="unknown",
            action="auth.rejected",
            resource=request.url.path,
            outcome="denied",
        )
        return JSONResponse(
            {"detail": "missing or invalid credential"},
            status_code=401,
            headers={"WWW-Authenticate": "Bearer"},
        )
    return await call_next(request)


def principal(request: Request) -> Principal:
    """The authenticated principal for this request."""
    resolved = getattr(request.state, "principal", None)
    if resolved is None:  # pragma: no cover - middleware always sets it
        raise HTTPException(401, "missing or invalid credential")
    return resolved


def owned(
    founder_id: str,
    actor: Principal,
    *,
    write: bool = False,
    scope: str | None = None,
    not_found: str | None = None,
) -> None:
    """Authorize, translating a refusal into 404.

    Not 403. A 403 on a founder id confirms the id exists, which turns
    id-guessing into founder enumeration — the whole point of adding
    ownership. Not-found and not-yours must be indistinguishable, which means
    the *message* has to match too: `not_found` lets a caller supply the
    exact wording that endpoint uses for a genuinely missing resource.

    `scope` is the action this path performs. A scheduler principal owns one
    founder but still 404s here unless the path is `run:trigger`.
    """
    try:
        authorize(actor, founder_id, write=write, scope=scope)
    except Forbidden:
        raise HTTPException(
            404, not_found or f"no profile for {founder_id}"
        ) from None


app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_origin_regex=ALLOWED_ORIGIN_REGEX,
    allow_methods=["GET", "POST", "PATCH", "PUT", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)


def _seed_demo_profile(repo: SqliteRepository) -> None:
    """Insert the demo founder if this database has never seen them.

    Writes only when absent, so a profile edited through `PUT /founders/{id}`
    survives a restart instead of being reset to the shipped JSON. A missing
    `demo_founder.json` is not an error — a real deployment has no demo row.
    """
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

    Now a security control, not just a typo guard: the caller has already
    been authorized for `founder_id`, and a run belonging to anyone else is
    a 404 regardless of whether the id was guessed or mistyped.
    """
    report = app.state.repo.get_run(run_id)
    if report is None or report.founder_id != founder_id:
        raise HTTPException(404, f"no run {run_id} for {founder_id}")
    return report


CandidateLane = Literal["university", "general", "both"]

SCRAPER_CANDIDATE_LANES: dict[str, ScraperLane] = {
    "university": UNIVERSITY_LANE,
    "general": GENERAL_LANE,
}


class ScraperCandidateGroup(BaseModel):
    """One candidate file, shaped for the dashboard."""

    lane: Literal["university", "general"]
    label: str
    source_file: str
    total: int
    candidates: list[ScrapedOpportunity]


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
        supabase_ok = config.auth_mode == "supabase" and bool(config.supabase_issuer)
        if (
            supabase_ok
            or config.api_token
            or config.credentials_file
        ):
            checks["authentication"] = "ok"
        else:
            checks["authentication"] = "missing"
        try:
            # An unmigrated database in production means the deploy skipped
            # its migration step. Serving on it would work until the first
            # query against a table this build expects and that one does not
            # have — which is a 500 at 3am rather than a red probe at deploy.
            checks["schema"] = (
                "ok" if app.state.repo.schema_version() else "unmigrated"
            )
        except Exception:  # noqa: BLE001
            checks["schema"] = "unknown"
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
def get_founder(founder_id: ResourceId, actor: Principal = Depends(principal)) -> FounderProfile:
    """One founder profile. Ownership-checked, and 404 for a founder that is not yours."""
    owned(founder_id, actor)
    profile = app.state.repo.get_profile(founder_id)
    if profile is None:
        raise HTTPException(404, f"no profile for {founder_id}")
    return profile


@app.put("/founders/{founder_id}")
def put_founder(
    founder_id: ResourceId,
    profile: FounderProfile,
    actor: Principal = Depends(principal),
) -> FounderProfile:
    """Create or replace a founder profile.

    A full replace, not a patch. These fields are what the deterministic
    eligibility filter compares against, so a half-applied update is the one
    outcome worth ruling out entirely — `citizenship` set without
    `degree_level` is how a founder gets told they are eligible for something
    they are not.

    Both ids are checked: the path (which the principal must own) and the
    body. Without the second check a principal could replace their own
    profile with a document naming someone else's founder id.
    """
    # Profile replace is a founder write. The scheduler token has no such
    # scope, so EventBridge cannot edit a knowledge base it is only meant
    # to run against.
    owned(founder_id, actor, write=True)
    if profile.founder_id != founder_id:
        raise HTTPException(
            400,
            f"founder_id in the body ({profile.founder_id!r}) does not match "
            f"the path ({founder_id!r})",
        )
    app.state.repo.save_profile(profile)
    # The event records that a profile was replaced, never the profile: a
    # founder's citizenship and traction do not belong in an audit log.
    audit_event(
        actor=actor.subject,
        action="profile.write",
        resource=founder_id,
        method=actor.method,
    )
    # Read back rather than echoing the request: what is stored has been
    # through redaction, and that is what every other endpoint will serve.
    stored = app.state.repo.get_profile(founder_id)
    if stored is None:  # pragma: no cover - only reachable if the write vanished
        raise HTTPException(500, "profile was not persisted")
    return stored


@app.get("/founders/{founder_id}/inbox")
def get_inbox(
    founder_id: ResourceId,
    include_passive: bool = True,
    limit: ListLimit = 50,
    actor: Principal = Depends(principal),
) -> list:
    """Surfaced opportunities for one founder, newest first.

    `include_passive=False` drops passively-surfaced items — the ones the run
    recorded but did not consider worth interrupting for.

    Note the ordering: `limit` is applied by the database and the passive
    filter afterwards in Python, so `include_passive=False` can return fewer
    than `limit` rows while more non-passive rows exist further back. The
    dashboard asks for a generous limit rather than paginating, which hides
    this; a caller that pages through this endpoint would not be so lucky.
    """
    owned(founder_id, actor)
    items = app.state.repo.list_inbox(founder_id, limit)
    today = datetime.now(timezone.utc).date()
    items = [
        item
        for item in items
        if (
            (opportunity := app.state.repo.get_opportunity(item.opportunity_id))
            is None
            or opportunity.deadline is None
            or opportunity.deadline >= today
        )
    ]
    return items if include_passive else [i for i in items if not i.passive]


@app.get("/founders/{founder_id}/eligibility-questions")
def list_eligibility_questions(
    founder_id: ResourceId,
    status: Literal["pending", "answered", "all"] = "pending",
    actor: Principal = Depends(principal),
) -> list[EligibilityQuestion]:
    """Founder-answerable uncertainty only; missing source facts do not belong here."""
    owned(founder_id, actor)
    questions = app.state.repo.list_eligibility_questions(founder_id, status)
    if status == "pending":
        today = datetime.now(timezone.utc).date()
        questions = [
            question
            for question in questions
            if question.deadline is None or question.deadline >= today
        ]
    return questions


@app.put("/founders/{founder_id}/eligibility-questions/{question_id}/answer")
async def answer_eligibility_question(
    founder_id: ResourceId,
    question_id: ResourceId,
    update: EligibilityAnswerUpdate,
    response: Response,
    actor: Principal = Depends(principal),
) -> EligibilityQuestion:
    """Save an answer and queue a one-opportunity reassessment when possible."""
    not_found = f"no eligibility question {question_id} for {founder_id}"
    owned(
        founder_id,
        actor,
        write=True,
        scope=SCOPE_ELIGIBILITY_ANSWER,
        not_found=not_found,
    )
    question = app.state.repo.get_eligibility_question(question_id)
    if question is None or question.founder_id != founder_id:
        raise HTTPException(404, not_found)
    updated = app.state.repo.answer_eligibility_question(question_id, update.answer)
    if updated is None:  # pragma: no cover - only if the row vanished mid-request
        raise HTTPException(404, not_found)
    audit_event(
        actor=actor.subject,
        action="eligibility.answer",
        resource=question_id,
        method=actor.method,
        answer=update.answer,
    )

    if update.answer == "not_sure":
        response.headers["X-Kairos-Reassessment"] = "not-requested"
        return updated

    opportunity = app.state.repo.get_opportunity(question.opportunity_id)
    if opportunity is None:
        # Legacy/operator-created rows may not have a persisted source row.
        app.state.repo.mark_eligibility_reassessed(
            founder_id,
            question.opportunity_id,
            before=datetime.now(timezone.utc),
        )
        response.headers["X-Kairos-Reassessment"] = "unavailable"
        return updated

    lease = app.state.run_lock.acquire(
        founder_id=founder_id,
        run_kind=job_module.RUN_KIND,
    )
    if not lease.acquired:
        response.headers["X-Kairos-Reassessment"] = "deferred"
        return updated

    job = job_module.new_job(
        founder_id=founder_id,
        idempotency_key=None,
        source="eligibility_answer",
        use_demo_catalog=False,
        include_grants_gov=False,
        target_opportunity_id=question.opportunity_id,
    )
    try:
        app.state.repo.save_job(job)
    except Exception:
        lease.release()
        raise
    app.state.executor.submit(job, lease)
    response.headers["X-Kairos-Reassessment"] = "queued"
    response.headers["X-Kairos-Reassessment-Job"] = job.job_id
    return updated


@app.get("/founders/{founder_id}/runs")
def list_runs(
    founder_id: ResourceId,
    limit: ListLimit = 20,
    actor: Principal = Depends(principal),
) -> list[RunReport]:
    """Recent run reports for one founder, newest first, capped at `limit`."""
    owned(founder_id, actor)
    return app.state.repo.list_runs(founder_id, limit)


@app.get("/founders/{founder_id}/runs/latest")
def latest_run(founder_id: ResourceId, actor: Principal = Depends(principal)) -> RunReport:
    """The most recent run report. 404 when the founder has never had a run."""
    owned(founder_id, actor)
    report = app.state.repo.latest_run(founder_id)
    if report is None:
        raise HTTPException(404, f"no runs recorded for {founder_id}")
    return report


@app.get("/founders/{founder_id}/runs/latest/skips")
def latest_skips(founder_id: ResourceId, actor: Principal = Depends(principal)) -> dict:
    """Everything the agent threw away, and why.

    The founder does not see this by default. A judge asking "how do I know
    it isn't hiding things?" gets it in one click.
    """
    owned(founder_id, actor)
    report = app.state.repo.latest_run(founder_id)
    if report is None:
        raise HTTPException(404, f"no runs recorded for {founder_id}")
    return _skips_payload(report)


@app.get("/founders/{founder_id}/runs/{run_id}")
def get_run(
    founder_id: ResourceId, run_id: ResourceId, actor: Principal = Depends(principal)
) -> RunReport:
    """One run by id, however old.

    `list_runs` is capped, so without this a link to an older run resolves to
    nothing and the transparency trail has a horizon.
    """
    owned(founder_id, actor)
    return _run_for_founder(founder_id, run_id)


@app.get("/founders/{founder_id}/runs/{run_id}/skips")
def get_run_skips(
    founder_id: ResourceId, run_id: ResourceId, actor: Principal = Depends(principal)
) -> dict:
    """The silent path for one specific run."""
    owned(founder_id, actor)
    return _skips_payload(_run_for_founder(founder_id, run_id))


@app.get("/opportunities/{opportunity_id}")
def get_opportunity(
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
    if not actor.has_scope(SCOPE_FOUNDER_READ):
        raise HTTPException(404, f"no opportunity {opportunity_id}")
    opportunity = app.state.repo.get_opportunity(opportunity_id)
    if opportunity is None:
        raise HTTPException(404, f"no opportunity {opportunity_id}")
    return opportunity


@app.get("/scraper/candidates")
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


@app.patch("/inbox/{item_id}")
def patch_inbox_item(
    item_id: ResourceId,
    update: InboxStateUpdate,
    actor: Principal = Depends(principal),
):
    """Record what the founder did with an item: opened, dismissed, applied.

    `state` is the only mutable field. Everything else on an inbox item is
    what the run decided, and letting a later edit rewrite it would turn the
    audit trail into a record of the most recent opinion.

    The item is read before it is written so its owner can be checked. The
    id is otherwise unguessable-by-design but not unguessable-in-fact —
    it is `{run_id}:{opportunity_id}` — so ownership is verified rather
    than assumed.
    """
    item = app.state.repo.get_inbox_item(item_id)
    if item is None:
        raise HTTPException(404, f"no inbox item {item_id}")
    owned(
        item.founder_id,
        actor,
        write=True,
        scope=SCOPE_INBOX_WRITE,
        not_found=f"no inbox item {item_id}",
    )

    updated = app.state.repo.set_inbox_state(item_id, update.state)
    if updated is None:  # pragma: no cover - it existed one line ago
        raise HTTPException(404, f"no inbox item {item_id}")
    audit_event(
        actor=actor.subject,
        action="inbox.state_change",
        resource=item_id,
        method=actor.method,
        new_state=update.state,
    )
    return updated


@app.get("/founders/{founder_id}/drafts")
def list_drafts(
    founder_id: ResourceId,
    opportunity_id: OptionalResourceId = None,
    actor: Principal = Depends(principal),
) -> list[dict]:
    """Every draft for a founder, newest form first.

    Counts come from `Draft.counts()` — computed in Python, never by a model
    (Section 9, rule 8).
    """
    owned(founder_id, actor)
    drafts = app.state.repo.list_drafts(founder_id, opportunity_id)
    return [{"draft": d, "counts": d.counts()} for d in drafts]


@app.get("/drafts/{draft_id}")
def get_draft(draft_id: ResourceId, actor: Principal = Depends(principal)) -> dict:
    """One draft, ownership-checked.

    A draft is the most sensitive object in the system — it is the founder's
    knowledge base rendered into prose — so the draft's own `founder_id` is
    checked against the principal rather than trusting an opaque id.
    """
    draft = app.state.repo.get_draft(draft_id)
    if draft is None:
        raise HTTPException(404, f"no draft {draft_id}")
    owned(draft.founder_id, actor, not_found=f"no draft {draft_id}")
    # Counts are computed in Python, never by a model (Section 9, rule 8).
    return {"draft": draft, "counts": draft.counts()}


@app.post("/founders/{founder_id}/runs", status_code=202)
async def trigger_run(
    founder_id: ResourceId,
    trigger: RunTrigger,
    response: Response,
    actor: Principal = Depends(principal),
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
    owned(founder_id, actor, write=True, scope=SCOPE_RUN_TRIGGER)
    profile = app.state.repo.get_profile(founder_id)
    if profile is None:
        raise HTTPException(404, f"no profile for {founder_id}")

    # A scheduler credential is not a founder. Force the recorded source
    # so a crafted body cannot pretend EventBridge was a person, and refuse
    # the synthetic demo catalog — that path exists for a laptop click, not
    # for a nightly production invocation.
    if actor.is_scheduler:
        if trigger.use_demo_catalog:
            raise HTTPException(400, "scheduled runs cannot use the demo catalog")
        trigger = trigger.model_copy(update={"source": "scheduled"})

    # Idempotency is checked before the lease, so a retry of a key that
    # already landed returns the original job rather than colliding with the
    # run it started and getting a 409. The check is not itself atomic —
    # two simultaneous retries can both miss here — which is why the unique
    # index is re-caught around `save_job` below.
    if trigger.idempotency_key:
        existing = app.state.repo.get_job_by_key(founder_id, trigger.idempotency_key)
        if existing is not None:
            response.status_code = 200
            return existing

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
        source=trigger.source,
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

    # Ownership of the lease passes to the executor here: from this line on,
    # releasing it is `execute_job`'s `finally`, not this function's.
    app.state.executor.submit(job, lease)
    audit_event(
        actor=actor.subject,
        action="run.trigger",
        resource=job.job_id,
        method=actor.method,
        founder_id=founder_id,
        source=trigger.source,
    )
    return job


@app.get("/founders/{founder_id}/jobs")
def list_jobs(
    founder_id: ResourceId,
    limit: ListLimit = 20,
    actor: Principal = Depends(principal),
) -> list[RunJob]:
    """Recent jobs for one founder, newest first — running and finished alike."""
    owned(founder_id, actor)
    return app.state.repo.list_jobs(founder_id, limit)


@app.get("/founders/{founder_id}/jobs/{job_id}")
def get_job(
    founder_id: ResourceId, job_id: ResourceId, actor: Principal = Depends(principal)
) -> dict:
    """One job, with its report once the run has one.

    The poll target for the dashboard's manual-run button. `report` is null
    until the run finishes; a halted run has a report too — halting is a
    reported outcome, not an error.
    """
    owned(founder_id, actor, not_found=f"no job {job_id} for {founder_id}")
    job = app.state.repo.get_job(job_id)
    if job is None or job.founder_id != founder_id:
        raise HTTPException(404, f"no job {job_id} for {founder_id}")
    report = app.state.repo.get_run(job.run_id) if job.run_id else None
    return {"job": job, "report": report}


@app.post("/founders/{founder_id}/jobs/{job_id}/cancel")
def cancel_job(
    founder_id: ResourceId, job_id: ResourceId, actor: Principal = Depends(principal)
) -> dict:
    """Ask the executor to stop a running job.

    Cooperative: the run stops at its next await point and the job records
    `cancelled`. What the run already persisted stays persisted — cancel
    stops future work, it does not rewrite history. A job that is already
    terminal, or running in a process this API cannot reach, reports
    `cancelled: false`.
    """
    owned(
        founder_id,
        actor,
        write=True,
        scope=SCOPE_RUN_CANCEL,
        not_found=f"no job {job_id} for {founder_id}",
    )
    job = app.state.repo.get_job(job_id)
    if job is None or job.founder_id != founder_id:
        raise HTTPException(404, f"no job {job_id} for {founder_id}")
    if job.terminal():
        return {"cancelled": False, "status": job.status}
    cancelled = app.state.executor.cancel(job_id)
    # `job.status` was read before the cancel and is not re-read: a
    # successful cancel still reports `"running"` here, because the task
    # writes `cancelled` asynchronously when it reaches its next await
    # point. Callers should treat the `cancelled` flag as the answer and
    # poll `GET .../jobs/{job_id}` for the settled status.
    audit_event(
        actor=actor.subject,
        action="run.cancel",
        resource=job_id,
        outcome="ok" if cancelled else "not_running_here",
        method=actor.method,
    )
    return {"cancelled": cancelled, "status": job.status}


@app.get("/founders/{founder_id}/scheduler/failures")
def scheduler_failures(
    founder_id: ResourceId,
    limit: ListLimit = 20,
    actor: Principal = Depends(principal),
) -> list:
    """Recent invocations that failed to start or finish, newest first.

    Sanitised before persistence — no credentials, no prompts, no stack
    traces. CloudWatch keeps the archive; this answers "did last night's
    run fail?" from the dashboard.
    """
    owned(founder_id, actor)
    return app.state.failure_log.recent(founder_id, limit=limit)
