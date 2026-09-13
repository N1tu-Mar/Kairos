"""Founder-answerable eligibility questions and answer-triggered reassessment."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from agent.models import EligibilityQuestion
from api import jobs as job_module
from api.auth import SCOPE_ELIGIBILITY_ANSWER, Principal, audit_event
from api.deps import (
    ResourceId,
    enforce_rate_limit,
    limit_authenticated_write,
    owned,
    principal,
    require_paid_work_capacity,
)
from api.schemas import EligibilityAnswerUpdate

router = APIRouter()


@router.get("/founders/{founder_id}/eligibility-questions")
def list_eligibility_questions(
    request: Request,
    founder_id: ResourceId,
    status: Literal["pending", "answered", "all"] = "pending",
    actor: Principal = Depends(principal),
) -> list[EligibilityQuestion]:
    """Founder-answerable uncertainty only; missing source facts do not belong here."""
    state = request.app.state
    owned(founder_id, actor)
    questions = state.repo.list_eligibility_questions(founder_id, status)
    if status == "pending":
        today = datetime.now(timezone.utc).date()
        questions = [
            question
            for question in questions
            if question.deadline is None or question.deadline >= today
        ]
    return questions


@router.put("/founders/{founder_id}/eligibility-questions/{question_id}/answer")
async def answer_eligibility_question(
    request: Request,
    founder_id: ResourceId,
    question_id: ResourceId,
    update: EligibilityAnswerUpdate,
    response: Response,
    actor: Principal = Depends(principal),
) -> EligibilityQuestion:
    """Save an answer and queue a one-opportunity reassessment when possible."""
    state = request.app.state
    not_found = f"no eligibility question {question_id} for {founder_id}"
    owned(
        founder_id,
        actor,
        write=True,
        scope=SCOPE_ELIGIBILITY_ANSWER,
        not_found=not_found,
    )
    question = state.repo.get_eligibility_question(question_id)
    if question is None or question.founder_id != founder_id:
        raise HTTPException(404, not_found)
    if question.answer == update.answer:
        response.headers["X-Kairos-Reassessment"] = "not-requested"
        return question

    opportunity = (
        state.repo.get_opportunity(question.opportunity_id)
        if update.answer in {"yes", "no"}
        else None
    )
    if opportunity is not None:
        require_paid_work_capacity(state)
        enforce_rate_limit(
            state,
            actor,
            founder_id,
            scope="eligibility_reassessment",
            limit=state.config.eligibility_reassessments_per_hour,
            window_seconds=3600,
        )
    else:
        limit_authenticated_write(state, actor, founder_id)
    updated = state.repo.answer_eligibility_question(question_id, update.answer)
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

    if opportunity is None:
        # Legacy/operator-created rows may not have a persisted source row.
        state.repo.mark_eligibility_reassessed(
            founder_id,
            question.opportunity_id,
            before=datetime.now(timezone.utc),
        )
        response.headers["X-Kairos-Reassessment"] = "unavailable"
        return updated

    if updated.answer_updated_at is None:  # pragma: no cover - yes/no always stamps it
        raise HTTPException(500, "eligibility answer timestamp was not persisted")
    reassessment_key = (
        f"eligibility:{question_id}:{update.answer}:"
        f"{updated.answer_updated_at.isoformat()}"
    )
    existing = state.repo.get_job_by_key(founder_id, reassessment_key)
    if existing is not None:
        response.headers["X-Kairos-Reassessment"] = "queued"
        response.headers["X-Kairos-Reassessment-Job"] = existing.job_id
        return updated

    lease = state.run_lock.acquire(
        founder_id=founder_id,
        run_kind=job_module.RUN_KIND,
    )
    if not lease.acquired:
        response.headers["X-Kairos-Reassessment"] = "deferred"
        return updated

    job = job_module.new_job(
        founder_id=founder_id,
        idempotency_key=reassessment_key,
        source="eligibility_answer",
        use_demo_catalog=False,
        include_grants_gov=False,
        target_opportunity_id=question.opportunity_id,
    )
    try:
        state.repo.save_job(job)
    except Exception:
        lease.release()
        existing = state.repo.get_job_by_key(founder_id, reassessment_key)
        if existing is None:
            raise
        response.headers["X-Kairos-Reassessment"] = "queued"
        response.headers["X-Kairos-Reassessment-Job"] = existing.job_id
        return updated
    state.executor.submit(job, lease)
    response.headers["X-Kairos-Reassessment"] = "queued"
    response.headers["X-Kairos-Reassessment-Job"] = job.job_id
    return updated
