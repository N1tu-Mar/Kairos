"""The golden set as a regression guard.

`scripts/run_eval.py` prints the number for a human. This pins it, so that a
change to the deterministic layer cannot quietly make the system less grounded
between now and the submission.

The counts below are asserted exactly rather than as inequalities. A fix that
improves them **should** fail this test — improving the number is a deliberate
act that comes with updating the line that records it, and an eval whose
expectations drift upward on their own is an eval nobody is reading.
"""

from __future__ import annotations

import pytest

from tests.golden_set.loader import load_cases
from tests.golden_set.runner import run_case
from tests.golden_set.scorer import score

pytestmark = pytest.mark.asyncio


async def scorecard():
    cases = load_cases().cases
    return score([await run_case(c) for c in cases])


async def test_the_case_set_is_the_shape_section_11_11_asks_for():
    case_set = load_cases()
    assert len(case_set.cases) == 15
    # "half containing deliberate traps" — counted, not asserted in prose.
    assert len(case_set.traps) == 8
    assert len(case_set.clean) == 7


async def test_the_published_numbers_are_what_the_readme_says():
    card = await scorecard()

    assert card.groundedness == pytest.approx(0.80)
    assert card.abstention_accuracy == pytest.approx(9 / 11)
    assert card.unnecessary_question_rate == pytest.approx(1 / 9)


async def test_the_two_known_leaks_are_exactly_these_two():
    """Both are the same bug: an evidence regex matching a negation.

    Pinned by case id so a *new* leak fails the build even though the count
    would look unchanged if one were swapped for another.
    """
    card = await scorecard()

    assert {o.case_id for o in card.leaks} == {
        "trap_04_claim_negated_by_its_own_evidence",
        "trap_05_invented_incorporation",
    }


async def test_every_other_trap_is_caught():
    card = await scorecard()
    leaked = {o.case_id for o in card.leaks}
    trapped = {c.case_id for c in load_cases().traps}

    assert trapped - leaked == {
        "trap_01_inflated_number",
        "trap_02_fabricated_citation",
        "trap_03_no_citation_at_all",
        "trap_06_invented_prior_award",
        "trap_07_names_a_program_never_retrieved",
        "trap_08_answers_a_certification_field",
    }


async def test_a_clean_draft_still_ships():
    """The check that stops the safety layer from scoring well by blocking all."""
    card = await scorecard()
    shipped = {(o.case_id, o.field_id) for o in card.outcomes if o.shipped}

    assert ("clean_06_three_supported_fields", "problem") in shipped
    assert ("clean_06_three_supported_fields", "traction") in shipped
    assert ("clean_06_three_supported_fields", "team") in shipped


async def test_every_unnecessary_question_is_collateral_not_a_false_positive():
    """No supported claim is blocked on its own merits — only by a neighbour."""
    card = await scorecard()

    assert all(o.collateral for o in card.over_withheld)
