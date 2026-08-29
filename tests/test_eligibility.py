"""The deterministic filter. It decides ~90% of the outcome, so it gets real coverage."""

from __future__ import annotations

from datetime import timedelta

import pytest

from agent.models import EligibilityRules
from agent.tools.eligibility import check_opportunity, hard_eligibility_filter
from tests.factories import TODAY, opportunity, profile


def verdict(rules: EligibilityRules, **profile_overrides) -> str:
    """Run the filter for these rules and return the verdict alone.

    Profile overrides go in as keywords, so a case reads as "these rules,
    this one profile difference" and the varied field is always visible in
    the call.
    """
    return check_opportunity(
        opportunity(eligibility=rules), profile(**profile_overrides), TODAY
    ).verdict


# ── Unstated rules are UNKNOWN, never a silent pass ──────────────────────────


def test_no_stated_rules_is_unknown_not_eligible():
    result = check_opportunity(opportunity(), profile(), TODAY)
    assert result.verdict == "UNKNOWN"
    assert "DEGREE_LEVEL" in result.unknown_checks
    assert "CITIZENSHIP" in result.unknown_checks


def test_unstated_degree_requirement_does_not_become_eligible():
    rules = EligibilityRules(citizenships=["us_citizen"], entity_types=["none"])
    result = check_opportunity(opportunity(eligibility=rules), profile(), TODAY)
    assert result.verdict == "UNKNOWN"
    assert result.unknown_checks == ["DEGREE_LEVEL"]


def test_fully_stated_matching_rules_are_eligible():
    rules = EligibilityRules(
        degree_levels=["undergrad", "masters"],
        citizenships=["us_citizen"],
        entity_types=["none", "llc"],
    )
    assert verdict(rules) == "ELIGIBLE"


# ── Hard rejections ─────────────────────────────────────────────────────────


def test_degree_level_mismatch_rejects():
    rules = EligibilityRules(degree_levels=["phd", "postdoc"])
    result = check_opportunity(opportunity(eligibility=rules), profile(), TODAY)
    assert result.verdict == "INELIGIBLE"
    assert result.rejection is not None
    assert result.rejection.check == "DEGREE_LEVEL"
    assert result.rejection.founder_value == "undergrad"


def test_citizenship_mismatch_rejects():
    rules = EligibilityRules(citizenships=["us_citizen"])
    result = check_opportunity(
        opportunity(eligibility=rules), profile(citizenship="f1_visa"), TODAY
    )
    assert result.verdict == "INELIGIBLE"
    assert result.rejection.check == "CITIZENSHIP"


def test_closed_deadline_rejects():
    result = check_opportunity(
        opportunity(deadline=TODAY - timedelta(days=1)), profile(), TODAY
    )
    assert result.verdict == "INELIGIBLE"
    assert result.rejection.check == "DEADLINE"


def test_deadline_today_is_still_open():
    result = check_opportunity(opportunity(deadline=TODAY), profile(), TODAY)
    assert result.verdict != "INELIGIBLE"


def test_missing_deadline_without_rolling_is_unknown():
    result = check_opportunity(
        opportunity(deadline=None, rolling=False), profile(), TODAY
    )
    assert "DEADLINE" in result.unknown_checks


def test_rolling_deadline_needs_no_question():
    result = check_opportunity(
        opportunity(deadline=None, rolling=True), profile(), TODAY
    )
    assert "DEADLINE" not in result.unknown_checks


def test_team_size_over_cap_rejects():
    rules = EligibilityRules(max_team_size=1)
    result = check_opportunity(opportunity(eligibility=rules), profile(team_size=4), TODAY)
    assert result.verdict == "INELIGIBLE"
    assert result.rejection.check == "TEAM_SIZE"


def test_geography_mismatch_rejects():
    rules = EligibilityRules(geographies=["CA"])
    result = check_opportunity(opportunity(eligibility=rules), profile(), TODAY)
    assert result.verdict == "INELIGIBLE"
    assert result.rejection.check == "GEOGRAPHY"


def test_equity_funder_rejected_for_non_dilutive_founder():
    rules = EligibilityRules(takes_equity=True)
    result = check_opportunity(opportunity(eligibility=rules), profile(), TODAY)
    assert result.verdict == "INELIGIBLE"
    assert result.rejection.check == "EQUITY"


# ── Resolvable blockers do NOT reject ───────────────────────────────────────


def test_entity_requirement_is_a_blocker_not_a_rejection():
    """Section 10.7 names "form an LLC" as worth surfacing. It cannot be a reject."""
    rules = EligibilityRules(entity_types=["llc", "c_corp"])
    result = check_opportunity(opportunity(eligibility=rules), profile(), TODAY)
    assert result.verdict != "INELIGIBLE"
    assert [b.check for b in result.resolvable_blockers] == ["ENTITY_TYPE"]
    assert "form a llc" in result.resolvable_blockers[0].remedy


def test_faculty_pi_requirement_is_a_blocker_not_a_rejection():
    rules = EligibilityRules(requires_faculty_pi=True)
    result = check_opportunity(opportunity(eligibility=rules), profile(), TODAY)
    assert result.verdict != "INELIGIBLE"
    assert [b.check for b in result.resolvable_blockers] == ["FACULTY_PI"]


def test_faculty_pi_satisfied_produces_no_blocker():
    rules = EligibilityRules(requires_faculty_pi=True)
    result = check_opportunity(
        opportunity(eligibility=rules), profile(has_faculty_advisor=True), TODAY
    )
    assert result.resolvable_blockers == []


def test_team_below_minimum_is_a_blocker():
    rules = EligibilityRules(min_team_size=3)
    result = check_opportunity(opportunity(eligibility=rules), profile(team_size=2), TODAY)
    assert result.verdict != "INELIGIBLE"
    assert result.resolvable_blockers[0].check == "TEAM_SIZE"


# ── Institution matching leans toward not losing money ──────────────────────


@pytest.mark.parametrize(
    "required,founder",
    [
        ("Georgia Tech", "Georgia Institute of Technology"),
        ("Georgia Institute of Technology", "Georgia Tech"),
        ("georgia institute of technology", "Georgia Institute of Technology"),
    ],
)
def test_institution_name_variants_still_match(required, founder):
    rules = EligibilityRules(institutions=[required])
    result = check_opportunity(
        opportunity(eligibility=rules), profile(institution=founder), TODAY
    )
    assert "INSTITUTION" not in result.unknown_checks


def test_unmatched_institution_is_unknown_never_ineligible():
    rules = EligibilityRules(institutions=["Stanford University"])
    result = check_opportunity(opportunity(eligibility=rules), profile(), TODAY)
    assert result.verdict == "UNKNOWN"
    assert "INSTITUTION" in result.unknown_checks


# ── Whole-set behaviour ─────────────────────────────────────────────────────


def test_filter_keeps_unknowns_and_drops_ineligibles():
    opps = [
        opportunity(id="keep_eligible", eligibility=EligibilityRules(
            degree_levels=["undergrad"], citizenships=["us_citizen"], entity_types=["none"]
        )),
        opportunity(id="keep_unknown"),
        opportunity(id="drop", eligibility=EligibilityRules(degree_levels=["phd"])),
    ]
    survivors, rejections, results = hard_eligibility_filter(opps, profile(), TODAY)

    assert [o.id for o in survivors] == ["keep_eligible", "keep_unknown"]
    assert [r.opportunity_id for r in rejections] == ["drop"]
    assert results["keep_unknown"].verdict == "UNKNOWN"
    assert len(results) == 3


def test_every_rejection_explains_itself():
    opps = [opportunity(id="drop", eligibility=EligibilityRules(degree_levels=["phd"]))]
    _, rejections, _ = hard_eligibility_filter(opps, profile(), TODAY)
    r = rejections[0]
    assert r.check and r.detail and r.founder_value and r.required_value
    assert r.opportunity_title


def test_filter_makes_no_network_or_model_calls(monkeypatch):
    """The filter must run with no credentials, no network, no model."""
    import socket

    def explode(*_args, **_kwargs):
        raise AssertionError("hard_eligibility_filter attempted network I/O")

    monkeypatch.setattr(socket, "socket", explode)
    hard_eligibility_filter([opportunity() for _ in range(50)], profile(), TODAY)
