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

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from agent.config import REPO_ROOT, settings, validate_runtime_posture, ConfigError
from agent.models import FounderProfile
from agent.scheduler import RunLock, ScheduledRunFailureLog
from agent.subagents.intake_interviewer import interview as run_intake_interview
from api import jobs as job_module
from api.auth import AuthError, audit_event, build_authenticator
from api.deps import MAX_BODY_BYTES, MAX_UPLOAD_BODY_BYTES
from api.jobs import LocalJobExecutor
from api.provisioning import provision_founder
from api.repository import SqliteRepository
from api.routes import catalog, eligibility, founders, health, inbox, intake, runs

log = logging.getLogger("kairos.api")

#: Vercel gives every preview deploy a generated subdomain, so the regex
#: matters as much as the literal origins. Without it, dashboard calls fail
#: silently in the browser while curl keeps working.
ALLOWED_ORIGINS = ["http://localhost:3000", "https://kairos.vercel.app"]
ALLOWED_ORIGIN_REGEX = r"https://kairos-[a-z0-9-]+\.vercel\.app"


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
    # Read per request by the authentication middleware, which has no other
    # way to reach settings resolved at startup.
    app.state.config = config
    # Kept on app state so offline tests can provide a deterministic fake.
    # The real callable lazily constructs the Bedrock agent on the first turn.
    app.state.intake_interviewer = run_intake_interview

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
        is_upload = (
            request.method == "POST"
            and request.url.path.endswith("/documents")
            and "/intake/sessions/" in request.url.path
        )
        limit = MAX_UPLOAD_BODY_BYTES if is_upload else MAX_BODY_BYTES
        if size > limit:
            return JSONResponse(
                {"detail": f"request body exceeds {limit} bytes"},
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
        resolved = authenticator.authenticate(request.headers.get("authorization"))
        # A verified person with no membership owns nothing and would see an
        # empty dashboard. Provisioning is separated from verification on
        # purpose — the authenticator stays a pure function of token and key —
        # so the write happens here, once, and only for a real identity.
        request.state.principal = provision_founder(
            resolved,
            request.app.state.repo,
            enabled=request.app.state.config.auto_provision_founder,
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


app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_origin_regex=ALLOWED_ORIGIN_REGEX,
    allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
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


#: Registration order is the matching order. Each router keeps its routes in
#: the order `api/main.py` declared them, and no path in one router can shadow
#: a path in another.
for module in (health, founders, intake, inbox, eligibility, runs, catalog):
    app.include_router(module.router)

__all__ = ["app", "MAX_BODY_BYTES", "MAX_UPLOAD_BODY_BYTES"]
