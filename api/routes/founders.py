"""Founder profile read and full replace."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from agent.models import FounderProfile
from api.auth import Principal, audit_event
from api.deps import ResourceId, limit_authenticated_write, owned, principal

router = APIRouter()


@router.get("/founders/{founder_id}")
def get_founder(request: Request, founder_id: ResourceId, actor: Principal = Depends(principal)) -> FounderProfile:
    """One founder profile. Ownership-checked, and 404 for a founder that is not yours."""
    state = request.app.state
    owned(founder_id, actor)
    profile = state.repo.get_profile(founder_id)
    if profile is None:
        raise HTTPException(404, f"no profile for {founder_id}")
    return profile


@router.put("/founders/{founder_id}")
def put_founder(
    request: Request,
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
    state = request.app.state
    # Profile replace is a founder write. The scheduler token has no such
    # scope, so EventBridge cannot edit a knowledge base it is only meant
    # to run against.
    owned(founder_id, actor, write=True)
    limit_authenticated_write(state, actor, founder_id)
    if profile.founder_id != founder_id:
        raise HTTPException(
            400,
            f"founder_id in the body ({profile.founder_id!r}) does not match "
            f"the path ({founder_id!r})",
        )
    state.repo.save_profile(profile)
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
    stored = state.repo.get_profile(founder_id)
    if stored is None:  # pragma: no cover - only reachable if the write vanished
        raise HTTPException(500, "profile was not persisted")
    return stored
