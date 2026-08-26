from __future__ import annotations

import pytest

from agent.models import ApplicationField, DraftField
from agent.subagents.drafter import DraftProposal, ProposedField, draft_application
from tests.factories import form, kb, opportunity, profile
from backend_method_suites.conftest import FakeAgent

pytestmark = pytest.mark.asyncio


FIELDS = [
    ApplicationField(field_id="venture", label="Describe your venture."),
    ApplicationField(field_id="traction", label="Describe your traction."),
    ApplicationField(
        field_id="signature",
        label="Applicant signature",
        kind="short_text",
    ),
]


async def test_cold_start_turns_every_field_into_needs_founder_without_model_call(run_budget):
    agent = FakeAgent()

    draft = await draft_application(
        agent,
        "prompt-v1",
        draft_id="draft_cold",
        budget=run_budget,
        form=form(*FIELDS),
        opportunity=opportunity(),
        profile=profile(),
        kb=kb("only one sparse fact"),
        min_kb_chunks=5,
    )

    assert [f.status for f in draft.fields] == [
        "NEEDS_FOUNDER",
        "NEEDS_FOUNDER",
        "NEEDS_FOUNDER",
    ]
    assert agent.prompts == []


async def test_blocklisted_and_recalled_fields_are_not_sent_to_the_drafter(run_budget):
    recalled = DraftField(
        field_id="traction",
        question="Describe your traction.",
        answer="We have 40 active users.",
        status="REUSED",
        provenance=[],
        reused_from="founder_demo::describe your traction",
    )
    agent = FakeAgent(
        DraftProposal(
            fields=[
                ProposedField(
                    field_id="venture",
                    status="GENERATED",
                    answer="LabQueue schedules shared lab equipment.",
                    provenance_chunk_ids=["c0"],
                )
            ]
        )
    )

    draft = await draft_application(
        agent,
        "prompt-v1",
        draft_id="draft_recall",
        budget=run_budget,
        form=form(*FIELDS),
        opportunity=opportunity(),
        profile=profile(),
        kb=kb(
            "LabQueue schedules shared lab equipment.",
            "The team has 40 active users.",
            "The team has completed 12 interviews.",
            "The pilot ran in one campus lab.",
            "The team is two undergraduates.",
        ),
        recalled={"traction": recalled},
    )

    by_id = {field.field_id: field for field in draft.fields}
    assert by_id["venture"].status == "GENERATED"
    assert by_id["traction"].status == "REUSED"
    assert by_id["signature"].status == "NEEDS_FOUNDER"
    assert "Applicant signature" not in agent.prompts[0]
    assert "Describe your traction" not in agent.prompts[0]


async def test_missing_or_fabricated_citation_demotes_field_to_needs_founder(run_budget):
    agent = FakeAgent(
        DraftProposal(
            fields=[
                ProposedField(
                    field_id="venture",
                    status="GENERATED",
                    answer="We operate across five campuses.",
                    provenance_chunk_ids=["not-a-real-chunk"],
                )
            ]
        )
    )

    draft = await draft_application(
        agent,
        "prompt-v1",
        draft_id="draft_bad_cite",
        budget=run_budget,
        form=form(ApplicationField(field_id="venture", label="Describe your venture.")),
        opportunity=opportunity(),
        profile=profile(),
        kb=kb(
            "LabQueue schedules shared lab equipment.",
            "The team has 40 active users.",
            "The team has completed 12 interviews.",
            "The pilot ran in one campus lab.",
            "The team is two undergraduates.",
        ),
    )

    assert draft.fields[0].status == "NEEDS_FOUNDER"
    assert "not in the knowledge base" in draft.fields[0].audit_note
