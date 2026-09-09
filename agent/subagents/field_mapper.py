"""Conservative semantic mapping from application fields to confirmed memory."""

from __future__ import annotations

import json
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from agent.guardrails import blocklisted
from agent.model_routing import call_receipt, route_for, usage_snapshot
from agent.models import (
    ApplicationField,
    DraftField,
    FounderProfile,
    KnowledgeBase,
    ModelCallReceipt,
)
from agent.prompting import structured_call
from agent.sanitize import wrap_untrusted
from agent.semantic import LexicalMatcher, is_reusable
from agent.subagents.base import build_subagent

DESCRIPTION = (
    "Determines whether bounded, founder-confirmed evidence answers one application "
    "field. It cites evidence IDs and abstains on ambiguity."
)
MAX_CANDIDATES = 5
MAX_CANDIDATE_CHARS = 4_000
MIN_CONFIDENCE = 0.90
Transformation = Literal[
    "none", "exact_reuse", "structured_alias", "verbatim", "paraphrase", "synthesis"
]


class FieldMappingDecision(BaseModel):
    """Strict output the model may propose; the server validates every ID."""

    model_config = ConfigDict(extra="forbid")

    answered: bool
    matched_chunk_ids: list[str] = Field(default_factory=list, max_length=MAX_CANDIDATES)
    confidence: float = Field(ge=0.0, le=1.0)
    transformation_type: Literal["verbatim", "paraphrase", "synthesis"]
    same_polarity: bool
    compatible_constraints: bool
    abstention_reason: str = Field(default="", max_length=500)


class FieldResolution(BaseModel):
    """Server-owned resolution handed to drafting, never raw model output."""

    model_config = ConfigDict(extra="forbid")

    field_id: str
    status: Literal["MATCHED", "NEEDS_FOUNDER"]
    answer: str | None = None
    matched_chunk_ids: list[str] = Field(default_factory=list, max_length=MAX_CANDIDATES)
    confidence: float = Field(ge=0.0, le=1.0)
    transformation_type: Transformation = "none"
    abstention_reason: str = Field(default="", max_length=500)
    reused_from: str | None = None
    model_call: ModelCallReceipt | None = None

    @model_validator(mode="after")
    def coherent(self) -> FieldResolution:
        if self.status == "MATCHED":
            if self.transformation_type == "none":
                raise ValueError("a matched field requires a transformation type")
            if not self.answer and not self.matched_chunk_ids:
                raise ValueError("a matched field requires an answer or confirmed evidence")
        elif self.answer is not None or self.matched_chunk_ids:
            raise ValueError("an unresolved field cannot carry an answer or evidence")
        return self


def build() -> tuple:
    return build_subagent(
        name="application-field-mapper",
        prompt_name="field_mapper",
        description=DESCRIPTION,
        role="application_field_mapper",
    )


def _normalise(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


_ALIASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("institution", ("institution", "school", "university", "college")),
    ("degree_level", ("degree", "degree level", "academic level")),
    ("major", ("major", "field of study", "program of study")),
    ("citizenship", ("citizenship", "visa status", "immigration status")),
    ("entity_type", ("entity type", "legal structure", "incorporation status")),
    ("team_size", ("team size", "number of team members", "how many founders")),
    ("stage", ("startup stage", "venture stage", "development stage")),
    ("funding_range", ("funding range", "amount of funding", "funding sought")),
    ("equity_ok", ("equity preference", "accept equity", "dilutive funding")),
    ("has_faculty_advisor", ("faculty advisor", "faculty adviser", "faculty mentor")),
    ("max_application_hours", ("application hours", "time available to apply")),
    ("geographies", ("geographic eligibility", "location", "residency")),
)

_ALIAS_PATTERNS = {
    "institution": re.compile(
        r"\b(institution|school|university|college)\b.*"
        r"\b(attend|enroll|study|name)\b|\b(attend|enroll|study)\b.*"
        r"\b(institution|school|university|college)\b"
    ),
    "degree_level": re.compile(
        r"\b(degree|academic level)\b.*\b(level|pursu\w*|program|current)\b|"
        r"\b(undergraduate|masters|phd|postdoc)\b"
    ),
    "major": re.compile(r"\b(major|field of study|program of study)\b"),
    "citizenship": re.compile(r"\b(citizenship|visa status|immigration status)\b"),
    "entity_type": re.compile(r"\b(entity type|legal structure|incorporation status)\b"),
    "team_size": re.compile(r"\b(team size|number of team members|how many founders)\b"),
    "stage": re.compile(r"\b(startup stage|venture stage|development stage|current stage)\b"),
    "funding_range": re.compile(
        r"\b(funding range|amount of funding|funding sought|how much funding)\b"
    ),
    "equity_ok": re.compile(
        r"\b(equity preference|accept equity|dilutive funding|willing to.*equity)\b"
    ),
    "has_faculty_advisor": re.compile(r"\b(faculty advisor|faculty adviser|faculty mentor)\b"),
    "max_application_hours": re.compile(r"\b(application hours|time available to apply)\b"),
    "geographies": re.compile(r"\b(geographic eligibility|residen|where.*located|location)\b"),
}


def _structured_alias(field: ApplicationField, profile: FounderProfile) -> FieldResolution | None:
    identifier = _normalise(field.field_id)
    question = _normalise(field.label)
    for profile_field, phrases in _ALIASES:
        identifiers = {_normalise(profile_field), *(_normalise(item) for item in phrases)}
        if identifier not in identifiers and not _ALIAS_PATTERNS[profile_field].search(question):
            continue
        value = getattr(profile, profile_field)
        if value is None or value == "" or value == []:
            return None
        if isinstance(value, bool):
            answer = "Yes" if value else "No"
        elif isinstance(value, tuple):
            answer = " to ".join(str(item) for item in value)
        elif isinstance(value, list):
            answer = ", ".join(str(item) for item in value)
        else:
            answer = str(value)
        return FieldResolution(
            field_id=field.field_id,
            status="MATCHED",
            answer=answer,
            matched_chunk_ids=[f"profile:{profile_field}"],
            confidence=1.0,
            transformation_type="structured_alias",
        )
    return None


def _exact_reuse(
    field: ApplicationField, previous_answers: list[DraftField]
) -> FieldResolution | None:
    key = _normalise(field.label)
    for previous in previous_answers:
        if _normalise(previous.question) != key:
            continue
        reusable, reason = is_reusable(previous, field.label)
        if not reusable:
            return FieldResolution(
                field_id=field.field_id,
                status="NEEDS_FOUNDER",
                confidence=0.0,
                abstention_reason=reason,
            )
        return FieldResolution(
            field_id=field.field_id,
            status="MATCHED",
            answer=previous.answer,
            confidence=1.0,
            transformation_type="exact_reuse",
            reused_from=previous.field_id,
        )
    return None


def _candidates(field: ApplicationField, kb: KnowledgeBase):
    matcher = LexicalMatcher()
    ranked = sorted(
        kb.chunks,
        key=lambda chunk: (
            -matcher.similarity(field.label, f"{chunk.source} {chunk.text}"),
            chunk.chunk_id,
        ),
    )
    return ranked[:MAX_CANDIDATES]


def _numbers(text: str) -> set[str]:
    return set(re.findall(r"(?<![A-Za-z])\d+(?:[,.]\d+)*(?![A-Za-z])", text))


def _needs(field: ApplicationField, reason: str, *, receipt=None) -> FieldResolution:
    return FieldResolution(
        field_id=field.field_id,
        status="NEEDS_FOUNDER",
        confidence=0.0,
        abstention_reason=reason[:500],
        model_call=receipt,
    )


async def resolve_field(
    field: ApplicationField,
    profile: FounderProfile,
    kb: KnowledgeBase,
    *,
    budget,
    previous_answers: list[DraftField] | None = None,
    agent=None,
    prompt_version: str | None = None,
    agent_factory=None,
) -> FieldResolution:
    """Resolve one field through exact, alias, then bounded Haiku matching."""
    if field.protected or blocklisted(field.label) or blocklisted(field.field_id):
        return _needs(field, "protected fields always require the founder")

    exact = _exact_reuse(field, previous_answers or [])
    if exact is not None:
        return exact

    alias = _structured_alias(field, profile)
    if alias is not None:
        return alias

    candidates = _candidates(field, kb)
    if not candidates:
        return _needs(field, "no confirmed founder evidence is available")

    if agent is None:
        if agent_factory is None:
            agent, prompt = build()
            prompt_version = prompt.version
        else:
            agent, prompt_version = agent_factory()
    if not prompt_version:
        raise ValueError("a prompt version is required for field mapping")

    candidate_payload = [
        {
            "chunk_id": chunk.chunk_id,
            "source": chunk.source,
            "text": chunk.text[:MAX_CANDIDATE_CHARS],
        }
        for chunk in candidates
    ]
    payload = json.dumps(
        {
            "field": {
                "field_id": field.field_id,
                "label": field.label,
                "kind": field.kind,
                "help_text": field.help_text,
                "options": field.options,
                "stated_limit": field.stated_limit,
            },
            "confirmed_candidates": candidate_payload,
        },
        ensure_ascii=True,
        separators=(",", ":"),
    )
    route = route_for("application_field_mapper")
    before = usage_snapshot(budget)
    decision = await structured_call(
        agent,
        FieldMappingDecision,
        "Determine whether the confirmed evidence answers this field.\n\n"
        + wrap_untrusted(payload, "application field and confirmed founder evidence"),
        agent_name="application-field-mapper",
        budget=budget,
        tier=route.tier,
    )
    receipt = call_receipt("application_field_mapper", prompt_version, before, budget)
    allowed = {chunk.chunk_id: chunk for chunk in candidates}
    selected_ids = list(dict.fromkeys(decision.matched_chunk_ids))
    if any(chunk_id not in allowed for chunk_id in selected_ids):
        return _needs(field, "the mapper cited evidence it was not given", receipt=receipt)
    if not decision.answered:
        return _needs(
            field,
            decision.abstention_reason
            or "confirmed evidence did not answer the field",
            receipt=receipt,
        )
    if decision.confidence < MIN_CONFIDENCE:
        return _needs(
            field,
            "mapping confidence was below the safe reuse threshold",
            receipt=receipt,
        )
    if not decision.same_polarity:
        return _needs(field, "the candidate has incompatible polarity", receipt=receipt)
    if not decision.compatible_constraints:
        return _needs(field, "the candidate has incompatible constraints", receipt=receipt)
    if not selected_ids:
        return _needs(field, "the mapper selected no confirmed evidence", receipt=receipt)

    question_numbers = _numbers(field.label + " " + field.help_text + " " + field.stated_limit)
    evidence_numbers = _numbers(" ".join(allowed[item].text for item in selected_ids))
    if question_numbers and not question_numbers.issubset(evidence_numbers):
        return _needs(
            field,
            "numeric constraints are not present in the selected evidence",
            receipt=receipt,
        )

    return FieldResolution(
        field_id=field.field_id,
        status="MATCHED",
        matched_chunk_ids=selected_ids,
        confidence=decision.confidence,
        transformation_type=decision.transformation_type,
        model_call=receipt,
    )
