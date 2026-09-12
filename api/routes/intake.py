"""Conversational intake: sessions, documents, messages and fact decisions."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, Request, Response, UploadFile

from agent.budget import BudgetExceeded, UnenforceableSpendCap
from agent.intake import (
    IntakeConflict,
    IntakeIncomplete,
    apply_interview_memory,
    confirm_proposal_batch,
    is_complete as intake_is_complete,
    missing_required,
    new_intake_id,
    new_session,
    profile_from_session,
    update_claim,
    update_field,
)
from agent.intake_documents import (
    DocumentRejected,
    MAX_FILE_BYTES,
    extract_upload,
    safe_filename,
)
from agent.models import (
    FounderProfile,
    IntakeDocument,
    IntakeEvidence,
    IntakeMessage,
    IntakeSession,
)
from agent.prompting import Abstention, Throttled
from agent.sanitize import clean
from agent.subagents.intake_interviewer import concise_reply
from api.auth import Principal, audit_event
from api.deps import ResourceId, limit_authenticated_write, owned, principal
from api.schemas import (
    IntakeBatchConfirmation,
    IntakeClaimUpdate,
    IntakeEvidenceView,
    IntakeFieldUpdate,
    IntakeMessageCreate,
    IntakeRevision,
    IntakeSessionView,
)

log = logging.getLogger("kairos.api")

router = APIRouter()


def _intake_for_founder(state, founder_id: str, session_id: str) -> IntakeSession:
    not_found = f"no intake session {session_id} for {founder_id}"
    intake = state.repo.get_intake_session(session_id)
    if intake is None or intake.founder_id != founder_id:
        raise HTTPException(404, not_found)
    return intake


def _intake_view(state, intake: IntakeSession) -> IntakeSessionView:
    return IntakeSessionView(
        session=intake,
        messages=state.repo.list_intake_messages(intake.session_id),
        documents=state.repo.list_intake_documents(intake.session_id),
        missing_required=missing_required(intake),
        ready_to_complete=intake_is_complete(intake),
        turn_pending=intake.pending_message_id is not None,
    )


def _intake_evidence(
    messages: list[IntakeMessage], documents: list[IntakeDocument]
) -> dict[str, IntakeEvidence]:
    """Evidence IDs from exactly the bounded context sent to the interviewer."""
    evidence = {
        message.message_id: IntakeEvidence(
            source_type="message",
            source_id=message.message_id,
            excerpt=message.text[:500],
        )
        for message in messages[-20:]
        if message.role == "founder"
    }
    chunks = [
        chunk
        for document in documents
        if document.status == "ready"
        for chunk in document.chunks
    ][:30]
    for chunk in chunks:
        evidence[chunk.chunk_id] = IntakeEvidence(
            source_type="document",
            source_id=chunk.chunk_id,
            location=chunk.location,
            excerpt=chunk.text[:500],
        )
    return evidence


def _is_unambiguous_confirmation(text: str) -> bool:
    normalized = text.casefold().strip().rstrip(".!?").strip()
    return normalized in {"yes", "correct", "confirm", "confirmed", "looks good"}


@router.post("/founders/{founder_id}/intake/sessions")
def create_or_resume_intake_session(
    request: Request,
    founder_id: ResourceId, actor: Principal = Depends(principal)
) -> IntakeSessionView:
    """Return the founder's active interview, creating one when absent."""
    state = request.app.state
    owned(founder_id, actor, write=True)
    intake = state.repo.get_active_intake_session(founder_id)
    if intake is None:
        limit_authenticated_write(state, actor, founder_id)
        intake = state.repo.create_intake_session(
            new_session(founder_id, state.repo.get_profile(founder_id))
        )
        audit_event(
            actor=actor.subject,
            action="intake.session_create",
            resource=intake.session_id,
            method=actor.method,
        )
    return _intake_view(state, intake)


@router.get("/founders/{founder_id}/intake/sessions/{session_id}")
def get_intake_session(
    request: Request,
    founder_id: ResourceId,
    session_id: ResourceId,
    actor: Principal = Depends(principal),
) -> IntakeSessionView:
    state = request.app.state
    not_found = f"no intake session {session_id} for {founder_id}"
    owned(founder_id, actor, not_found=not_found)
    return _intake_view(state, _intake_for_founder(state, founder_id, session_id))


async def _read_bounded_upload(upload: UploadFile) -> bytes:
    """Read one spooled upload without ever accepting more than 10 MB."""
    body = bytearray()
    while True:
        chunk = await upload.read(min(64 * 1024, MAX_FILE_BYTES + 1 - len(body)))
        if not chunk:
            return bytes(body)
        body.extend(chunk)
        if len(body) > MAX_FILE_BYTES:
            raise HTTPException(413, "the file exceeds the 10 MB limit")


@router.post("/founders/{founder_id}/intake/sessions/{session_id}/documents")
async def upload_intake_document(
    request: Request,
    founder_id: ResourceId,
    session_id: ResourceId,
    file: Annotated[UploadFile, File()],
    actor: Principal = Depends(principal),
) -> IntakeDocument:
    """Extract one founder-owned document; raw bytes are never persisted."""
    state = request.app.state
    not_found = f"no intake session {session_id} for {founder_id}"
    owned(founder_id, actor, write=True, not_found=not_found)
    limit_authenticated_write(state, actor, founder_id)
    intake = _intake_for_founder(state, founder_id, session_id)
    if intake.status != "active":
        raise HTTPException(409, "documents can only be added to an active intake session")
    try:
        filename = safe_filename(file.filename or "")
        media_type = file.content_type or "application/octet-stream"
        data = await _read_bounded_upload(file)
    except DocumentRejected as exc:
        raise HTTPException(422, str(exc)) from None
    finally:
        await file.close()

    document_id = new_intake_id("document")
    processing = IntakeDocument(
        document_id=document_id,
        session_id=session_id,
        founder_id=founder_id,
        filename=filename,
        media_type=media_type,
        byte_size=len(data),
        status="processing",
    )
    if not state.repo.reserve_intake_document(processing):
        raise HTTPException(409, "an intake session may contain at most two documents")
    reserved = state.repo.get_intake_document(document_id)
    if reserved is None:  # pragma: no cover - transaction just inserted it
        raise HTTPException(500, "document reservation was not persisted")
    try:
        _, extracted = await asyncio.to_thread(extract_upload, data, filename, media_type)
    except DocumentRejected as exc:
        # Failed uploads do not consume a slot or accumulate attacker-chosen
        # metadata. The caller keeps the safe error for its local status UI.
        state.repo.delete_intake_document(document_id)
        audit_event(
            actor=actor.subject,
            action="intake.document_rejected",
            resource=document_id,
            method=actor.method,
        )
        raise HTTPException(422, str(exc)) from None

    chunks = [
        chunk.model_copy(update={"chunk_id": f"{document_id}:chunk:{index}"})
        for index, chunk in enumerate(extracted, 1)
    ]
    ready = reserved.model_copy(update={"status": "ready", "chunks": chunks, "error": None})
    state.repo.save_intake_document(ready)
    audit_event(
        actor=actor.subject,
        action="intake.document_uploaded",
        resource=document_id,
        method=actor.method,
    )
    return ready


@router.delete(
    "/founders/{founder_id}/intake/sessions/{session_id}/documents/{document_id}"
)
def remove_intake_document(
    request: Request,
    founder_id: ResourceId,
    session_id: ResourceId,
    document_id: ResourceId,
    actor: Principal = Depends(principal),
) -> Response:
    state = request.app.state
    not_found = f"no intake document {document_id} for {founder_id}"
    owned(founder_id, actor, write=True, not_found=not_found)
    limit_authenticated_write(state, actor, founder_id)
    intake = _intake_for_founder(state, founder_id, session_id)
    document = state.repo.get_intake_document(document_id)
    if document is None or document.founder_id != founder_id or document.session_id != session_id:
        raise HTTPException(404, not_found)
    if intake.status != "active":
        raise HTTPException(409, "documents can only be removed from an active intake session")
    if document.status == "processing":
        raise HTTPException(409, "wait for document extraction to finish")
    if not state.repo.delete_intake_document(document_id):
        raise HTTPException(404, not_found)
    audit_event(
        actor=actor.subject,
        action="intake.document_removed",
        resource=document_id,
        method=actor.method,
    )
    return Response(status_code=204)


@router.get(
    "/founders/{founder_id}/intake/sessions/{session_id}/evidence/{source_id}"
)
def get_intake_evidence(
    request: Request,
    founder_id: ResourceId,
    session_id: ResourceId,
    source_id: ResourceId,
    actor: Principal = Depends(principal),
) -> IntakeEvidenceView:
    state = request.app.state
    not_found = f"no intake evidence {source_id} for {founder_id}"
    owned(founder_id, actor, not_found=not_found)
    _intake_for_founder(state, founder_id, session_id)
    for message in state.repo.list_intake_messages(session_id):
        if message.message_id == source_id:
            return IntakeEvidenceView(
                source_type="message", source_id=source_id, excerpt=message.text[:500]
            )
    for document in state.repo.list_intake_documents(session_id):
        if document.founder_id != founder_id:
            continue
        for chunk in document.chunks:
            if chunk.chunk_id == source_id:
                return IntakeEvidenceView(
                    source_type="document",
                    source_id=source_id,
                    location=chunk.location,
                    excerpt=chunk.text[:500],
                )
    raise HTTPException(404, not_found)


@router.post("/founders/{founder_id}/intake/sessions/{session_id}/messages")
async def send_intake_message(
    request: Request,
    founder_id: ResourceId,
    session_id: ResourceId,
    turn: IntakeMessageCreate,
    actor: Principal = Depends(principal),
) -> IntakeSessionView:
    """Persist one founder turn, call Bedrock once, and publish proposals."""
    state = request.app.state
    not_found = f"no intake session {session_id} for {founder_id}"
    owned(founder_id, actor, write=True, not_found=not_found)
    intake = _intake_for_founder(state, founder_id, session_id)
    founder_text = clean(turn.text)
    if not founder_text:
        raise HTTPException(400, "message contains no readable text")
    message = IntakeMessage(
        message_id=new_intake_id("message"),
        session_id=session_id,
        founder_id=founder_id,
        role="founder",
        text=founder_text,
        client_message_id=turn.client_message_id,
    )
    if (
        intake.pending_confirmation_batch is not None
        and _is_unambiguous_confirmation(founder_text)
    ):
        limit_authenticated_write(state, actor, founder_id)
        try:
            changed = confirm_proposal_batch(
                intake,
                batch_id=intake.pending_confirmation_batch.batch_id,
                actor=actor.subject,
            )
        except IntakeConflict:
            raise HTTPException(409, "proposal batch is stale") from None
        assistant = IntakeMessage(
            message_id=new_intake_id("message"),
            session_id=session_id,
            founder_id=founder_id,
            role="assistant",
            text="Confirmed. I updated your founder memory with those facts.",
            client_message_id=f"reply:{turn.client_message_id}",
            in_reply_to=message.message_id,
        )
        outcome = state.repo.save_intake_session_with_messages(
            changed,
            [message, assistant],
            expected_revision=turn.expected_revision,
        )
        if outcome == "duplicate":
            return _intake_view(state, _intake_for_founder(state, founder_id, session_id))
        if outcome != "saved":
            raise HTTPException(409, "intake session revision is stale")
        audit_event(
            actor=actor.subject,
            action="intake.batch_confirm",
            resource=session_id,
            method=actor.method,
        )
        return _intake_view(state, changed)
    outcome = state.repo.begin_intake_turn(
        message,
        expected_revision=turn.expected_revision,
        rate_window_start=datetime.now(timezone.utc) - timedelta(hours=1),
        founder_hour_limit=state.config.intake_turns_per_hour,
        session_turn_limit=30,
    )
    if outcome == "duplicate":
        current = _intake_for_founder(state, founder_id, session_id)
        if current.pending_message_id is not None:
            raise HTTPException(
                409, "this message is still being processed", headers={"Retry-After": "1"}
            )
        return _intake_view(state, current)
    if outcome == "rate_limited":
        audit_event(
            actor=actor.subject,
            action="rate_limit.rejected",
            resource=session_id,
            method=actor.method,
            limit="intake_model_turns",
        )
        raise HTTPException(
            429,
            "founder chat limit reached; try again later",
            headers={"Retry-After": "3600"},
        )
    if outcome == "turn_limit":
        raise HTTPException(409, "this intake session has reached its 30-turn limit")
    if outcome == "busy":
        raise HTTPException(
            409, "another message is still being processed", headers={"Retry-After": "1"}
        )
    if outcome == "inactive":
        raise HTTPException(409, "only an active intake session accepts messages")
    if outcome != "accepted":
        raise HTTPException(409, "intake session revision is stale")

    reserved = _intake_for_founder(state, founder_id, session_id)
    messages = state.repo.list_intake_messages(session_id)
    documents = state.repo.list_intake_documents(session_id)
    try:
        result = await state.intake_interviewer(
            reserved,
            messages,
            documents,
        )
    except (BudgetExceeded, UnenforceableSpendCap):
        state.repo.abort_intake_turn(
            session_id, message.message_id, expected_revision=reserved.revision
        )
        raise HTTPException(429, "chat spending limit reached; try again later") from None
    except (Abstention, Throttled):
        state.repo.abort_intake_turn(
            session_id, message.message_id, expected_revision=reserved.revision
        )
        raise HTTPException(503, "the interview assistant is temporarily unavailable") from None
    except Exception:  # noqa: BLE001 - raw provider details must never cross boundaries
        # Deliberately omit exception text and traceback: provider errors can
        # echo prompts, credentials, or document text.
        log.error("intake model turn failed", extra={"session_id": session_id})
        state.repo.abort_intake_turn(
            session_id, message.message_id, expected_revision=reserved.revision
        )
        raise HTTPException(503, "the interview assistant is temporarily unavailable") from None

    changed = apply_interview_memory(
        reserved,
        field_proposals=result.proposals,
        claim_proposals=result.claim_proposals,
        provisional_summary=result.working_summary,
        source_message_id=message.message_id,
        valid_evidence=_intake_evidence(messages, documents),
    )
    now = datetime.now(timezone.utc)
    changed.pending_message_id = None
    changed.revision = reserved.revision + 1
    changed.updated_at = now
    assistant = IntakeMessage(
        message_id=new_intake_id("message"),
        session_id=session_id,
        founder_id=founder_id,
        role="assistant",
        text=concise_reply(result.assistant_message),
        client_message_id=f"reply:{turn.client_message_id}",
        in_reply_to=message.message_id,
        created_at=now,
    )
    if not state.repo.finish_intake_turn(
        changed, assistant, expected_revision=reserved.revision
    ):
        state.repo.abort_intake_turn(
            session_id, message.message_id, expected_revision=reserved.revision
        )
        raise HTTPException(409, "intake session changed while the reply was generated")
    audit_event(
        actor=actor.subject,
        action="intake.message",
        resource=session_id,
        method=actor.method,
    )
    return _intake_view(state, changed)


@router.post(
    "/founders/{founder_id}/intake/sessions/{session_id}/proposal-batches/{batch_id}/confirm"
)
def confirm_intake_proposal_batch(
    request: Request,
    founder_id: ResourceId,
    session_id: ResourceId,
    batch_id: ResourceId,
    confirmation: IntakeBatchConfirmation,
    actor: Principal = Depends(principal),
) -> IntakeSessionView:
    """Confirm exactly the candidates the founder was shown in one batch."""
    state = request.app.state
    not_found = f"no intake session {session_id} for {founder_id}"
    owned(founder_id, actor, write=True, not_found=not_found)
    limit_authenticated_write(state, actor, founder_id)
    intake = _intake_for_founder(state, founder_id, session_id)
    if intake.pending_message_id is not None:
        raise HTTPException(409, "wait for the current chat response before confirming facts")
    if intake.revision != confirmation.expected_revision:
        raise HTTPException(409, "intake session revision is stale")
    try:
        changed = confirm_proposal_batch(
            intake, batch_id=batch_id, actor=actor.subject
        )
    except IntakeConflict:
        raise HTTPException(409, "proposal batch is stale") from None
    if not state.repo.save_intake_session(
        changed, expected_revision=confirmation.expected_revision
    ):
        raise HTTPException(409, "intake session revision is stale")
    audit_event(
        actor=actor.subject,
        action="intake.batch_confirm",
        resource=session_id,
        method=actor.method,
    )
    return _intake_view(state, changed)


@router.patch("/founders/{founder_id}/intake/sessions/{session_id}/fields/{field}")
def update_intake_field(
    request: Request,
    founder_id: ResourceId,
    session_id: ResourceId,
    field: ResourceId,
    update: IntakeFieldUpdate,
    actor: Principal = Depends(principal),
) -> IntakeSessionView:
    state = request.app.state
    not_found = f"no intake session {session_id} for {founder_id}"
    owned(founder_id, actor, write=True, not_found=not_found)
    limit_authenticated_write(state, actor, founder_id)
    intake = _intake_for_founder(state, founder_id, session_id)
    if intake.pending_message_id is not None:
        raise HTTPException(409, "wait for the current chat response before editing facts")
    if intake.revision != update.expected_revision:
        raise HTTPException(409, "intake session revision is stale")
    try:
        changed = update_field(
            intake,
            field=field,
            action=update.action,
            actor=actor.subject,
            value=update.value,
            source_id=update.client_action_id,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    if not state.repo.save_intake_session(
        changed, expected_revision=update.expected_revision
    ):
        raise HTTPException(409, "intake session revision is stale")
    audit_event(
        actor=actor.subject,
        action=f"intake.field_{update.action}",
        resource=session_id,
        method=actor.method,
        field=field,
    )
    return _intake_view(state, changed)


@router.patch("/founders/{founder_id}/intake/sessions/{session_id}/claims/{claim_id}")
def update_intake_claim(
    request: Request,
    founder_id: ResourceId,
    session_id: ResourceId,
    claim_id: ResourceId,
    update: IntakeClaimUpdate,
    actor: Principal = Depends(principal),
) -> IntakeSessionView:
    """Apply a founder-owned decision to one provisional narrative claim."""
    state = request.app.state
    not_found = f"no intake session {session_id} for {founder_id}"
    owned(founder_id, actor, write=True, not_found=not_found)
    limit_authenticated_write(state, actor, founder_id)
    intake = _intake_for_founder(state, founder_id, session_id)
    if intake.pending_message_id is not None:
        raise HTTPException(409, "wait for the current chat response before editing memory")
    if intake.revision != update.expected_revision:
        raise HTTPException(409, "intake session revision is stale")
    try:
        changed = update_claim(
            intake,
            claim_id=claim_id,
            action=update.action,
            actor=actor.subject,
            source_id=update.client_action_id,
            text=update.text,
            category=update.category,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    if not state.repo.save_intake_session(
        changed, expected_revision=update.expected_revision
    ):
        raise HTTPException(409, "intake session revision is stale")
    audit_event(
        actor=actor.subject,
        action=f"intake.claim_{update.action}",
        resource=session_id,
        method=actor.method,
        claim_id=claim_id,
    )
    return _intake_view(state, changed)


@router.post("/founders/{founder_id}/intake/sessions/{session_id}/complete")
def complete_intake_session(
    request: Request,
    founder_id: ResourceId,
    session_id: ResourceId,
    revision: IntakeRevision,
    actor: Principal = Depends(principal),
) -> FounderProfile:
    state = request.app.state
    not_found = f"no intake session {session_id} for {founder_id}"
    owned(founder_id, actor, write=True, not_found=not_found)
    limit_authenticated_write(state, actor, founder_id)
    intake = _intake_for_founder(state, founder_id, session_id)
    if intake.pending_message_id is not None:
        raise HTTPException(409, "wait for the current chat response before completing")
    if intake.revision != revision.expected_revision:
        raise HTTPException(409, "intake session revision is stale")
    try:
        profile = profile_from_session(
            intake, state.repo.get_profile(founder_id)
        )
    except IntakeIncomplete as exc:
        raise HTTPException(409, f"required intake fields are missing: {exc}") from None
    now = datetime.now(timezone.utc)
    completed = intake.model_copy(
        update={
            "status": "completed",
            "revision": intake.revision + 1,
            "updated_at": now,
            "completed_at": now,
        }
    )
    if not state.repo.complete_intake_session(
        completed, profile, expected_revision=revision.expected_revision
    ):
        raise HTTPException(409, "intake session revision is stale")
    audit_event(
        actor=actor.subject,
        action="intake.complete",
        resource=session_id,
        method=actor.method,
    )
    stored = state.repo.get_profile(founder_id)
    if stored is None:  # pragma: no cover - transaction wrote it
        raise HTTPException(500, "profile was not persisted")
    return stored


@router.delete("/founders/{founder_id}/intake/sessions/{session_id}")
def abandon_intake_session(
    request: Request,
    founder_id: ResourceId,
    session_id: ResourceId,
    revision: IntakeRevision,
    actor: Principal = Depends(principal),
) -> IntakeSessionView:
    state = request.app.state
    not_found = f"no intake session {session_id} for {founder_id}"
    owned(founder_id, actor, write=True, not_found=not_found)
    limit_authenticated_write(state, actor, founder_id)
    intake = _intake_for_founder(state, founder_id, session_id)
    if intake.pending_message_id is not None:
        raise HTTPException(409, "wait for the current chat response before abandoning")
    if intake.status != "active":
        raise HTTPException(409, "only an active intake session can be abandoned")
    if intake.revision != revision.expected_revision:
        raise HTTPException(409, "intake session revision is stale")
    now = datetime.now(timezone.utc)
    abandoned = intake.model_copy(
        update={
            "status": "abandoned",
            "revision": intake.revision + 1,
            "updated_at": now,
        }
    )
    if not state.repo.save_intake_session(
        abandoned, expected_revision=revision.expected_revision
    ):
        raise HTTPException(409, "intake session revision is stale")
    audit_event(
        actor=actor.subject,
        action="intake.abandon",
        resource=session_id,
        method=actor.method,
    )
    return _intake_view(state, abandoned)
