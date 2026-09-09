"""Deterministic state machine for conversational founder intake.

The model may propose values, never confirm them. Everything in this module
is provider-free so completion, correction, and profile writes stay testable
without AWS and cannot be changed by prompt text.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Iterable

from agent.models import (
    DegreeLevel,
    EntityType,
    FounderProfile,
    IntakeEvidence,
    IntakeFieldName,
    IntakeFieldState,
    IntakeKnowledgeClaim,
    IntakeProposalBatch,
    IntakeSession,
    IntakeWorkingMemory,
    KnowledgeChunk,
    Stage,
)
from agent.sanitize import clean

REQUIRED_FIELDS = frozenset(
    {
        "startup_description",
        "degree_level",
        "institution",
        "citizenship",
        "entity_type",
        "team_size",
        "stage",
        "funding_range",
        "equity_ok",
        "has_faculty_advisor",
        "max_application_hours",
    }
)
ALL_FIELDS = frozenset(IntakeFieldName.__args__)
DEGREES = frozenset(DegreeLevel.__args__)
ENTITIES = frozenset(EntityType.__args__)
STAGES = frozenset(Stage.__args__)
CLAIM_CATEGORIES = frozenset(
    {
        "problem",
        "solution",
        "customers",
        "market",
        "business_model",
        "differentiation",
        "team",
        "traction",
        "milestones",
        "funding_needs",
    }
)


class IntakeConflict(RuntimeError):
    """The caller edited a stale session revision."""


class IntakeIncomplete(RuntimeError):
    """Completion was requested before every required fact was confirmed."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def new_intake_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def validate_field_value(field: str, value: Any) -> Any:
    """Validate without scalar coercion and return a JSON-safe canonical value."""
    if field not in ALL_FIELDS:
        raise ValueError(f"unsupported intake field {field!r}")
    if field in {"startup_description", "institution", "citizenship"}:
        limit = 4_000 if field == "startup_description" else 300
        if not isinstance(value, str) or not value.strip() or len(value.strip()) > limit:
            raise ValueError(f"{field} must be non-empty text under {limit} characters")
        return value.strip()
    if field in {"full_name", "major"}:
        if not isinstance(value, str) or not value.strip() or len(value.strip()) > 200:
            raise ValueError(f"{field} must be non-empty text under 200 characters")
        return value.strip()
    if field == "degree_level":
        if value not in DEGREES:
            raise ValueError("invalid degree_level")
        return value
    if field == "entity_type":
        if value not in ENTITIES:
            raise ValueError("invalid entity_type")
        return value
    if field == "stage":
        if value not in STAGES:
            raise ValueError("invalid stage")
        return value
    if field in {"equity_ok", "has_faculty_advisor"}:
        if type(value) is not bool:
            raise ValueError(f"{field} must be a boolean")
        return value
    if field in {"team_size", "max_application_hours"}:
        if type(value) is not int or not 1 <= value <= 10_000:
            raise ValueError(f"{field} must be a whole number from 1 to 10000")
        return value
    if field == "funding_range":
        if (
            not isinstance(value, (list, tuple))
            or len(value) != 2
            or any(type(part) is not int or part < 0 for part in value)
            or value[0] > value[1]
        ):
            raise ValueError("funding_range must be two non-negative ordered integers")
        return [value[0], value[1]]
    if field == "geographies":
        if not isinstance(value, list) or len(value) > 500:
            raise ValueError("geographies must be a list")
        cleaned: list[str] = []
        for item in value:
            if not isinstance(item, str) or not item.strip() or len(item.strip()) > 100:
                raise ValueError("each geography must be non-empty text under 100 characters")
            cleaned.append(item.strip())
        return cleaned
    if field == "traction":
        if not isinstance(value, dict) or len(value) > 200:
            raise ValueError("traction must be a numeric object")
        cleaned_traction: dict[str, float] = {}
        for key, number in value.items():
            if not isinstance(key, str) or not key.strip() or len(key) > 100:
                raise ValueError("traction keys must be bounded text")
            if type(number) not in {int, float} or number < 0:
                raise ValueError("traction values must be non-negative numbers")
            cleaned_traction[key.strip()] = float(number)
        return cleaned_traction
    raise AssertionError(f"validator missing for {field}")


def _confirmed(
    field: IntakeFieldName, value: Any, *, actor: str, source: str
) -> IntakeFieldState:
    now = _now()
    return IntakeFieldState(
        field=field,
        status="confirmed",
        value=validate_field_value(field, value),
        confidence=1.0,
        evidence=[IntakeEvidence(source_type="existing_profile", source_id=source)],
        proposed_at=now,
        confirmed_at=now,
        confirmed_by=actor,
    )


def new_session(founder_id: str, existing: FounderProfile | None) -> IntakeSession:
    """Start from explicit stored facts, never guessed defaults."""
    fields: dict[str, IntakeFieldState] = {}
    if existing is not None:
        values: dict[str, Any] = {
            "degree_level": existing.degree_level,
            "institution": existing.institution,
            "citizenship": existing.citizenship,
            "entity_type": existing.entity_type,
            "team_size": existing.team_size,
            "stage": existing.stage,
            "traction": existing.traction,
            "funding_range": list(existing.funding_range),
            "equity_ok": existing.equity_ok,
            "has_faculty_advisor": existing.has_faculty_advisor,
            "max_application_hours": existing.max_application_hours,
            "geographies": existing.geographies,
        }
        if existing.full_name:
            values["full_name"] = existing.full_name
        if existing.major:
            values["major"] = existing.major
        for chunk in reversed(existing.knowledge_base):
            if chunk.source.startswith(("onboarding_chat", "intake:")):
                values["startup_description"] = chunk.text
                break
        for name, value in values.items():
            fields[name] = _confirmed(
                name, value, actor="existing-profile", source=existing.founder_id
            )
    existing_summary = existing.memory_summary if existing is not None else ""
    return IntakeSession(
        session_id=new_intake_id("intake"),
        founder_id=founder_id,
        fields=fields,
        memory=IntakeWorkingMemory(
            provisional_summary=existing_summary,
            confirmed_summary=existing_summary,
        ),
    )


def missing_required(session: IntakeSession) -> list[str]:
    return sorted(
        field
        for field in REQUIRED_FIELDS
        if field not in session.fields or session.fields[field].status != "confirmed"
    )


def is_complete(session: IntakeSession) -> bool:
    return session.status == "active" and not missing_required(session)


def apply_model_proposals(
    session: IntakeSession,
    proposals: Iterable[object],
    *,
    source_id: str | None = None,
    valid_evidence: dict[str, IntakeEvidence] | None = None,
) -> IntakeSession:
    """Validate model candidates and mark them proposed, never confirmed.

    Unknown fields, invalid values, and evidence pointing anywhere other
    than the persisted founder message are discarded. A model turn also
    cannot overwrite a fact the founder already confirmed.
    """
    evidence_by_id = dict(valid_evidence or {})
    if source_id is not None:
        evidence_by_id.setdefault(
            source_id, IntakeEvidence(source_type="message", source_id=source_id)
        )
    updated = session.model_copy(deep=True)
    now = _now()
    for proposal in proposals:
        field = getattr(proposal, "field", None)
        value = getattr(proposal, "value", None)
        confidence = getattr(proposal, "confidence", None)
        evidence_source_ids = getattr(proposal, "evidence_source_ids", [])
        evidence = [
            evidence_by_id[evidence_id]
            for evidence_id in evidence_source_ids
            if evidence_id in evidence_by_id
        ]
        if field not in ALL_FIELDS or not evidence:
            continue
        current = updated.fields.get(field)
        if current is not None and current.status == "confirmed":
            continue
        try:
            canonical = validate_field_value(field, value)
            numeric_confidence = float(confidence)
            if not 0 <= numeric_confidence <= 1:
                continue
        except (TypeError, ValueError):
            continue
        updated.fields[field] = IntakeFieldState(
            field=field,
            status="proposed",
            value=canonical,
            confidence=numeric_confidence,
            evidence=evidence,
            proposed_at=now,
        )
    updated.updated_at = now
    return updated


def apply_interview_memory(
    session: IntakeSession,
    *,
    field_proposals: Iterable[object],
    claim_proposals: Iterable[object],
    provisional_summary: str,
    source_message_id: str,
    valid_evidence: dict[str, IntakeEvidence],
) -> IntakeSession:
    """Publish one model turn as provisional memory and one exact batch."""
    updated = apply_model_proposals(
        session,
        field_proposals,
        source_id=source_message_id,
        valid_evidence=valid_evidence,
    )
    before_fields = session.fields
    accepted_fields = [
        name
        for name, state in updated.fields.items()
        if state.status == "proposed" and state != before_fields.get(name)
    ]
    memory = updated.memory.model_copy(deep=True)
    accepted_claim_ids: list[str] = []
    now = _now()
    for proposal in claim_proposals:
        category = getattr(proposal, "category", None)
        raw_text = getattr(proposal, "text", None)
        confidence = getattr(proposal, "confidence", None)
        evidence_ids = getattr(proposal, "evidence_source_ids", [])
        supersedes = getattr(proposal, "supersedes_claim_id", None)
        if category not in CLAIM_CATEGORIES or not isinstance(raw_text, str):
            continue
        text = clean(raw_text).strip()[:4_000]
        evidence = [
            valid_evidence[evidence_id]
            for evidence_id in evidence_ids
            if evidence_id in valid_evidence
        ]
        if not text or not evidence:
            continue
        try:
            numeric_confidence = float(confidence)
        except (TypeError, ValueError):
            continue
        if not 0 <= numeric_confidence <= 1:
            continue
        if supersedes is not None:
            prior = memory.claims.get(supersedes)
            if prior is None or prior.status not in {"proposed", "confirmed"}:
                supersedes = None
        duplicate = next(
            (
                claim
                for claim in memory.claims.values()
                if claim.status in {"proposed", "confirmed"}
                and claim.category == category
                and claim.text.casefold() == text.casefold()
            ),
            None,
        )
        if duplicate is not None:
            continue
        claim_id = new_intake_id("claim")
        memory.claims[claim_id] = IntakeKnowledgeClaim(
            claim_id=claim_id,
            category=category,
            text=text,
            confidence=numeric_confidence,
            evidence=evidence,
            supersedes_claim_id=supersedes,
            created_at=now,
            updated_at=now,
        )
        accepted_claim_ids.append(claim_id)

    memory.revision += 1
    memory.provisional_summary = clean(provisional_summary).strip()[:4_000]
    memory.updated_at = now
    updated.memory = memory
    if accepted_fields or accepted_claim_ids:
        batch_id = new_intake_id("batch")
        for claim_id in accepted_claim_ids:
            memory.claims[claim_id].proposal_batch_id = batch_id
        updated.pending_confirmation_batch = IntakeProposalBatch(
            batch_id=batch_id,
            source_message_id=source_message_id,
            field_names=accepted_fields,
            claim_ids=accepted_claim_ids,
            created_at=now,
        )
    else:
        updated.pending_confirmation_batch = None
    updated.updated_at = now
    return updated


def confirmed_memory_summary(session: IntakeSession) -> str:
    """Render only founder-confirmed state; no model-authored summary is reused."""
    lines: list[str] = []
    for name in sorted(session.fields):
        state = session.fields[name]
        if state.status == "confirmed":
            lines.append(f"{name.replace('_', ' ').title()}: {state.value}")
    claims = sorted(
        (
            claim
            for claim in session.memory.claims.values()
            if claim.status == "confirmed"
        ),
        key=lambda claim: (claim.category, claim.created_at, claim.claim_id),
    )
    lines.extend(f"{claim.category.replace('_', ' ').title()}: {claim.text}" for claim in claims)
    return "\n".join(lines)[:8_000]


def confirm_proposal_batch(
    session: IntakeSession, *, batch_id: str, actor: str
) -> IntakeSession:
    """Confirm exactly the still-pending candidates from one displayed batch."""
    if session.status != "active":
        raise IntakeConflict("only an active intake session can be confirmed")
    batch = session.pending_confirmation_batch
    if batch is None or batch.batch_id != batch_id:
        raise IntakeConflict("proposal batch is stale")
    updated = session.model_copy(deep=True)
    now = _now()
    for field in batch.field_names:
        state = updated.fields.get(field)
        if state is None or state.status != "proposed":
            continue
        state.status = "confirmed"
        state.confidence = 1.0
        state.confirmed_at = now
        state.confirmed_by = actor
    for claim_id in batch.claim_ids:
        claim = updated.memory.claims.get(claim_id)
        if claim is None or claim.status != "proposed":
            continue
        claim.status = "confirmed"
        claim.confidence = 1.0
        claim.confirmed_at = now
        claim.confirmed_by = actor
        claim.updated_at = now
        if claim.supersedes_claim_id:
            prior = updated.memory.claims.get(claim.supersedes_claim_id)
            if prior is not None and prior.status in {"proposed", "confirmed"}:
                prior.status = "superseded"
                prior.superseded_by_claim_id = claim.claim_id
                prior.updated_at = now
    updated.pending_confirmation_batch = None
    updated.memory.revision += 1
    updated.memory.confirmed_summary = confirmed_memory_summary(updated)
    updated.memory.updated_at = now
    updated.revision += 1
    updated.updated_at = now
    return updated


def update_field(
    session: IntakeSession,
    *,
    field: str,
    action: str,
    actor: str,
    value: Any = None,
    source_id: str | None = None,
) -> IntakeSession:
    """Apply an explicit founder action to a copy of the session."""
    if session.status != "active":
        raise ValueError("only an active intake session can be edited")
    if field not in ALL_FIELDS:
        raise ValueError(f"unsupported intake field {field!r}")
    updated = session.model_copy(deep=True)
    now = _now()
    if action == "reject":
        updated.fields[field] = IntakeFieldState(field=field)
    elif action in {"confirm", "correct"}:
        current = updated.fields.get(field)
        candidate = value if action == "correct" or value is not None else (
            current.value if current is not None else None
        )
        canonical = validate_field_value(field, candidate)
        evidence = list(current.evidence if current is not None else [])
        if action == "correct" and source_id:
            evidence.append(
                IntakeEvidence(source_type="founder_edit", source_id=source_id)
            )
        proposed_at = current.proposed_at if current is not None else now
        updated.fields[field] = IntakeFieldState(
            field=field,
            status="confirmed",
            value=canonical,
            confidence=1.0,
            evidence=evidence,
            proposed_at=proposed_at,
            confirmed_at=now,
            confirmed_by=actor,
        )
    else:
        raise ValueError("action must be confirm, correct, or reject")
    batch = updated.pending_confirmation_batch
    if batch is not None and field in batch.field_names:
        remaining = [name for name in batch.field_names if name != field]
        updated.pending_confirmation_batch = (
            batch.model_copy(update={"field_names": remaining})
            if remaining or batch.claim_ids
            else None
        )
    updated.memory.revision += 1
    updated.memory.confirmed_summary = confirmed_memory_summary(updated)
    updated.memory.updated_at = now
    updated.revision += 1
    updated.updated_at = now
    return updated


def update_claim(
    session: IntakeSession,
    *,
    claim_id: str,
    action: str,
    actor: str,
    source_id: str,
    text: str | None = None,
    category: str | None = None,
) -> IntakeSession:
    """Confirm, reject, or replace one narrative claim explicitly."""
    if session.status != "active":
        raise ValueError("only an active intake session can be edited")
    updated = session.model_copy(deep=True)
    current = updated.memory.claims.get(claim_id)
    if current is None or current.status in {"rejected", "superseded"}:
        raise ValueError("knowledge claim is not editable")
    now = _now()
    if action == "confirm":
        current.status = "confirmed"
        current.confidence = 1.0
        current.confirmed_at = now
        current.confirmed_by = actor
        current.updated_at = now
    elif action == "reject":
        current.status = "rejected"
        current.confirmed_at = None
        current.confirmed_by = None
        current.updated_at = now
    elif action == "correct":
        if not isinstance(text, str):
            raise ValueError("a corrected knowledge claim requires text")
        corrected = clean(text).strip()[:4_000]
        corrected_category = category or current.category
        if not corrected or corrected_category not in CLAIM_CATEGORIES:
            raise ValueError("corrected knowledge claim is invalid")
        replacement_id = new_intake_id("claim")
        current.status = "superseded"
        current.superseded_by_claim_id = replacement_id
        current.updated_at = now
        updated.memory.claims[replacement_id] = IntakeKnowledgeClaim(
            claim_id=replacement_id,
            category=corrected_category,
            text=corrected,
            status="confirmed",
            confidence=1.0,
            evidence=[
                IntakeEvidence(source_type="founder_edit", source_id=source_id)
            ],
            supersedes_claim_id=current.claim_id,
            created_at=now,
            updated_at=now,
            confirmed_at=now,
            confirmed_by=actor,
        )
    else:
        raise ValueError("action must be confirm, correct, or reject")
    batch = updated.pending_confirmation_batch
    if batch is not None and claim_id in batch.claim_ids:
        remaining = [item for item in batch.claim_ids if item != claim_id]
        updated.pending_confirmation_batch = (
            batch.model_copy(update={"claim_ids": remaining})
            if remaining or batch.field_names
            else None
        )
    updated.memory.revision += 1
    updated.memory.confirmed_summary = confirmed_memory_summary(updated)
    updated.memory.updated_at = now
    updated.revision += 1
    updated.updated_at = now
    return updated


def profile_from_session(
    session: IntakeSession, existing: FounderProfile | None
) -> FounderProfile:
    """Build a complete profile exclusively from confirmed intake state."""
    missing = missing_required(session)
    if missing:
        raise IntakeIncomplete(", ".join(missing))

    def value(name: str, fallback: Any = None) -> Any:
        state = session.fields.get(name)
        return state.value if state is not None and state.status == "confirmed" else fallback

    knowledge = list(existing.knowledge_base if existing else [])
    description = str(value("startup_description"))
    if not any(
        chunk.source == f"intake:{session.session_id}" and chunk.text == description
        for chunk in knowledge
    ):
        knowledge.append(
            KnowledgeChunk(
                chunk_id=new_intake_id("knowledge"),
                text=description,
                source=f"intake:{session.session_id}",
                confidence=1.0,
            )
        )
    for claim in session.memory.claims.values():
        if claim.status != "confirmed":
            continue
        chunk_id = f"intake:{session.session_id}:{claim.claim_id}"[:200]
        if any(chunk.chunk_id == chunk_id for chunk in knowledge):
            continue
        locations = ", ".join(
            filter(
                None,
                (
                    f"{evidence.source_type}:{evidence.source_id}"
                    + (f" ({evidence.location})" if evidence.location else "")
                    for evidence in claim.evidence
                ),
            )
        )
        knowledge.append(
            KnowledgeChunk(
                chunk_id=chunk_id,
                text=claim.text,
                source=(
                    f"intake:{session.session_id}:{claim.category}:{locations}"
                )[:500],
                confidence=1.0,
            )
        )
    return FounderProfile(
        founder_id=session.founder_id,
        full_name=value("full_name", existing.full_name if existing else None),
        degree_level=value("degree_level"),
        institution=value("institution"),
        major=value("major", existing.major if existing else None),
        citizenship=value("citizenship"),
        entity_type=value("entity_type"),
        team_size=value("team_size"),
        stage=value("stage"),
        traction=value("traction", existing.traction if existing else {}),
        funding_range=tuple(value("funding_range")),
        equity_ok=value("equity_ok"),
        has_faculty_advisor=value("has_faculty_advisor"),
        max_application_hours=value("max_application_hours"),
        geographies=value("geographies", existing.geographies if existing else []),
        reuse_eligibility_answers=(
            existing.reuse_eligibility_answers if existing else False
        ),
        knowledge_base=knowledge,
        memory_summary=confirmed_memory_summary(session),
    )
