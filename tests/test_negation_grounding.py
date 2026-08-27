"""Adversarial negation matrix for the forbidden-claims evidence check.

Written as an independent test matrix, deliberately not derived from the two
golden-set leaks (trap_04, trap_05). The golden set found the bug; these cases
define the behaviour, so the fix is checked against cases it was not tuned on.

The rule under test: an evidence keyword match only supports a claim when the
clause it sits in has the same polarity as the claim. "There is no faculty
advisor" contains the words "faculty advisor" and supports nothing except the
absence of one.

Every case runs through the real `ship_gate`, not a private helper, so the
matrix keeps meaning even if the implementation is restructured.
"""

from __future__ import annotations

from agent.guardrails import ship_gate
from tests.factories import draft, generated, kb, opportunity


def gate(answer: str, *evidence: str, traction: dict | None = None):
    knowledge = kb(*evidence, traction=traction or {})
    d = draft(generated("field_under_test", answer))
    return ship_gate(d, knowledge, opportunity=opportunity())


def assert_blocked(result, category: str):
    assert result.passed is False
    assert result.failed_check == "FORBIDDEN_CLAIMS"
    assert any(category in v.detail for v in result.violations)


# ── positive claim / positive evidence ──────────────────────────────────────


def test_positive_advisor_claim_with_positive_evidence_ships():
    result = gate(
        "We work closely with a faculty advisor on this project.",
        "Our faculty advisor meets with the team weekly.",
    )
    assert result.passed is True


def test_positive_incorporation_claim_with_positive_evidence_ships():
    result = gate(
        "The company is incorporated in Delaware.",
        "The company was incorporated in Delaware last year.",
    )
    assert result.passed is True


def test_positive_funding_claim_with_positive_evidence_ships():
    result = gate(
        "We raised a small pre-seed round.",
        "The team raised a small pre-seed round from an angel investor.",
    )
    assert result.passed is True


# ── positive claim / explicit negative evidence ─────────────────────────────


def test_advisor_claim_against_no_advisor_evidence_is_blocked():
    result = gate(
        "We work closely with a faculty advisor.",
        "There is no faculty advisor.",
    )
    assert_blocked(result, "faculty_sponsor")


def test_incorporation_claim_against_no_entity_evidence_is_blocked():
    result = gate(
        "We are incorporated as a Delaware C-Corporation.",
        "No legal entity has been formed.",
    )
    assert_blocked(result, "incorporation")


def test_award_claim_against_never_awarded_evidence_is_blocked():
    result = gate(
        "We were awarded a campus prize.",
        "The team has never been awarded any prize.",
    )
    assert_blocked(result, "prior_funding")


def test_partnership_claim_against_negated_evidence_is_blocked():
    result = gate(
        "We have partnered with a local hospital.",
        "We have not partnered with anyone so far.",
    )
    assert_blocked(result, "prior_funding")


def test_credential_claim_against_neither_evidence_is_blocked():
    result = gate(
        "Our founding team includes a PhD.",
        "Neither founder holds a PhD.",
    )
    assert_blocked(result, "credentials")


def test_patent_claim_against_negated_evidence_is_blocked():
    result = gate(
        "We have filed a provisional patent.",
        "We have not filed any patent.",
    )
    assert_blocked(result, "ip_status")


def test_ip_claim_against_without_evidence_is_blocked():
    result = gate(
        "Our IP portfolio includes a licensed trademark.",
        "The product was built without any licensed IP.",
    )
    assert_blocked(result, "ip_status")


def test_funding_claim_against_has_yet_to_evidence_is_blocked():
    result = gate(
        "We were awarded a research grant.",
        "The team has yet to receive any grant or award.",
    )
    assert_blocked(result, "prior_funding")


# ── contractions ────────────────────────────────────────────────────────────


def test_isnt_negation_is_recognised():
    result = gate(
        "The company is incorporated as an LLC.",
        "The company isn't incorporated.",
    )
    assert_blocked(result, "incorporation")


def test_havent_negation_is_recognised():
    result = gate(
        "We have filed a provisional patent.",
        "We haven't filed a patent.",
    )
    assert_blocked(result, "ip_status")


def test_doesnt_negation_is_recognised():
    result = gate(
        "Our faculty advisor reviews our work.",
        "The team doesn't have a faculty advisor.",
    )
    assert_blocked(result, "faculty_sponsor")


# ── negative claim / negative evidence ──────────────────────────────────────


def test_stating_the_absence_the_kb_states_is_supported():
    result = gate(
        "We do not yet have a faculty advisor.",
        "There is no faculty advisor.",
    )
    assert result.passed is True


def test_stating_no_entity_when_kb_says_none_formed_is_supported():
    result = gate(
        "We haven't incorporated yet.",
        "No legal entity has been formed.",
    )
    assert result.passed is True


def test_stating_an_absence_the_kb_contradicts_is_blocked():
    """Claiming NOT to have something the deck says you have is still a
    misstatement, in the direction that costs the founder credit."""
    result = gate(
        "We do not have a faculty advisor.",
        "Our faculty advisor meets with the team weekly.",
    )
    assert_blocked(result, "faculty_sponsor")


# ── mixed evidence: both a positive and a negative statement ────────────────


def test_positive_claim_supported_by_the_positive_clause_of_mixed_evidence():
    result = gate(
        "40 students used our pilot.",
        "There is no revenue yet, but 40 students joined the pilot.",
        traction={"users": 40},
    )
    assert result.passed is True


def test_mixed_polarity_claim_needs_both_polarities_in_evidence():
    result = gate(
        "We have no revenue, but 40 users.",
        "40 students signed up during the pilot.",
        "There is no revenue.",
        traction={"users": 40},
    )
    assert result.passed is True


def test_mixed_polarity_claim_with_only_positive_evidence_is_blocked():
    """The negative half of the claim ('no revenue') has no support."""
    result = gate(
        "We have no revenue, but 40 users.",
        "40 students signed up during the pilot.",
        traction={"users": 40},
    )
    assert_blocked(result, "traction")


# ── negation separated by punctuation ───────────────────────────────────────


def test_negation_after_a_colon_still_negates_the_clause():
    result = gate(
        "Our faculty advisor supports the application.",
        "Faculty advisor: none.",
    )
    assert_blocked(result, "faculty_sponsor")


def test_negation_in_a_different_sentence_does_not_negate_the_evidence():
    """A 'no' in a neighbouring sentence must not flip a positive statement."""
    result = gate(
        "We work with a faculty advisor.",
        "There is no revenue. Our faculty advisor meets the team weekly.",
    )
    assert result.passed is True


def test_negation_before_a_comma_does_not_leak_across_the_clause():
    result = gate(
        "40 students used our pilot.",
        "No funding has been raised, but 40 students joined the pilot.",
        traction={"users": 40},
    )
    assert result.passed is True


# ── unrelated keyword overlap ───────────────────────────────────────────────


def test_evidence_for_a_different_category_does_not_support_this_one():
    result = gate(
        "We work closely with a faculty advisor.",
        "The team was awarded a campus prize last spring.",
    )
    assert_blocked(result, "faculty_sponsor")


def test_negated_overlap_about_something_else_is_not_support():
    result = gate(
        "We are incorporated as an LLC.",
        "No partnership has been formed with any hospital.",
    )
    assert_blocked(result, "incorporation")


def test_no_evidence_at_all_still_blocks():
    result = gate(
        "We work closely with a faculty advisor.",
        "The product schedules shared lab equipment.",
    )
    assert_blocked(result, "faculty_sponsor")


# ── the false-positive guard the DECISIONS.md entry warned about ────────────


def test_no_revenue_yet_but_40_users_does_not_block_the_supported_claim():
    """DECISIONS.md names this sentence as the one a naive negation window
    would break. The negation on 'revenue' must not swallow the 'users'
    clause of the same sentence."""
    result = gate(
        "We have 40 users.",
        "No revenue yet, but 40 users.",
        traction={"users": 40},
    )
    assert result.passed is True


def test_a_negated_aside_does_not_poison_the_main_clause():
    """'who is not paid' negates only its own comma-delimited clause. The
    main clause still states the advisor positively, so the claim ships."""
    result = gate(
        "Our faculty advisor is reviewing the draft.",
        "Our faculty advisor, who is not paid, reviews our work.",
    )
    assert result.passed is True
