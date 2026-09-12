"""Surfaced inbox items and drafts."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request

from api.auth import SCOPE_INBOX_WRITE, Principal, audit_event
from api.deps import (
    ListLimit,
    OptionalResourceId,
    ResourceId,
    limit_authenticated_write,
    owned,
    principal,
)
from api.schemas import InboxStateUpdate

router = APIRouter()


@router.get("/founders/{founder_id}/inbox")
def get_inbox(
    request: Request,
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
    state = request.app.state
    owned(founder_id, actor)
    items = state.repo.list_inbox(founder_id, limit)
    today = datetime.now(timezone.utc).date()
    items = [
        item
        for item in items
        if (
            (opportunity := state.repo.get_opportunity(item.opportunity_id))
            is None
            or opportunity.deadline is None
            or opportunity.deadline >= today
        )
    ]
    return items if include_passive else [i for i in items if not i.passive]


@router.patch("/inbox/{item_id}")
def patch_inbox_item(
    request: Request,
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
    state = request.app.state
    item = state.repo.get_inbox_item(item_id)
    if item is None:
        raise HTTPException(404, f"no inbox item {item_id}")
    owned(
        item.founder_id,
        actor,
        write=True,
        scope=SCOPE_INBOX_WRITE,
        not_found=f"no inbox item {item_id}",
    )
    limit_authenticated_write(state, actor, item.founder_id)

    updated = state.repo.set_inbox_state(item_id, update.state)
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


@router.get("/founders/{founder_id}/drafts")
def list_drafts(
    request: Request,
    founder_id: ResourceId,
    opportunity_id: OptionalResourceId = None,
    actor: Principal = Depends(principal),
) -> list[dict]:
    """Every draft for a founder, newest form first.

    Counts come from `Draft.counts()` — computed in Python, never by a model
    (Section 9, rule 8).
    """
    state = request.app.state
    owned(founder_id, actor)
    drafts = state.repo.list_drafts(founder_id, opportunity_id)
    return [{"draft": d, "counts": d.counts()} for d in drafts]


@router.get("/drafts/{draft_id}")
def get_draft(request: Request, draft_id: ResourceId, actor: Principal = Depends(principal)) -> dict:
    """One draft, ownership-checked.

    A draft is the most sensitive object in the system — it is the founder's
    knowledge base rendered into prose — so the draft's own `founder_id` is
    checked against the principal rather than trusting an opaque id.
    """
    state = request.app.state
    draft = state.repo.get_draft(draft_id)
    if draft is None:
        raise HTTPException(404, f"no draft {draft_id}")
    owned(draft.founder_id, actor, not_found=f"no draft {draft_id}")
    # Counts are computed in Python, never by a model (Section 9, rule 8).
    return {"draft": draft, "counts": draft.counts()}
