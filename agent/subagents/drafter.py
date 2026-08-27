"""Drafter — fills the form from what is already known.

The only component allowed above temperature 0, and the only one that writes
prose. Everything it produces is grounding-checked afterwards by an
independent Auditor and then by `ship_gate`.

Three defenses run *before* the model is asked anything, because the cheapest
place to stop a bad answer is to never request it:

1.  **Cold start** — below `MIN_KB_CHUNKS` the Drafter is disabled entirely
    (Section 11.10). A sparse profile produces more NEEDS_FOUNDER fields,
    never more invention.
2.  **Blocklist** — certification, signature, disclosure, tax and payment
    fields are removed from the request. The model is never given the
    opportunity to answer them, and `ship_gate` checks again afterwards.
3.  **Recall** — a question the founder already answered is filled from
    history and never re-asked (Section 9, rule 3).

And one after: a citation naming a chunk that does not exist in the
knowledge base is a fabricated receipt, so that field is demoted to
NEEDS_FOUNDER regardless of how good the prose is.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from agent.config import settings
from agent.guardrails import MIN_KB_CHUNKS, blocklisted
from agent.models import (
    ApplicationForm,
    Draft,
    DraftField,
    FounderProfile,
    KnowledgeBase,
    Opportunity,
    SourceSpan,
)
from agent.prompting import structured_call
from agent.sanitize import wrap_untrusted
from agent.subagents.base import build_subagent

DESCRIPTION = (
    "Fills an application form from the founder's knowledge base. Classifies every "
    "field KNOWN, REUSED, GENERATED or NEEDS_FOUNDER, and cites a source chunk for "
    "everything it writes."
)

COLD_START_MESSAGE = (
    "I can find and judge opportunities now. Give me your deck and I can draft "
    "most of the next one."
)


class ProposedField(BaseModel):
    """What the model is allowed to return per field.

    Deliberately narrower than `DraftField`: no `model_id`, no
    `prompt_version`, no `audit_verdict`. A component that can write its own
    receipt can write a false one, so those are stamped by the caller.
    """

    field_id: str
    status: Literal["KNOWN", "GENERATED", "NEEDS_FOUNDER", "REUSED"]
    answer: str | None = None
    #: Chunk IDs from the provided knowledge base. Verified to exist.
    provenance_chunk_ids: list[str] = Field(default_factory=list)
    #: On NEEDS_FOUNDER: what the founder has to supply.
    needs_reason: str = ""


class DraftProposal(BaseModel):
    fields: list[ProposedField]


def build() -> tuple:
    return build_subagent(
        name="drafter",
        prompt_name="drafter",
        description=DESCRIPTION,
        tier=settings().reasoning,
        temperature=settings().drafting_temperature,
    )


def render_context(
    form: ApplicationForm,
    opportunity: Opportunity,
    profile: FounderProfile,
    kb: KnowledgeBase,
    askable_field_ids: set[str],
) -> str:
    chunks = "\n".join(
        f"[{c.chunk_id}] (from {c.source}) {c.text}" for c in kb.chunks
    ) or "(the knowledge base is empty)"

    traction = (
        "\n".join(f"  {k}: {v:g}" for k, v in kb.traction.items())
        or "  none recorded"
    )

    questions = "\n".join(
        f"- {f.field_id}: {f.label}"
        + (f" (max {f.max_chars} characters)" if f.max_chars else "")
        + (f" [{f.help_text}]" if f.help_text else "")
        for f in form.fields
        if f.field_id in askable_field_ids
    )

    return f"""## Knowledge base — the complete set of facts you may assert

{chunks}

Structured traction numbers (these are the ONLY numbers you may use for
traction; do not round them, do not restate them differently):
{traction}

## The opportunity this application is for

{opportunity.title} — {opportunity.funder}
{wrap_untrusted(opportunity.description_excerpt, opportunity.source)}

## Founder facts

Degree level: {profile.degree_level}
Institution: {profile.institution}
Stage: {profile.stage}
Team size: {profile.team_size}
Entity: {profile.entity_type}
Faculty advisor: {"yes" if profile.has_faculty_advisor else "no"}

## Fields to answer

{questions}

For each field return a status and, where you wrote something, the chunk IDs
that support it. If the knowledge base does not support an answer, return
NEEDS_FOUNDER with a one-line `needs_reason`.
"""


def _spans_for(chunk_ids: list[str], kb: KnowledgeBase) -> tuple[list[SourceSpan], list[str]]:
    """Resolve cited chunk IDs into spans. Returns `(spans, missing_ids)`."""
    by_id = {c.chunk_id: c for c in kb.chunks}
    spans: list[SourceSpan] = []
    missing: list[str] = []
    for chunk_id in chunk_ids:
        chunk = by_id.get(chunk_id)
        if chunk is None:
            missing.append(chunk_id)
            continue
        spans.append(
            SourceSpan(chunk_id=chunk.chunk_id, source=chunk.source, text=chunk.text)
        )
    return spans, missing


def cold_start_draft(
    draft_id: str, form: ApplicationForm, profile: FounderProfile
) -> Draft:
    """Every field goes to the founder, and we say why.

    Not a failure state. The agent still discovered, filtered and judged —
    it just refuses to write prose it cannot ground.
    """
    return Draft(
        draft_id=draft_id,
        founder_id=profile.founder_id,
        opportunity_id=form.opportunity_id,
        form_name=form.name,
        fields=[
            DraftField(
                field_id=f.field_id,
                question=f.label,
                status="NEEDS_FOUNDER",
                audit_note=COLD_START_MESSAGE,
            )
            for f in form.fields
        ],
    )


async def draft_application(
    agent,
    prompt_version: str,
    *,
    draft_id: str,
    budget,
    form: ApplicationForm,
    opportunity: Opportunity,
    profile: FounderProfile,
    kb: KnowledgeBase,
    recalled: dict[str, DraftField] | None = None,
    min_kb_chunks: int = MIN_KB_CHUNKS,
) -> Draft:
    """Produce a draft. Never raises on a thin knowledge base — abstains instead."""
    recalled = recalled or {}

    if kb.is_cold(min_kb_chunks):
        return cold_start_draft(draft_id, form, profile)

    fields: list[DraftField] = []
    askable: set[str] = set()

    for spec in form.fields:
        # Two independent sources of "the agent must not fill this": the
        # label pattern, and the curator who transcribed the form. Either is
        # enough. The curator sees things a regex cannot — an agreement to
        # terms, a disclosure whose label happens to contain no keyword —
        # and a flag that only advises is a flag that eventually gets ignored.
        category = blocklisted(spec.label) or blocklisted(spec.field_id)
        if not category and spec.protected:
            category = "curator_marked_protected"
        if category:
            # Never even ask. Section 10.1 is enforced before the model call
            # as well as inside the gate.
            fields.append(
                DraftField(
                    field_id=spec.field_id,
                    question=spec.label,
                    status="NEEDS_FOUNDER",
                    audit_note=f"blocked field type: {category} — only you can answer this",
                )
            )
        elif spec.field_id in recalled:
            # Never re-ask a known question (Section 9, rule 3).
            fields.append(recalled[spec.field_id])
        else:
            askable.add(spec.field_id)

    if askable:
        proposal = await structured_call(
            agent,
            DraftProposal,
            render_context(form, opportunity, profile, kb, askable),
            agent_name="drafter",
            budget=budget,
            tier="reasoning",
        )
        by_spec = {f.field_id: f for f in form.fields}

        for proposed in proposal.fields:
            spec = by_spec.get(proposed.field_id)
            if spec is None or proposed.field_id not in askable:
                # The model answered a field it was not given. Discard it
                # rather than trusting a field_id it invented.
                continue

            spans, missing = _spans_for(proposed.provenance_chunk_ids, kb)

            if proposed.status in {"GENERATED", "KNOWN", "REUSED"} and (
                missing or not spans
            ):
                # A citation pointing at a chunk that does not exist is a
                # fabricated receipt. Demote, do not repair.
                fields.append(
                    DraftField(
                        field_id=spec.field_id,
                        question=spec.label,
                        status="NEEDS_FOUNDER",
                        audit_note=(
                            f"drafter cited sources that are not in the knowledge base: "
                            f"{', '.join(missing) or 'none provided'}"
                        ),
                    )
                )
                continue

            fields.append(
                DraftField(
                    field_id=spec.field_id,
                    question=spec.label,
                    answer=proposed.answer,
                    status=proposed.status,
                    provenance=spans,
                    model_id=settings().reasoning.model_id,
                    prompt_version=prompt_version,
                    audit_note=proposed.needs_reason,
                )
            )

        # A field the model simply did not return is not silently dropped.
        answered = {f.field_id for f in fields}
        for field_id in sorted(askable - answered):
            spec = by_spec[field_id]
            fields.append(
                DraftField(
                    field_id=field_id,
                    question=spec.label,
                    status="NEEDS_FOUNDER",
                    audit_note="drafter returned no answer for this field",
                )
            )

    order = {f.field_id: i for i, f in enumerate(form.fields)}
    fields.sort(key=lambda f: order.get(f.field_id, len(order)))

    return Draft(
        draft_id=draft_id,
        founder_id=profile.founder_id,
        opportunity_id=opportunity.id,
        form_name=form.name,
        fields=fields,
    )
