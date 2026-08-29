"""Caps are code, not discipline. Tested like code."""

from __future__ import annotations

from datetime import date

import pytest

from agent.budget import BudgetExceeded, DailyLedger, RunBudget, TierPrice


def budget(tmp_path, **overrides) -> RunBudget:
    """A `RunBudget` with a ledger under `tmp_path`. Override any cap by keyword."""
    base = dict(
        max_run_tokens=1_000,
        max_assessments=3,
        daily_usd_cap=1.0,
        ledger=DailyLedger(tmp_path),
        prices={"reasoning": TierPrice(3.0, 15.0)},
    )
    base.update(overrides)
    return RunBudget(**base)


def test_token_ceiling_halts_the_run(tmp_path):
    b = budget(tmp_path)
    b.charge(tier="reasoning", input_tokens=400, output_tokens=100)

    with pytest.raises(BudgetExceeded) as exc:
        b.charge(tier="reasoning", input_tokens=400, output_tokens=200)

    assert exc.value.cap == "RUN_TOKEN_CEILING"


def test_usage_records_the_call_that_crossed_the_line(tmp_path):
    b = budget(tmp_path)
    with pytest.raises(BudgetExceeded):
        b.charge(tier="reasoning", input_tokens=2_000, output_tokens=0)

    assert b.usage.total_tokens == 2_000, "the report must show what was actually spent"


def test_assessment_cap_halts_the_run(tmp_path):
    b = budget(tmp_path)
    for _ in range(3):
        b.take_assessment_slot()

    with pytest.raises(BudgetExceeded) as exc:
        b.take_assessment_slot()

    assert exc.value.cap == "ASSESSMENT_CAP"


def test_daily_cap_persists_across_runs(tmp_path):
    """Yesterday's run cannot be undone by restarting the process."""
    first = budget(tmp_path, max_run_tokens=10_000_000, daily_usd_cap=10.0)
    first.charge(tier="reasoning", input_tokens=1_000_000, output_tokens=0)  # $3.00
    assert first.ledger.spent_today() == pytest.approx(3.0)

    # A fresh process, a fresh RunBudget, a cap already blown by run one.
    second = budget(tmp_path, max_run_tokens=10_000_000, daily_usd_cap=1.0)
    with pytest.raises(BudgetExceeded) as exc:
        second.charge(tier="reasoning", input_tokens=1, output_tokens=0)

    assert exc.value.cap == "DAILY_USD_CAP"


def test_unknown_tier_costs_zero_rather_than_guessing(tmp_path):
    b = budget(tmp_path)
    b.charge(tier="unpriced", input_tokens=100, output_tokens=100)
    assert b.usage.usd_estimate == 0.0


def test_corrupt_ledger_refuses_to_spend(tmp_path):
    (tmp_path / "daily_spend.json").write_text("{not json")
    b = budget(tmp_path)

    with pytest.raises(BudgetExceeded) as exc:
        b.charge(tier="reasoning", input_tokens=1, output_tokens=1)

    assert exc.value.cap == "DAILY_USD_CAP"
    assert "unreadable" in exc.value.detail


def test_ledger_is_keyed_by_day(tmp_path):
    ledger = DailyLedger(tmp_path)
    ledger.add(0.5, today=date(2026, 8, 22))
    ledger.add(0.25, today=date(2026, 8, 23))

    assert ledger.spent_today(date(2026, 8, 22)) == 0.5
    assert ledger.spent_today(date(2026, 8, 23)) == 0.25


def test_strands_limits_shrink_as_the_run_progresses(tmp_path):
    b = budget(tmp_path, max_run_tokens=1_000_000, daily_usd_cap=100.0)
    before = b.strands_limits()["total_tokens"]
    b.charge(tier="reasoning", input_tokens=500_000, output_tokens=0)

    assert b.strands_limits()["total_tokens"] < before
