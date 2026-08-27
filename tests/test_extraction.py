"""The eligibility extraction boundary, including the adversarial cases.

Nothing here calls a model. The perception stage is represented by a
hand-written `EligibilityExtraction`, which is exactly what a sub-agent's
structured output would be — so these tests answer the question that matters:
*given an extractor that says X, what reaches the deterministic filter?*

The adversarial half covers the five ways prose defeats a naive reader:
negation, exception clauses, illustrative lists, several applicant
categories in one sentence, and two page sections that disagree.
"""

from __future__ import annotations

import pytest

from agent.models import EligibilityRules
from agent.tools.extraction import (
    EligibilityClaim,
    EligibilityExtraction,
    extract_and_verify,
    to_eligibility_rules,
    verify,
)

SOURCE = """
Eligibility

The Campus Innovation Fund is open to undergraduate and graduate students
enrolled at a US institution. Applicants must be US citizens or permanent
residents. Teams may consist of 1 to 4 members. A faculty sponsor is
required for every submission. The Fund takes no equity in funded ventures.

Postdoctoral researchers are not eligible for this program.

All majors are welcome, including but not limited to engineering, design and
public health.

Applications are open to any student except those who have won in a previous
cycle.
"""


def claim(field, value, evidence, source_ref="fund#eligibility"):
    return EligibilityClaim(
        field=field, value=value, evidence=evidence, source_ref=source_ref
    )


def extraction(*claims):
    return EligibilityExtraction(claims=list(claims))


class TestEvidenceIsRefound:
    def test_a_supported_claim_survives(self):
        result = verify(
            extraction(
                claim(
                    "degree_levels",
                    ["undergrad", "masters"],
                    "open to undergraduate and graduate students",
                )
            ),
            SOURCE,
        )
        assert result.value("degree_levels") == ["undergrad", "masters"]
        assert result.dropped == []

    def test_a_fabricated_span_is_dropped_and_the_field_stays_unknown(self):
        result = verify(
            extraction(
                claim("degree_levels", ["phd"], "open to doctoral candidates worldwide")
            ),
            SOURCE,
        )
        assert result.value("degree_levels") is None
        assert result.dropped[0].reason == "SPAN_NOT_IN_SOURCE"
        assert "degree_levels" in result.unknown_fields

    def test_a_paraphrase_is_treated_as_a_fabrication(self):
        # Same meaning, different words. The page does not contain it.
        result = verify(
            extraction(
                claim("degree_levels", ["undergrad"], "undergrads may apply to the fund")
            ),
            SOURCE,
        )
        assert result.dropped[0].reason == "SPAN_NOT_IN_SOURCE"

    def test_span_matching_survives_punctuation_and_wrapping(self):
        result = verify(
            extraction(
                claim(
                    "min_team_size",
                    1,
                    "Teams may consist of 1 to 4 members.",
                )
            ),
            SOURCE,
        )
        assert result.value("min_team_size") == 1

    def test_an_empty_span_cannot_support_anything(self):
        result = verify(extraction(claim("takes_equity", False, "")), SOURCE)
        assert result.dropped[0].reason == "SPAN_NOT_IN_SOURCE"


class TestVocabulary:
    def test_a_value_outside_the_controlled_vocabulary_is_dropped(self):
        result = verify(
            extraction(
                claim(
                    "degree_levels",
                    ["high_school"],
                    "open to undergraduate and graduate students",
                )
            ),
            SOURCE,
        )
        assert result.dropped[0].reason == "VALUE_OUT_OF_VOCABULARY"

    def test_a_field_this_boundary_does_not_own_is_dropped(self):
        result = verify(
            extraction(
                claim("gpa_minimum", 3.5, "open to undergraduate and graduate students")
            ),
            SOURCE,
        )
        assert result.dropped[0].reason == "UNKNOWN_FIELD"

    @pytest.mark.parametrize("bad", [0, -1, "two", True, 2.5])
    def test_team_size_must_be_a_positive_integer(self, bad):
        result = verify(
            extraction(claim("min_team_size", bad, "Teams may consist of 1 to 4 members")),
            SOURCE,
        )
        assert result.dropped[0].reason == "VALUE_OUT_OF_VOCABULARY"

    def test_a_boolean_field_refuses_a_string(self):
        result = verify(
            extraction(
                claim("requires_faculty_pi", "yes", "A faculty sponsor is required")
            ),
            SOURCE,
        )
        assert result.dropped[0].reason == "VALUE_OUT_OF_VOCABULARY"


class TestAdversarialNegation:
    def test_a_negated_span_cannot_grant_permission(self):
        """'Postdoctoral researchers are not eligible' is in the source, so
        the span check passes. It still must not license postdocs."""
        result = verify(
            extraction(
                claim(
                    "degree_levels",
                    ["postdoc"],
                    "Postdoctoral researchers are not eligible for this program",
                )
            ),
            SOURCE,
        )
        assert result.value("degree_levels") is None
        assert result.dropped[0].reason == "NEGATED_SPAN"

    def test_a_negated_span_may_still_support_a_negative_fact(self):
        """'takes no equity' is polarity carried by the value, not the
        sentence — this one is allowed through."""
        result = verify(
            extraction(
                claim(
                    "takes_equity",
                    False,
                    "The Fund takes no equity in funded ventures",
                )
            ),
            SOURCE,
        )
        assert result.value("takes_equity") is False


class TestAdversarialExceptions:
    def test_an_exception_clause_does_not_settle_who_qualifies(self):
        result = verify(
            extraction(
                claim(
                    "institutions",
                    ["any"],
                    "Applications are open to any student except those who have won "
                    "in a previous cycle",
                )
            ),
            SOURCE,
        )
        assert result.value("institutions") is None
        assert result.dropped[0].reason == "EXCEPTION_CLAUSE"


class TestAdversarialNonExhaustiveLists:
    def test_including_but_not_limited_to_cannot_close_a_set(self):
        result = verify(
            extraction(
                claim(
                    "geographies",
                    ["engineering"],
                    "All majors are welcome, including but not limited to engineering, "
                    "design and public health",
                )
            ),
            SOURCE,
        )
        assert result.dropped[0].reason == "NON_EXHAUSTIVE_LIST"

    def test_an_exhaustive_list_in_the_same_document_still_works(self):
        result = verify(
            extraction(
                claim(
                    "citizenships",
                    ["us_citizen", "us_permanent_resident"],
                    "Applicants must be US citizens or permanent residents",
                )
            ),
            SOURCE,
        )
        assert result.value("citizenships") == ["us_citizen", "us_permanent_resident"]


class TestMultipleApplicantCategories:
    def test_one_sentence_naming_two_categories_yields_both(self):
        result = verify(
            extraction(
                claim(
                    "degree_levels",
                    ["undergrad", "masters", "phd"],
                    "open to undergraduate and graduate students",
                )
            ),
            SOURCE,
        )
        assert result.value("degree_levels") == ["undergrad", "masters", "phd"]

    def test_two_claims_that_agree_after_reordering_do_not_conflict(self):
        result = verify(
            extraction(
                claim(
                    "citizenships",
                    ["us_citizen", "us_permanent_resident"],
                    "Applicants must be US citizens or permanent residents",
                ),
                claim(
                    "citizenships",
                    ["us_permanent_resident", "us_citizen"],
                    "must be US citizens or permanent residents",
                ),
            ),
            SOURCE,
        )
        assert result.value("citizenships") is not None
        assert result.dropped == []


class TestConflictingSections:
    def test_two_sections_that_disagree_collapse_to_unknown(self):
        result = verify(
            extraction(
                claim(
                    "max_team_size",
                    4,
                    "Teams may consist of 1 to 4 members",
                ),
                claim(
                    "max_team_size",
                    5,
                    "Teams may consist of 1 to 4 members",
                ),
            ),
            SOURCE,
        )
        assert result.value("max_team_size") is None
        assert {d.reason for d in result.dropped} == {"CONFLICTING_CLAIMS"}
        assert len(result.dropped) == 2

    def test_a_conflict_on_one_field_does_not_poison_another(self):
        result = verify(
            extraction(
                claim("max_team_size", 4, "Teams may consist of 1 to 4 members"),
                claim("max_team_size", 5, "Teams may consist of 1 to 4 members"),
                claim("requires_faculty_pi", True, "A faculty sponsor is required"),
            ),
            SOURCE,
        )
        assert result.value("max_team_size") is None
        assert result.value("requires_faculty_pi") is True


class TestProjection:
    def test_unpopulated_fields_project_to_none_not_to_a_default(self):
        rules = to_eligibility_rules(verify(extraction(), SOURCE))
        assert rules == EligibilityRules()
        assert rules.degree_levels is None
        assert rules.takes_equity is None

    def test_the_whole_boundary_end_to_end(self):
        rules, verified = extract_and_verify(
            extraction(
                claim(
                    "degree_levels",
                    ["undergrad", "masters"],
                    "open to undergraduate and graduate students",
                ),
                claim(
                    "citizenships",
                    ["us_citizen", "us_permanent_resident"],
                    "Applicants must be US citizens or permanent residents",
                ),
                claim("min_team_size", 1, "Teams may consist of 1 to 4 members"),
                claim("max_team_size", 4, "Teams may consist of 1 to 4 members"),
                claim("requires_faculty_pi", True, "A faculty sponsor is required"),
                claim("takes_equity", False, "The Fund takes no equity in funded ventures"),
                # every one of these is a trap
                claim("degree_levels", ["postdoc"], "Postdoctoral researchers are not eligible"),
                claim("institutions", ["Stanford"], "open to students at Stanford University"),
            ),
            SOURCE,
        )
        assert rules.degree_levels == ["undergrad", "masters"]
        assert rules.max_team_size == 4
        assert rules.requires_faculty_pi is True
        assert rules.takes_equity is False
        # The traps left no residue: institutions is UNKNOWN, not Stanford.
        assert rules.institutions is None
        # The postdoc trap dies on polarity before it can even become a
        # conflict with the surviving degree_levels claim; the Stanford trap
        # dies because that sentence is not on the page.
        assert {d.reason for d in verified.dropped} == {
            "NEGATED_SPAN",
            "SPAN_NOT_IN_SOURCE",
        }

    def test_extractor_self_report_cannot_populate_a_field(self):
        """An extractor claiming full coverage proves nothing; the derived
        unknown set is computed from surviving claims only."""
        result = verify(
            EligibilityExtraction(claims=[], unstated=[]),
            SOURCE,
        )
        assert set(result.unknown_fields) >= {"degree_levels", "takes_equity"}
