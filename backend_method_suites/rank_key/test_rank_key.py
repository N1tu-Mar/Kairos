"""The ranking key: how surfaced opportunities are ordered.

Pure and total, so the order is reproducible for the same inputs.
"""

from __future__ import annotations

from agent.guardrails import rank_key
from tests.factories import opportunity
from backend_method_suites.conftest import assessment


def test_value_per_hour_beats_raw_award_size():
    small_fast = rank_key(assessment("APPLY", hours=1), opportunity(award_max=10_000))
    big_slow = rank_key(assessment("APPLY", hours=20), opportunity(award_max=50_000))

    assert small_fast > big_slow


def test_maybe_is_discounted_below_apply_for_same_award_and_effort():
    apply_score = rank_key(assessment("APPLY", hours=4), opportunity(award_max=20_000))
    maybe_score = rank_key(assessment("MAYBE", hours=4), opportunity(award_max=20_000))

    assert maybe_score == apply_score / 2
