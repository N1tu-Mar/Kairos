from __future__ import annotations

import pytest

from agent.budget import BudgetExceeded, TierPrice


def test_charge_records_usage_and_cost_before_enforcing_limits(run_budget):
    run_budget.max_run_tokens = 100
    run_budget.prices = {"reasoning": TierPrice(input_per_mtok=10.0, output_per_mtok=20.0)}

    with pytest.raises(BudgetExceeded) as caught:
        run_budget.charge(tier="reasoning", input_tokens=90, output_tokens=20)

    assert caught.value.cap == "RUN_TOKEN_CEILING"
    assert run_budget.usage.total_tokens == 110
    assert run_budget.usage.usd_estimate > 0


def test_assessment_slots_are_a_hard_cap(run_budget):
    run_budget.max_assessments = 2

    run_budget.take_assessment_slot()
    run_budget.take_assessment_slot()

    with pytest.raises(BudgetExceeded) as caught:
        run_budget.take_assessment_slot()

    assert caught.value.cap == "ASSESSMENT_CAP"
