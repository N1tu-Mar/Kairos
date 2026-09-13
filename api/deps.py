"""Shared request plumbing for every route module.

Bounds on identifiers and list sizes, the authenticated principal, the
404-not-403 ownership check, durable rate limits and the paid-work gate.
Kept in one place so a route added later inherits them by importing them.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import HTTPException, Path as PathParam, Query, Request

from agent.budget import BudgetExceeded, RunBudget, UnenforceableSpendCap
from agent.intake_documents import MAX_FILE_BYTES
from api.auth import Forbidden, Principal, audit_event, authorize

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
MAX_UPLOAD_BODY_BYTES = MAX_FILE_BYTES + 256 * 1024

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


def enforce_rate_limit(
    state,
    actor: Principal,
    founder_id: str,
    *,
    scope: str,
    limit: int,
    window_seconds: int,
) -> None:
    """Apply a durable principal/founder limit and emit a body-free audit."""
    retry_after = state.repo.take_rate_limit(
        scope,
        actor.subject,
        founder_id,
        limit=limit,
        window_seconds=window_seconds,
    )
    if retry_after is None:
        return
    audit_event(
        actor=actor.subject,
        action="rate_limit.rejected",
        resource=founder_id,
        method=actor.method,
        limit=scope,
        retry_after=retry_after,
    )
    raise HTTPException(
        429,
        "request limit reached; try again later",
        headers={"Retry-After": str(retry_after)},
    )


def require_paid_work_capacity(state) -> None:
    """Reject paid work before queueing when its spend posture is unsafe."""
    budget = RunBudget.from_settings(state.config)
    try:
        budget.require_enforceable_spend_cap()
        if (
            budget.daily_usd_cap > 0
            and budget.ledger.spent_today() >= budget.daily_usd_cap
        ):
            raise BudgetExceeded(
                "DAILY_USD_CAP", "the configured daily spending cap is exhausted"
            )
    except (BudgetExceeded, UnenforceableSpendCap):
        raise HTTPException(
            429,
            "paid-work spending limit reached; try again later",
            headers={"Retry-After": "3600"},
        ) from None


def limit_authenticated_write(state, actor: Principal, founder_id: str) -> None:
    """The shared per-minute ceiling on founder-owned writes."""
    enforce_rate_limit(
        state,
        actor,
        founder_id,
        scope="authenticated_write",
        limit=state.config.authenticated_writes_per_minute,
        window_seconds=60,
    )
