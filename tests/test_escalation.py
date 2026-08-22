"""The escalation policy (Section 10.7).

"How does it decide what's worth my time?" should be answered by a file. This
is the file that proves the file does what it says.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from agent.guardrails import (
    HIGH_VALUE_THRESHOLD_USD,
    MAX_SURFACED_PER_RUN,
    REALISTIC_HOURS_PER_DAY,
    URGENT_DAYS,
    days_until,
    escalation_decision,
    is_reachable,
    rank_key,
)
from agent.models import Assessment
from tests.factories import TODAY, opportunity


def decide(assessment: Assessment, opp=None, **overrides):
    base = dict(
        assessment=assessment,
        opportunity=opp or opportunity(),
        eligibility="ELIGIBLE",
        max_application_hours=8,
        min_award=2_000,
        today=TODAY,
        already_surfaced=False,
    )
    base.update(overrides)
    return escalation_decision(**base)


def assessment(verdict="APPLY", hours=4.0, **kw) -> Assessment:
    return Assessment(verdict=verdict, reason=f"[DEMO] {verdict}", effort_hours=hours, **kw)


# ── Surface it ──────────────────────────────────────────────────────────────


def test_apply_surfaces():
    assert decide(assessment("APPLY")).surface is True


def test_maybe_with_a_resolvable_blocker_surfaces():
    """"Form an LLC" is actionable, so it is worth an interrupt."""
    decision = decide(
        assessment("MAYBE", blocker="requires an LLC", blocker_founder_resolvable=True)
    )
    assert decision.surface is True
    assert decision.kind == "MAYBE"


def test_maybe_with_an_unresolvable_blocker_stays_quiet():
    """"Restricted to PhD students" is not something the founder can fix."""
    decision = decide(
        assessment("MAYBE", blocker="phd only", blocker_founder_resolvable=False)
    )
    assert decision.surface is False
    assert "cannot resolve" in decision.reason


def test_abstention_surfaces_when_the_money_justifies_it():
    opp = opportunity(award_max=HIGH_VALUE_THRESHOLD_USD)
    decision = decide(assessment("INSUFFICIENT_INFO"), opp)

    assert decision.surface is True
    assert decision.kind == "UNKNOWN_HIGH_VALUE"
    assert "two-minute email" in decision.reason


def test_abstention_below_the_threshold_stays_quiet():
    opp = opportunity(award_min=1_000, award_max=HIGH_VALUE_THRESHOLD_USD - 1)
    assert decide(assessment("INSUFFICIENT_INFO"), opp).surface is False


def test_unknown_eligibility_on_a_large_award_surfaces():
    opp = opportunity(award_max=HIGH_VALUE_THRESHOLD_USD * 2)
    decision = decide(assessment("SKIP"), opp, eligibility="UNKNOWN")

    assert decision.surface is True
    assert decision.kind == "UNKNOWN_HIGH_VALUE"


def test_the_reason_never_characterises_the_odds():
    """Section 10.5 — we have no data on competitiveness, so we never imply any."""
    banned = ("competitive", "selective", "likely", "strong chance", "acceptance rate")
    decision = decide(assessment("INSUFFICIENT_INFO"), opportunity(award_max=50_000))

    assert not any(word in decision.reason.lower() for word in banned)


# ── Handle silently ─────────────────────────────────────────────────────────


def test_skip_stays_quiet():
    assert decide(assessment("SKIP")).surface is False


def test_already_surfaced_never_notifies_twice():
    decision = decide(assessment("APPLY"), already_surfaced=True)

    assert decision.surface is False
    assert "already surfaced" in decision.reason


def test_effort_over_the_founders_ceiling_stays_quiet():
    decision = decide(assessment("APPLY", hours=40.0), max_application_hours=8)

    assert decision.surface is False
    assert "ceiling" in decision.reason


def test_an_award_below_the_founders_floor_stays_quiet():
    opp = opportunity(award_min=200, award_max=500)
    decision = decide(assessment("APPLY"), opp, min_award=2_000)

    assert decision.surface is False
    assert "below the founder's floor" in decision.reason


def test_a_closed_deadline_stays_quiet():
    opp = opportunity(deadline=TODAY - timedelta(days=1))
    assert decide(assessment("APPLY"), opp).surface is False


def test_physically_unfinishable_work_is_noise_not_help():
    """Three days left, eight hours of work, and 1.5 usable hours a day."""
    opp = opportunity(deadline=TODAY + timedelta(days=3))
    decision = decide(assessment("APPLY", hours=8.0), opp)

    assert decision.surface is False
    assert "cannot cover" in decision.reason


def test_the_same_work_surfaces_when_there_is_time_for_it():
    opp = opportunity(deadline=TODAY + timedelta(days=30))
    assert decide(assessment("APPLY", hours=8.0), opp).surface is True


# ── The arithmetic, which is never done by a model ──────────────────────────


def test_days_until_is_computed_in_python():
    assert days_until(TODAY + timedelta(days=45), TODAY) == 45
    assert days_until(None, TODAY) is None


@pytest.mark.parametrize(
    "days,hours,reachable",
    [
        (30, 8.0, True),
        (3, 8.0, False),
        (0, 0.5, False),
        (1, 1.0, True),
        (-1, 0.5, False),
    ],
)
def test_reachability(days, hours, reachable):
    assert is_reachable(hours, TODAY + timedelta(days=days), TODAY) is reachable


def test_a_rolling_deadline_is_always_reachable():
    assert is_reachable(100.0, None, TODAY) is True


def test_ranking_is_value_per_hour():
    big_slow = rank_key(assessment(hours=20.0), opportunity(award_max=40_000))
    small_fast = rank_key(assessment(hours=2.0), opportunity(award_max=10_000))

    assert small_fast > big_slow


def test_a_maybe_ranks_below_an_apply_of_the_same_value():
    opp = opportunity(award_max=10_000)
    apply_rank = rank_key(assessment("APPLY", hours=4.0), opp)
    maybe_rank = rank_key(
        assessment("MAYBE", hours=4.0, blocker_founder_resolvable=True), opp
    )

    assert apply_rank > maybe_rank


# ── The constants are documented values, not accidents ──────────────────────


def test_the_thresholds_are_what_the_spec_says():
    assert MAX_SURFACED_PER_RUN == 3
    assert URGENT_DAYS == 7
    assert HIGH_VALUE_THRESHOLD_USD == 10_000
    assert REALISTIC_HOURS_PER_DAY == 1.5
