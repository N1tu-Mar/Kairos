"""Liveness, readiness and caller identity."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, Request, Response

from agent.config import settings
from api.auth import Principal
from api.deps import principal
from api.schemas import Identity

router = APIRouter()


@router.get("/health")
def health() -> dict:
    """Liveness. The process is up and serving; nothing more is claimed.

    Deliberately dependency-free. A liveness probe that checks the database
    is a liveness probe that restarts a healthy container because storage
    hiccuped, and restarting rarely fixes storage.
    """
    return {"status": "ok"}


@router.get("/ready")
def ready(request: Request, response: Response) -> dict:
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
    state = request.app.state
    checks: dict[str, str] = {}

    try:
        state.repo.get_profile("__readiness_probe__")
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
                "ok" if state.repo.schema_version() else "unmigrated"
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


@router.get("/me")
def me(actor: Principal = Depends(principal)) -> Identity:
    """What this session owns.

    Deliberately takes no founder id. A `/me/{founder_id}` would tell any
    holder of any credential whether an id exists, which is the enumeration
    that `owned()` answers 404 rather than 403 to prevent. This route can
    only ever describe its own caller, so there is nothing to authorize.

    A principal with no founders is a real answer, not an error: it is what a
    verified person looks like when auto-provisioning is off and no operator
    has granted them anything yet. The dashboard shows them a request-access
    page rather than an empty inbox.
    """
    owned_ids = sorted(actor.founder_ids)
    return Identity(
        subject=actor.subject,
        founder_id=owned_ids[0] if owned_ids else None,
        founder_ids=owned_ids,
        can_write=actor.can_write,
        method=actor.method,
    )
