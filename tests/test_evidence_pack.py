"""Evidence packs: smaller model context, unchanged grounding.

Relevant chunks are kept, unrelated chunks are left out, a knowledge base
small enough to fit is passed whole, and nothing about the pack can make the
ship gate accept a claim it would otherwise block.
"""

from __future__ import annotations

import logging

import pytest

from agent import guardrails
from agent.evidence import (
    MAX_EVIDENCE_BYTES,
    MAX_EVIDENCE_CHUNKS,
    auditor_evidence,
    drafter_evidence,
    select_evidence,
)
from agent.models import ApplicationField, KnowledgeBase, KnowledgeChunk
from agent.subagents.auditor import ProposedAudit, audit_draft, render_context as audit_context
from agent.subagents.drafter import (
    DraftProposal,
    ProposedField,
    draft_application,
    render_context as draft_context,
)
from tests.conftest import FakeAgent
from tests.factories import budget, draft, form, generated, kb, opportunity, profile, span

TRACTION = "[DEMO] A six-week pilot: 40 students booked microscopes and 12 double-bookings stopped."
TEAM = "[DEMO] The founding team is two undergraduates in materials science."


def big_kb(unrelated: int = 60) -> KnowledgeBase:
    """Two founder facts buried among many chunks about nothing the form asks."""
    chunks = [
        KnowledgeChunk(
            chunk_id=f"misc_{i}",
            text=f"[DEMO] Cafeteria menu rotation note {i}: soup, salad and sandwiches " + "x" * 300,
            source=f"notes.md#{i}",
        )
        for i in range(unrelated)
    ]
    chunks.insert(17, KnowledgeChunk(chunk_id="deck_traction", text=TRACTION, source="pitch_deck.pdf p.3"))
    chunks.insert(41, KnowledgeChunk(chunk_id="deck_team", text=TEAM, source="pitch_deck.pdf p.5"))
    return KnowledgeBase(founder_id="founder_demo", chunks=chunks, traction={"users": 40})


FORM = form(
    ApplicationField(field_id="traction", label="What traction or pilot results do you have?"),
    ApplicationField(field_id="team", label="Who is on your founding team?"),
)


# ── Selection ────────────────────────────────────────────────────────────────


def test_a_small_knowledge_base_is_passed_whole_and_prompts_are_identical():
    small = kb("[DEMO] one", "[DEMO] two", "[DEMO] three", "[DEMO] four", "[DEMO] five")
    pack = drafter_evidence(small, FORM, {"traction", "team"})

    assert pack.complete
    assert pack.kb.chunks == small.chunks
    assert draft_context(FORM, opportunity(), profile(), pack.kb, {"traction", "team"}) == draft_context(
        FORM, opportunity(), profile(), small, {"traction", "team"}
    )


def test_relevant_evidence_is_retained_and_unrelated_evidence_excluded():
    pack = drafter_evidence(big_kb(), FORM, {"traction", "team"})
    ids = [c.chunk_id for c in pack.kb.chunks]

    assert "deck_traction" in ids
    assert "deck_team" in ids
    assert not any(i.startswith("misc_") for i in ids)
    assert not pack.complete
    assert pack.total_chunks == 62


def test_selected_chunks_are_whole_with_source_and_in_kb_order():
    full = big_kb()
    pack = drafter_evidence(full, FORM, {"traction", "team"})
    originals = {c.chunk_id: c for c in full.chunks}

    for chunk in pack.kb.chunks:
        assert chunk == originals[chunk.chunk_id]
    order = [c.chunk_id for c in full.chunks if c.chunk_id in {x.chunk_id for x in pack.kb.chunks}]
    assert [c.chunk_id for c in pack.kb.chunks] == order
    assert pack.kb.traction == full.traction


def test_selection_respects_both_caps():
    related = KnowledgeBase(
        founder_id="f",
        chunks=[
            KnowledgeChunk(chunk_id=f"t{i}", text=f"[DEMO] traction pilot result {i} " + "y" * 2_000, source="s")
            for i in range(80)
        ],
    )
    pack = select_evidence(related, ["traction pilot"] * 40, per_query=3)

    assert pack.selected_chunks <= MAX_EVIDENCE_CHUNKS
    assert pack.selected_bytes <= MAX_EVIDENCE_BYTES


def test_selection_is_deterministic():
    first = drafter_evidence(big_kb(), FORM, {"traction", "team"})
    second = drafter_evidence(big_kb(), FORM, {"traction", "team"})
    assert [c.chunk_id for c in first.kb.chunks] == [c.chunk_id for c in second.kb.chunks]


def test_a_question_with_no_related_chunk_selects_nothing_for_it():
    pack = drafter_evidence(big_kb(), form(ApplicationField(field_id="budget", label="Itemized budget?")), {"budget"})
    assert pack.kb.chunks == []


# ── Drafter ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_drafter_prompt_is_bounded_and_carries_the_relevant_facts():
    agent = FakeAgent(DraftProposal(fields=[]))
    full = big_kb()

    await draft_application(
        agent, "v1", draft_id="d", budget=budget(), form=FORM,
        opportunity=opportunity(), profile=profile(), kb=full,
    )

    prompt = agent.prompts[0]
    unbounded = draft_context(FORM, opportunity(), profile(), full, {"traction", "team"})
    assert TRACTION in prompt and TEAM in prompt
    assert "Cafeteria menu" not in prompt
    assert len(prompt.encode()) < len(unbounded.encode()) / 5


@pytest.mark.asyncio
async def test_a_citation_to_a_chunk_the_drafter_was_not_shown_is_demoted():
    agent = FakeAgent(
        DraftProposal(
            fields=[
                ProposedField(
                    field_id="traction", status="GENERATED",
                    answer="We ran a pilot.", provenance_chunk_ids=["misc_3"],
                )
            ]
        )
    )
    result = await draft_application(
        agent, "v1", draft_id="d", budget=budget(), form=FORM,
        opportunity=opportunity(), profile=profile(), kb=big_kb(),
    )

    traction = next(f for f in result.fields if f.field_id == "traction")
    assert traction.status == "NEEDS_FOUNDER"
    assert "misc_3" in traction.audit_note


@pytest.mark.asyncio
async def test_an_unsupported_field_stays_blocked_by_the_gate_on_the_full_kb():
    """A field with no evidence cannot be rescued by the pack, and an invented
    number is still caught because the gate reads the whole knowledge base."""
    full = big_kb()
    agent = FakeAgent(
        DraftProposal(
            fields=[
                ProposedField(
                    field_id="traction", status="GENERATED",
                    answer="400 students used our pilot.", provenance_chunk_ids=["deck_traction"],
                )
            ]
        )
    )
    result = await draft_application(
        agent, "v1", draft_id="d", budget=budget(), form=FORM,
        opportunity=opportunity(), profile=profile(), kb=full,
    )

    team = next(f for f in result.fields if f.field_id == "team")
    assert team.status == "NEEDS_FOUNDER"

    gate = guardrails.ship_gate(result, full, opportunity=opportunity(), required_field_ids={"traction", "team"})
    assert not gate.passed
    assert gate.failed_check == "NUMERIC_WHITELIST"
    assert result.status == "BLOCKED"


def test_the_gate_still_sees_evidence_outside_any_pack():
    """Proof the gate is not narrowed: a number supported only by a chunk no
    pack would select still passes the numeric whitelist."""
    full = big_kb()
    full.chunks.append(KnowledgeChunk(chunk_id="grant_history", text="[DEMO] We won 3 hackathons.", source="cv"))
    field = generated("traction", "We won 3 hackathons.", provenance=[span("grant_history")])
    gated = draft(field)

    assert "grant_history" not in {c.chunk_id for c in drafter_evidence(full, FORM, {"traction", "team"}).kb.chunks}
    assert 3.0 in guardrails.allowed_numbers(full, None)
    guardrails.ship_gate(gated, full, opportunity=opportunity())
    assert gated.gate_result.failed_check != "NUMERIC_WHITELIST"


# ── Auditor ──────────────────────────────────────────────────────────────────


def test_the_auditor_pack_always_contains_cited_chunks():
    full = big_kb()
    answered = draft(generated("team", "Two undergraduates.", question="Who is on the team?", provenance=[span("misc_59")]))

    pack = auditor_evidence(full, answered, {"GENERATED"})
    ids = [c.chunk_id for c in pack.kb.chunks]
    assert "misc_59" in ids
    assert "deck_team" in ids
    assert len(ids) < 10


@pytest.mark.asyncio
async def test_the_auditor_prompt_is_bounded_and_hides_provenance():
    full = big_kb()
    answered = draft(
        generated("traction", "40 students used our pilot.", question="What traction do you have?", provenance=[span("deck_traction")])
    )
    agent = FakeAgent(ProposedAudit())
    await audit_draft(agent, "v1", answered, full, budget=budget())

    prompt = agent.prompts[0]
    assert TRACTION in prompt
    assert "Cafeteria menu" not in prompt
    assert len(prompt.encode()) < len(audit_context(answered, full).encode()) / 5
    assert "provenance" not in prompt.lower()


# ── Observability ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_pack_size_and_model_call_are_logged_without_content(caplog):
    caplog.set_level(logging.INFO)
    agent = FakeAgent(DraftProposal(fields=[]))

    await draft_application(
        agent, "v1", draft_id="d", budget=budget(), form=FORM,
        opportunity=opportunity(), profile=profile(), kb=big_kb(),
    )

    pack = next(r for r in caplog.records if r.msg == "evidence_pack")
    assert (pack.agent, pack.total_chunks) == ("drafter", 62)
    assert 0 < pack.selected_chunks < 62

    call = next(r for r in caplog.records if r.msg == "model_call")
    assert call.agent == "drafter"
    assert call.prompt_bytes == len(agent.prompts[0].encode())
    assert call.latency_ms >= 0
    assert call.input_tokens == 100
    assert TRACTION not in caplog.text


@pytest.mark.asyncio
async def test_every_pipeline_stage_is_timed(tmp_path, caplog):
    from agent.budget import DailyLedger, RunBudget
    from agent.dryrun import build_stub_agents
    from agent.scout import new_run_context, run_once
    from agent.tools.discovery import SeedCatalog
    from api.repository import SqliteRepository
    from tests.factories import TODAY

    caplog.set_level(logging.INFO)
    ctx = new_run_context(
        profile=profile(),
        repo=SqliteRepository("sqlite:///:memory:"),
        budget=RunBudget(max_run_tokens=1_000_000, max_assessments=25, daily_usd_cap=0.0, ledger=DailyLedger(tmp_path)),
        today=TODAY,
    )
    ctx.agents = build_stub_agents(ctx)
    await run_once(ctx, [SeedCatalog("data/opportunities.demo.json", allow_unverified=True)])

    stages = [r.stage for r in caplog.records if r.msg == "stage_complete"]
    assert stages == [
        "discover", "filter_eligibility", "resolve_founder_answers", "assess",
        "persist_questions", "escalation_policy", "draft_and_audit", "persist",
    ]
