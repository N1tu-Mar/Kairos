"""Auditor — an independent grounding pass (Section 11.5).

The isolation is the whole design. The Auditor gets a **fresh agent with a
fresh context**: only the finished draft and the knowledge base. It never
sees the Drafter's prompt, the Drafter's reasoning, or the Drafter's own
claim about which chunk supports what. An auditor that inherits the drafter's
context inherits its mistakes.

It runs on the reasoning tier, not the cheap one. Section 3 assigns the
classification tier to high-volume eligibility parsing; this is the last
check standing between an invented number and a real funding application, and
saving tokens there is the wrong trade. Recorded in DECISIONS.md.

When the Auditor and the Drafter disagree, the Drafter loses and the field
goes back to the founder (Section 11.12).
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from agent.config import settings
from agent.models import AuditReport, Draft, FieldAudit, KnowledgeBase
from agent.prompting import structured_call
from agent.subagents.base import build_subagent

DESCRIPTION = (
    "Independently checks whether every claim in a finished draft is supported by "
    "the founder's knowledge base. Returns SUPPORTED, UNSUPPORTED or UNVERIFIABLE "
    "per field, with a quoted supporting span."
)


class ProposedAudit(BaseModel):
    fields: list[FieldAudit] = Field(default_factory=list)


def build() -> tuple:
    return build_subagent(
        name="auditor",
        prompt_name="auditor",
        description=DESCRIPTION,
        tier=settings().reasoning,
    )


def render_context(draft: Draft, kb: KnowledgeBase) -> str:
    """The Auditor's entire world: source material, then finished text.

    Note what is absent — no provenance annotations from the Drafter. Telling
    the Auditor which chunk the Drafter *believes* supports a claim is how you
    get an auditor that checks the citation instead of the claim.
    """
    chunks = "\n".join(
        f"[{c.chunk_id}] (from {c.source}) {c.text}" for c in kb.chunks
    ) or "(the knowledge base is empty)"

    traction = (
        "\n".join(f"  {k}: {v:g}" for k, v in kb.traction.items()) or "  none recorded"
    )

    audited = [f for f in draft.fields if f.status in {"GENERATED", "KNOWN", "REUSED"}]
    answers = "\n\n".join(
        f"### {f.field_id}\nQuestion: {f.question}\nAnswer as written:\n{f.answer}"
        for f in audited
    ) or "(no answered fields)"

    return f"""## Source material — the only thing that can support a claim

{chunks}

Structured traction numbers:
{traction}

## The finished draft

{answers}

For each field above, return a verdict. SUPPORTED requires a verbatim quote
from the source material. If you cannot tell either way, return UNVERIFIABLE
and say what is missing.
"""


async def audit_draft(
    agent, prompt_version: str, draft: Draft, kb: KnowledgeBase
) -> AuditReport:
    """Audit every answered field and write the verdicts back onto the draft."""
    audited = [f for f in draft.fields if f.status in {"GENERATED", "KNOWN", "REUSED"}]

    if not audited:
        return AuditReport(
            draft_id=draft.draft_id,
            model_id=settings().reasoning.model_id,
            prompt_version=prompt_version,
        )

    proposal = await structured_call(
        agent, ProposedAudit, render_context(draft, kb), agent_name="auditor"
    )

    by_field = {a.field_id: a for a in proposal.fields}
    verdicts: list[FieldAudit] = []

    for field in audited:
        found = by_field.get(field.field_id)
        if found is None:
            # A field the Auditor skipped is not a pass. Absence of an
            # opinion is not an opinion.
            found = FieldAudit(
                field_id=field.field_id,
                verdict="UNVERIFIABLE",
                note="the auditor returned no verdict for this field",
            )
        elif found.verdict == "SUPPORTED" and not (found.supporting_quote or "").strip():
            # SUPPORTED without a quote is an assertion, not a check.
            found = FieldAudit(
                field_id=field.field_id,
                verdict="UNVERIFIABLE",
                note="the auditor claimed support but quoted no supporting span",
            )
        verdicts.append(found)
        field.audit_verdict = found.verdict
        if found.verdict != "SUPPORTED":
            field.audit_note = found.note

    return AuditReport(
        draft_id=draft.draft_id,
        fields=verdicts,
        model_id=settings().reasoning.model_id,
        prompt_version=prompt_version,
    )
