"""Mapping a Grants.gov hit onto the Opportunity model.

The boundary where an external payload becomes an internal record. A
field the API does not state must arrive as None, not as a default.
"""

from __future__ import annotations

from agent.tools.discovery import GrantsGovSource


def test_grants_gov_mapping_keeps_prose_as_criteria_not_structured_eligibility():
    hit = {
        "id": 123,
        "title": "Student Research Commercialization",
        "agency": "Example Agency",
        "agencyCode": "EX",
        "closeDate": "11/30/2026",
    }
    detail = {
        "synopsis": {
            "awardFloor": "5000",
            "awardCeiling": "25000",
            "synopsisDesc": "<p>Funds early translational research.</p>",
            "applicantEligibilityDesc": "&lt;p&gt;Open to nonprofit research institutions.&lt;/p&gt;",
            "applicantTypes": [{"id": "06", "description": "Public and State institutions"}],
        }
    }

    opp = GrantsGovSource.to_opportunity(hit, detail)

    assert opp.id == "grants_gov:123"
    assert opp.award_min == 5000
    assert opp.award_max == 25000
    assert opp.deadline.isoformat() == "2026-11-30"
    assert opp.eligibility.degree_levels is None
    assert "Open to nonprofit research institutions." in opp.criteria[0].text


def test_grants_gov_mapping_without_detail_is_still_a_valid_unknown_rich_row():
    hit = {
        "id": 456,
        "title": "Regional Innovation Prize",
        "agency": "",
        "agencyCode": "REG",
        "closeDate": "",
    }

    opp = GrantsGovSource.to_opportunity(hit, None)

    assert opp.id == "grants_gov:456"
    assert opp.funder == "REG"
    assert opp.deadline is None
    assert opp.award_max is None
    assert opp.criteria == []
