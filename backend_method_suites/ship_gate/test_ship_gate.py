"""The ship gate, the last thing between a draft and a real application.

Fail-closed: an exception inside the gate is a BLOCK, never a pass.
"""

from __future__ import annotations

from agent.guardrails import ship_gate
from agent.models import AuditReport, DraftField, FieldAudit
from tests.factories import draft, generated, kb, opportunity


def test_grounded_generated_answer_passes_all_required_checks():
    knowledge = kb(
        "The team has 40 active users after a Rutgers campus pilot.",
        traction={"users": 40},
    )
    retrieved = [opportunity(title="[DEMO] Campus Innovation Fund")]
    field = generated(
        "traction",
        "We have 40 active users and are applying to the Campus Innovation Fund.",
    )
    d = draft(field)
    audit = AuditReport(
        draft_id=d.draft_id,
        fields=[
            FieldAudit(
                field_id="traction",
                verdict="SUPPORTED",
                supporting_quote="The team has 40 active users",
            )
        ],
    )

    result = ship_gate(
        d,
        knowledge,
        retrieved=retrieved,
        opportunity=retrieved[0],
        audit=audit,
        required_field_ids={"traction"},
    )

    assert result.passed is True
    assert d.status == "READY"


def test_sensitive_certification_field_is_forced_to_founder_not_auto_filled():
    field = DraftField(
        field_id="certify_truth",
        question="I certify that this application is complete and accurate.",
        answer="Yes",
        status="KNOWN",
    )
    d = draft(field)

    result = ship_gate(d, kb("The venture helps students schedule labs."), opportunity=opportunity())

    assert result.passed is True
    assert field.status == "NEEDS_FOUNDER"
    assert field.answer is None
    assert result.violations[0].severity == "FORCED_NEEDS_FOUNDER"


def test_invented_number_blocks_before_later_checks_can_hide_it():
    d = draft(generated("traction", "We have 400 active users."))

    result = ship_gate(
        d,
        kb("The team has 40 active users.", traction={"users": 40}),
        opportunity=opportunity(),
    )

    assert result.passed is False
    assert result.failed_check == "NUMERIC_WHITELIST"
    assert "AUDITOR_VERDICT" not in result.checks_run
