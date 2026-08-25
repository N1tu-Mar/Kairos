from __future__ import annotations

from datetime import timedelta

from agent.models import EligibilityRules
from agent.tools.eligibility import hard_eligibility_filter
from tests.factories import TODAY, opportunity, profile


OPEN_RULES = EligibilityRules(
    degree_levels=["undergrad"],
    citizenships=["us_citizen"],
    entity_types=["none"],
)


def test_mixed_catalog_keeps_unknowns_and_actionable_blockers_but_drops_hard_walls():
    opportunities = [
        opportunity(id="clear_fit", eligibility=OPEN_RULES),
        opportunity(id="unknown_rules", eligibility=EligibilityRules()),
        opportunity(
            id="needs_llc",
            eligibility=EligibilityRules(
                degree_levels=["undergrad"],
                citizenships=["us_citizen"],
                entity_types=["llc"],
            ),
        ),
        opportunity(
            id="wrong_citizenship",
            eligibility=EligibilityRules(citizenships=["us_permanent_resident"]),
        ),
        opportunity(id="expired", eligibility=OPEN_RULES, deadline=TODAY - timedelta(days=1)),
    ]

    survivors, rejections, results = hard_eligibility_filter(
        opportunities, profile(entity_type="none"), TODAY
    )

    assert [o.id for o in survivors] == ["clear_fit", "unknown_rules", "needs_llc"]
    assert [r.check for r in rejections] == ["CITIZENSHIP", "DEADLINE"]
    assert results["unknown_rules"].verdict == "UNKNOWN"
    assert results["needs_llc"].resolvable_blockers[0].check == "ENTITY_TYPE"


def test_rejections_include_human_readable_ground_truth_values():
    opportunities = [
        opportunity(
            id="phd_only",
            title="[DEMO] Doctoral Founder Award",
            eligibility=EligibilityRules(degree_levels=["phd"]),
        )
    ]

    _, rejections, _ = hard_eligibility_filter(opportunities, profile(), TODAY)

    assert len(rejections) == 1
    rejection = rejections[0]
    assert rejection.opportunity_id == "phd_only"
    assert rejection.check == "DEGREE_LEVEL"
    assert rejection.founder_value == "undergrad"
    assert rejection.required_value == "phd"
    assert "phd" in rejection.detail
