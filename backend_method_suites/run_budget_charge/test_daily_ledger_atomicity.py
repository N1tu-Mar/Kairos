"""`DailyLedger` — the atomic daily counter, exercised the way production hits it.

The JSON read-modify-write ledger was safe for exactly one process. These
tests pin the SQLite replacement: concurrent increments never lose money,
corruption refuses rather than resets, the legacy JSON imports once, and the
call that crosses the cap is recorded and then halted — never silently split
into "both callers thought they were under".
"""

from __future__ import annotations

import subprocess
import sys
import threading
from datetime import date
from pathlib import Path

import pytest

from agent.budget import BudgetExceeded, DailyLedger, RunBudget, TierPrice

REPO_ROOT = Path(__file__).resolve().parents[2]


def _budget(tmp_path, *, cap: float, price_out: float = 1_000_000.0) -> RunBudget:
    """A budget where one output token costs `price_out / 1M` dollars."""
    return RunBudget(
        max_run_tokens=10_000_000,
        max_assessments=100,
        daily_usd_cap=cap,
        ledger=DailyLedger(tmp_path),
        prices={"reasoning": TierPrice(0.0, price_out)},
    )


# ── Concurrency ──────────────────────────────────────────────────────────────


def test_concurrent_adds_lose_nothing(tmp_path):
    ledger = DailyLedger(tmp_path)
    day = date(2026, 8, 26)
    barrier = threading.Barrier(16)

    def spend():
        barrier.wait()
        DailyLedger(tmp_path).add(0.25, today=day)

    threads = [threading.Thread(target=spend) for _ in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert ledger.spent_today(day) == pytest.approx(4.0)


def test_adds_from_a_second_process_are_counted(tmp_path):
    ledger = DailyLedger(tmp_path)
    day = date(2026, 8, 26)
    ledger.add(1.0, today=day)

    script = (
        "from agent.budget import DailyLedger; from datetime import date; "
        f"DailyLedger({str(tmp_path)!r}).add(2.0, today=date(2026, 8, 26))"
    )
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=REPO_ROOT, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
    assert ledger.spent_today(day) == pytest.approx(3.0)


def test_two_budgets_sharing_a_ledger_cannot_both_pass_the_cap(tmp_path):
    """The race the JSON ledger allowed: both read $0, both spend $6.

    With the atomic counter the second charge sees a total that includes the
    first, crosses the $10 cap, is recorded, and halts.
    """
    first = _budget(tmp_path, cap=10.0)
    second = _budget(tmp_path, cap=10.0)

    first.charge(tier="reasoning", input_tokens=0, output_tokens=6)  # $6

    with pytest.raises(BudgetExceeded) as excinfo:
        second.charge(tier="reasoning", input_tokens=0, output_tokens=6)  # $12 total
    assert excinfo.value.cap == "DAILY_USD_CAP"

    # The crossing call is recorded — the report shows what was truly spent.
    assert second.usage.output_tokens == 6
    assert first.ledger.spent_today() == pytest.approx(12.0)


# ── Corruption ───────────────────────────────────────────────────────────────


def test_corrupt_database_refuses_to_spend(tmp_path):
    (tmp_path / "daily_spend.sqlite3").write_text("this is not a database")
    ledger = DailyLedger(tmp_path)

    with pytest.raises(BudgetExceeded) as excinfo:
        ledger.add(0.5)
    assert excinfo.value.cap == "DAILY_USD_CAP"

    with pytest.raises(BudgetExceeded):
        ledger.spent_today()


def test_corrupt_legacy_json_refuses_rather_than_resets(tmp_path):
    (tmp_path / "daily_spend.json").write_text("{not json")

    with pytest.raises(BudgetExceeded):
        DailyLedger(tmp_path).spent_today()


# ── Legacy migration ─────────────────────────────────────────────────────────


def test_legacy_json_totals_carry_forward(tmp_path):
    (tmp_path / "daily_spend.json").write_text('{"2026-08-25": 1.5, "2026-08-26": 2.0}')
    ledger = DailyLedger(tmp_path)

    assert ledger.spent_today(date(2026, 8, 25)) == pytest.approx(1.5)
    assert ledger.add(0.5, today=date(2026, 8, 26)) == pytest.approx(2.5)


def test_legacy_import_happens_once_not_per_read(tmp_path):
    """Re-reading must not re-add the JSON totals on top of new spend."""
    (tmp_path / "daily_spend.json").write_text('{"2026-08-26": 2.0}')
    day = date(2026, 8, 26)

    DailyLedger(tmp_path).add(1.0, today=day)
    # A fresh instance over the same directory sees 3.0, not 5.0.
    assert DailyLedger(tmp_path).spent_today(day) == pytest.approx(3.0)


def test_legacy_json_is_preserved_as_backup(tmp_path):
    legacy = tmp_path / "daily_spend.json"
    legacy.write_text('{"2026-08-26": 2.0}')
    DailyLedger(tmp_path).add(1.0, today=date(2026, 8, 26))

    assert legacy.read_text() == '{"2026-08-26": 2.0}'


# ── Rollover and zero-price ──────────────────────────────────────────────────


def test_days_do_not_bleed_into_each_other(tmp_path):
    ledger = DailyLedger(tmp_path)
    ledger.add(2.99, today=date(2026, 8, 25))

    assert ledger.spent_today(date(2026, 8, 26)) == 0.0
    # Yesterday's spend cannot trip today's cap.
    budget = _budget(tmp_path, cap=3.0, price_out=0.0)
    budget.daily_usd_cap = 3.0
    budget.charge(tier="reasoning", input_tokens=100, output_tokens=100)


def test_zero_price_records_tokens_but_no_dollars(tmp_path):
    budget = _budget(tmp_path, cap=10.0, price_out=0.0)
    budget.charge(tier="reasoning", input_tokens=1000, output_tokens=1000)

    assert budget.usage.total_tokens == 2000
    assert budget.usage.usd_estimate == 0.0
    assert budget.ledger.spent_today() == 0.0
