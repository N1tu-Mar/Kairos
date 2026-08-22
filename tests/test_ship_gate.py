"""The ship gate. Fail-closed behaviour, including gate exceptions (Section 11.9)."""

from __future__ import annotations

import agent.guardrails as guardrails
from agent.guardrails import GATE_CHECKS, ship_gate
from agent.models import AuditReport, DraftField, FieldAudit
from tests.factories import draft, generated, kb, opportunity, span

KB = kb("We are two undergrads building a scheduling tool for lab equipment.")


def audit_for(*field_ids: str, verdict: str = "SUPPORTED") -> AuditReport:
    return AuditReport(
        draft_id="draft_1",
        fields=[
            FieldAudit(field_id=f, verdict=verdict, supporting_quote="lab equipment")
            for f in field_ids
        ],
    )


# ── Provenance ──────────────────────────────────────────────────────────────


def test_generated_field_without_provenance_is_a_hard_failure():
    d = draft(generated("why", "We build scheduling tools.", provenance=[]))

    result = ship_gate(d, KB, opportunity=opportunity())

    assert result.passed is False
    assert result.failed_check == "PROVENANCE"
    assert d.status == "BLOCKED"


def test_known_field_needs_no_provenance():
    field = DraftField(
        field_id="name", question="Project name", answer="LabQueue", status="KNOWN"
    )
    assert ship_gate(draft(field), KB, opportunity=opportunity()).passed is True


# ── Ordering: the first failure stops the chain ─────────────────────────────


def test_first_failure_stops_the_chain():
    """A draft that trips provenance never reaches the numeric check."""
    d = draft(generated("why", "We have 999999 users.", provenance=[]))

    result = ship_gate(d, KB, opportunity=opportunity())

    assert result.failed_check == "PROVENANCE"
    assert "NUMERIC_WHITELIST" not in result.checks_run


def test_checks_run_in_the_documented_order():
    d = draft(generated("why", "We build scheduling tools for lab equipment."))
    result = ship_gate(d, KB, opportunity=opportunity(), audit=audit_for("why"))

    assert result.passed is True
    assert tuple(result.checks_run) == GATE_CHECKS


# ── Auditor wins ties ───────────────────────────────────────────────────────


def test_unsupported_audit_verdict_blocks_the_draft():
    d = draft(generated("why", "We build scheduling tools for lab equipment."))

    result = ship_gate(
        d, KB, opportunity=opportunity(), audit=audit_for("why", verdict="UNSUPPORTED")
    )

    assert result.passed is False
    assert result.failed_check == "AUDITOR_VERDICT"


def test_unverifiable_audit_verdict_blocks_the_draft():
    d = draft(generated("why", "We build scheduling tools for lab equipment."))

    result = ship_gate(
        d, KB, opportunity=opportunity(), audit=audit_for("why", verdict="UNVERIFIABLE")
    )

    assert result.failed_check == "AUDITOR_VERDICT"


def test_generated_field_the_auditor_never_saw_is_blocked():
    d = draft(
        generated("why", "We build scheduling tools for lab equipment."),
        generated("how", "The tool schedules lab equipment."),
    )

    result = ship_gate(d, KB, opportunity=opportunity(), audit=audit_for("why"))

    assert result.failed_check == "AUDITOR_VERDICT"
    assert any("never audited" in v.detail for v in result.violations)


# ── Blocklist corrects, it does not fail ────────────────────────────────────


def test_blocklist_rewrites_the_field_and_the_draft_can_still_ship():
    cert = DraftField(
        field_id="cert",
        question="I certify the above is true",
        answer="Yes",
        status="KNOWN",
    )
    ok = DraftField(
        field_id="name", question="Project name", answer="LabQueue", status="KNOWN"
    )
    d = draft(cert, ok)

    result = ship_gate(d, KB, opportunity=opportunity())

    assert result.passed is True
    assert cert.status == "NEEDS_FOUNDER"
    assert [v.severity for v in result.violations] == ["FORCED_NEEDS_FOUNDER"]


# ── Completeness ────────────────────────────────────────────────────────────


def test_empty_answer_on_a_non_needs_founder_field_blocks():
    field = DraftField(field_id="name", question="Project name", answer="  ", status="KNOWN")

    result = ship_gate(draft(field), KB, opportunity=opportunity())

    assert result.failed_check == "COMPLETENESS"


def test_missing_required_field_blocks():
    field = DraftField(
        field_id="name", question="Project name", answer="LabQueue", status="KNOWN"
    )

    result = ship_gate(
        draft(field), KB, opportunity=opportunity(), required_field_ids={"name", "budget"}
    )

    assert result.failed_check == "COMPLETENESS"
    assert result.violations[0].field_id == "budget"


def test_needs_founder_field_may_be_blank():
    field = DraftField(
        field_id="budget", question="Requested budget", answer=None, status="NEEDS_FOUNDER"
    )
    assert ship_gate(draft(field), KB, opportunity=opportunity()).passed is True


# ── Fail closed ─────────────────────────────────────────────────────────────


def test_an_exception_inside_the_gate_is_never_read_as_a_pass(monkeypatch):
    """The whole point of the word 'closed'."""

    def explode(*_args, **_kwargs):
        raise RuntimeError("regex engine died")

    monkeypatch.setattr(guardrails, "extract_numbers", explode)

    d = draft(generated("why", "We build scheduling tools for lab equipment."))
    result = ship_gate(d, KB, opportunity=opportunity())

    assert result.passed is False
    assert result.failed_check == "GATE_EXCEPTION"
    assert d.status == "BLOCKED"
    assert "regex engine died" in result.violations[-1].detail


def test_gate_result_defaults_to_not_passed():
    from agent.models import GateResult

    assert GateResult().passed is False


def test_gate_always_writes_its_result_onto_the_draft():
    d = draft(generated("why", "We build scheduling tools for lab equipment.", provenance=[]))
    ship_gate(d, KB, opportunity=opportunity())

    assert d.gate_result is not None
    assert d.gate_result.failed_check == "PROVENANCE"


def test_empty_draft_does_not_silently_pass_required_fields():
    result = ship_gate(draft(), KB, opportunity=opportunity(), required_field_ids={"why"})
    assert result.passed is False


def test_draft_with_only_provenance_bearing_generated_fields_passes():
    d = draft(
        DraftField(
            field_id="why",
            question="Why this project?",
            answer="A scheduling tool for lab equipment.",
            status="GENERATED",
            provenance=[span(text="scheduling tool for lab equipment")],
            audit_verdict="SUPPORTED",
        )
    )
    assert ship_gate(d, KB, opportunity=opportunity()).passed is True
