"""Drafter and Auditor. Offline, with fakes standing in for Bedrock."""

from __future__ import annotations

import pytest

from agent.models import ApplicationField, AuditReport, DraftField, FieldAudit
from agent.prompting import Abstention
from agent.subagents.auditor import audit_draft, render_context as audit_context
from agent.subagents.drafter import (
    COLD_START_MESSAGE,
    DraftProposal,
    ProposedField,
    draft_application,
)
from tests.conftest import FakeAgent
from tests.factories import draft, form, generated, kb, opportunity, profile, span

pytestmark = pytest.mark.asyncio

RICH_KB = kb(
    "[DEMO] LabQueue schedules shared lab equipment.",
    "[DEMO] 40 students used it during a six-week pilot.",
    "[DEMO] We interviewed 12 lab managers.",
    "[DEMO] Two undergraduates, no faculty advisor.",
    "[DEMO] No legal entity, no revenue.",
    traction={"users": 40, "interviews": 12},
)

FORM = form(
    ApplicationField(field_id="problem", label="What problem are you solving?"),
    ApplicationField(field_id="evidence", label="What evidence do you have?"),
    ApplicationField(
        field_id="certification",
        label="I certify that this application is true and complete.",
        kind="checkbox",
    ),
)


async def run_drafter(*proposed, kb_override=None, recalled=None):
    agent = FakeAgent(DraftProposal(fields=list(proposed)))
    result = await draft_application(
        agent,
        "promptv1",
        draft_id="d1",
        form=FORM,
        opportunity=opportunity(),
        profile=profile(),
        kb=kb_override or RICH_KB,
        recalled=recalled,
    )
    return result, agent


# ── Cold start ──────────────────────────────────────────────────────────────


async def test_a_thin_knowledge_base_disables_the_drafter_entirely():
    agent = FakeAgent()  # no canned response — it must never be called
    thin = kb("[DEMO] one fact")

    result = await draft_application(
        agent,
        "promptv1",
        draft_id="d1",
        form=FORM,
        opportunity=opportunity(),
        profile=profile(),
        kb=thin,
    )

    assert agent.prompts == [], "the model must not be asked to fill gaps it cannot ground"
    assert {f.status for f in result.fields} == {"NEEDS_FOUNDER"}
    assert COLD_START_MESSAGE in result.fields[0].audit_note


# ── The blocklist runs before the model, not just after ─────────────────────


async def test_a_certification_field_is_never_even_sent_to_the_model():
    result, agent = await run_drafter(
        ProposedField(field_id="problem", status="GENERATED", answer="Lab scheduling.",
                      provenance_chunk_ids=["c0"]),
        ProposedField(field_id="evidence", status="GENERATED", answer="A pilot ran.",
                      provenance_chunk_ids=["c1"]),
    )

    assert "I certify" not in agent.prompts[0]
    certification = next(f for f in result.fields if f.field_id == "certification")
    assert certification.status == "NEEDS_FOUNDER"
    assert "blocked field type" in certification.audit_note


# ── Fabricated citations ────────────────────────────────────────────────────


async def test_a_citation_to_a_chunk_that_does_not_exist_is_demoted():
    """A receipt pointing at nothing is a fabricated receipt."""
    result, _ = await run_drafter(
        ProposedField(field_id="problem", status="GENERATED", answer="Lab scheduling.",
                      provenance_chunk_ids=["c99"]),
    )

    field = next(f for f in result.fields if f.field_id == "problem")
    assert field.status == "NEEDS_FOUNDER"
    assert field.answer is None
    assert "c99" in field.audit_note


async def test_a_generated_field_with_no_citation_at_all_is_demoted():
    result, _ = await run_drafter(
        ProposedField(field_id="problem", status="GENERATED", answer="Lab scheduling."),
    )

    assert next(f for f in result.fields if f.field_id == "problem").status == "NEEDS_FOUNDER"


async def test_a_valid_citation_becomes_a_real_source_span():
    result, _ = await run_drafter(
        ProposedField(field_id="problem", status="GENERATED", answer="Lab scheduling.",
                      provenance_chunk_ids=["c0"]),
    )

    field = next(f for f in result.fields if f.field_id == "problem")
    assert field.status == "GENERATED"
    assert field.provenance[0].chunk_id == "c0"
    assert field.provenance[0].source == "pitch_deck.pdf p.1"
    assert field.prompt_version == "promptv1"


# ── Fields the model invents or forgets ─────────────────────────────────────


async def test_an_answer_to_a_field_that_was_never_asked_is_discarded():
    result, _ = await run_drafter(
        ProposedField(field_id="problem", status="GENERATED", answer="Lab scheduling.",
                      provenance_chunk_ids=["c0"]),
        ProposedField(field_id="invented_field", status="GENERATED", answer="Hello.",
                      provenance_chunk_ids=["c0"]),
    )

    assert "invented_field" not in {f.field_id for f in result.fields}


async def test_a_field_the_model_skipped_is_flagged_not_dropped():
    result, _ = await run_drafter(
        ProposedField(field_id="problem", status="GENERATED", answer="Lab scheduling.",
                      provenance_chunk_ids=["c0"]),
    )

    evidence = next(f for f in result.fields if f.field_id == "evidence")
    assert evidence.status == "NEEDS_FOUNDER"
    assert "no answer" in evidence.audit_note


async def test_fields_come_back_in_form_order():
    result, _ = await run_drafter(
        ProposedField(field_id="evidence", status="NEEDS_FOUNDER"),
        ProposedField(field_id="problem", status="GENERATED", answer="Lab scheduling.",
                      provenance_chunk_ids=["c0"]),
    )

    assert [f.field_id for f in result.fields] == ["problem", "evidence", "certification"]


# ── Recall ──────────────────────────────────────────────────────────────────


async def test_a_recalled_answer_is_never_re_asked():
    reused = DraftField(
        field_id="problem",
        question="What problem are you solving?",
        answer="[DEMO] Previously answered.",
        status="REUSED",
        provenance=[span()],
    )
    result, agent = await run_drafter(
        ProposedField(field_id="evidence", status="NEEDS_FOUNDER"),
        recalled={"problem": reused},
    )

    assert "What problem are you solving?" not in agent.prompts[0]
    problem = next(f for f in result.fields if f.field_id == "problem")
    assert problem.status == "REUSED"


async def test_counts_are_computed_in_python():
    result, _ = await run_drafter(
        ProposedField(field_id="problem", status="GENERATED", answer="Lab scheduling.",
                      provenance_chunk_ids=["c0"]),
        ProposedField(field_id="evidence", status="NEEDS_FOUNDER"),
    )

    assert result.counts() == {
        "KNOWN": 0,
        "GENERATED": 1,
        "NEEDS_FOUNDER": 2,
        "REUSED": 0,
    }


# ── Abstention ──────────────────────────────────────────────────────────────


async def test_repeated_schema_failures_end_in_an_abstention_not_a_guess():
    agent = FakeAgent(ValueError("bad json"), ValueError("bad json"), ValueError("bad json"))

    with pytest.raises(Abstention):
        await draft_application(
            agent,
            "promptv1",
            draft_id="d1",
            form=FORM,
            opportunity=opportunity(),
            profile=profile(),
            kb=RICH_KB,
        )


# ── Auditor ─────────────────────────────────────────────────────────────────


async def test_the_auditor_never_sees_the_drafters_provenance():
    """An auditor that inherits the drafter's context inherits its mistakes."""
    d = draft(generated("problem", "Lab scheduling for shared equipment."))

    context = audit_context(d, RICH_KB)

    assert "Lab scheduling for shared equipment." in context
    assert "provenance" not in context.lower()
    assert "c0" not in context.split("## The finished draft")[1]


async def test_supported_without_a_quote_is_downgraded():
    """A verdict with no evidence is an assertion, not a check."""
    d = draft(generated("problem", "Lab scheduling."))
    agent = FakeAgent(
        AuditReport(
            draft_id="d1",
            fields=[FieldAudit(field_id="problem", verdict="SUPPORTED", supporting_quote="")],
        )
    )

    report = await audit_draft(agent, "v1", d, RICH_KB)

    assert report.fields[0].verdict == "UNVERIFIABLE"
    assert "quoted no supporting span" in report.fields[0].note


async def test_a_field_the_auditor_skipped_is_not_a_pass():
    d = draft(
        generated("problem", "Lab scheduling."),
        generated("evidence", "A pilot ran."),
    )
    agent = FakeAgent(
        AuditReport(
            draft_id="d1",
            fields=[
                FieldAudit(field_id="problem", verdict="SUPPORTED", supporting_quote="LabQueue")
            ],
        )
    )

    report = await audit_draft(agent, "v1", d, RICH_KB)

    evidence = next(f for f in report.fields if f.field_id == "evidence")
    assert evidence.verdict == "UNVERIFIABLE"


async def test_verdicts_are_written_back_onto_the_draft():
    d = draft(generated("problem", "Lab scheduling."))
    agent = FakeAgent(
        AuditReport(
            draft_id="d1",
            fields=[
                FieldAudit(
                    field_id="problem", verdict="UNSUPPORTED", note="nothing supports this"
                )
            ],
        )
    )

    await audit_draft(agent, "v1", d, RICH_KB)

    assert d.fields[0].audit_verdict == "UNSUPPORTED"
    assert d.fields[0].audit_note == "nothing supports this"


async def test_a_draft_with_nothing_answered_needs_no_model_call():
    d = draft(
        DraftField(field_id="problem", question="Problem?", status="NEEDS_FOUNDER")
    )
    agent = FakeAgent()

    report = await audit_draft(agent, "v1", d, RICH_KB)

    assert agent.prompts == []
    assert report.fields == []
