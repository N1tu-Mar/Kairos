"""The escalation policy: which judged opportunities are worth interrupting for.

Surfacing everything is the same as surfacing nothing. These fix the
boundary — what gets a notification, what is surfaced passively, and what
is skipped with a recorded reason.
"""

from __future__ import annotations

from datetime import timedelta

from agent.guardrails import escalation_decision
from tests.factories import TODAY, opportunity
from backend_method_suites.conftest import assessment


def test_apply_stays_quiet_when_the_deadline_is_physically_unreachable():
    decision = escalation_decision(
        assessment=assessment("APPLY", hours=20),
        opportunity=opportunity(award_max=50_000, deadline=TODAY + timedelta(days=3)),
        eligibility="ELIGIBLE",
        max_application_hours=40,
        min_award=2_000,
        today=TODAY,
        already_surfaced=False,
    )

    assert decision.surface is False
    assert "cannot cover" in decision.reason


def test_high_value_insufficient_info_surfaces_as_a_human_check():
    decision = escalation_decision(
        assessment=assessment("INSUFFICIENT_INFO", hours=2),
        opportunity=opportunity(award_max=25_000),
        eligibility="UNKNOWN",
        max_application_hours=8,
        min_award=2_000,
        today=TODAY,
        already_surfaced=False,
    )

    assert decision.surface is True
    assert decision.kind == "UNKNOWN_HIGH_VALUE"
    assert "two-minute email" in decision.reason


def test_already_surfaced_blocks_even_a_perfect_fit():
    decision = escalation_decision(
        assessment=assessment("APPLY", hours=1),
        opportunity=opportunity(award_max=100_000),
        eligibility="ELIGIBLE",
        max_application_hours=8,
        min_award=2_000,
        today=TODAY,
        already_surfaced=True,
    )

    assert decision.surface is False
    assert "already surfaced" in decision.reason
