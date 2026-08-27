"""What the budget actually enforces, and whether it says so.

Bedrock prices default to zero because this repository does not invent them
(DECISIONS.md D12). The consequence is arithmetic: at $0.00 per call the
daily USD ledger records nothing, so `KAIROS_DAILY_USD_CAP` can never trip
and only the per-run token ceiling is doing work.

That is a defensible posture for a dry run against fixtures. It is not a
defensible posture for a scheduled production run whose operator has set a
dollar cap and believes it is protecting them. The distinction these tests
pin is the difference between the two, and the requirement that the system
never *implies* dollar enforcement it cannot deliver.
"""

from __future__ import annotations

import pytest

from agent.budget import (
    BudgetExceeded,
    DailyLedger,
    RunBudget,
    UnenforceableSpendCap,
)


PRICED = {"reasoning_in": 3.0, "reasoning_out": 15.0, "classify_in": 0.8, "classify_out": 4.0}


def budget(tmp_path, *, cap: float = 3.0, prices: dict | None = None, **overrides):
    """A RunBudget built directly, so no `.env` is involved.

    The prices here are test fixtures, not a claim about Bedrock's real
    pricing. Nothing in this repository ships a price table.
    """
    from agent.budget import TierPrice

    prices = PRICED if prices is None else prices
    return RunBudget(
        max_run_tokens=overrides.pop("max_run_tokens", 100_000),
        max_assessments=overrides.pop("max_assessments", 25),
        daily_usd_cap=cap,
        ledger=DailyLedger(tmp_path / "ledger"),
        prices={
            "reasoning": TierPrice(prices["reasoning_in"], prices["reasoning_out"]),
            "classify": TierPrice(prices["classify_in"], prices["classify_out"]),
        },
        **overrides,
    )


ZERO = {"reasoning_in": 0.0, "reasoning_out": 0.0, "classify_in": 0.0, "classify_out": 0.0}
PARTIAL = {"reasoning_in": 3.0, "reasoning_out": 15.0, "classify_in": 0.0, "classify_out": 0.0}


# ── what is actually enforced ───────────────────────────────────────────────


def test_correct_prices_report_both_caps_active(tmp_path):
    status = budget(tmp_path).enforcement_status()

    assert status.token_ceiling_active is True
    assert status.usd_cap_active is True
    assert "token ceiling" in status.summary
    assert "$3.00" in status.summary


def test_zero_prices_report_the_usd_cap_as_inactive(tmp_path):
    status = budget(tmp_path, prices=ZERO).enforcement_status()

    assert status.token_ceiling_active is True
    assert status.usd_cap_active is False
    assert "token ceiling" in status.summary
    assert "cannot be enforced" in status.summary


def test_partially_configured_prices_are_treated_as_unconfigured(tmp_path):
    """One tier priced and the other at zero under-counts every call the
    unpriced tier makes. A cap computed from half the spend is not a cap."""
    status = budget(tmp_path, prices=PARTIAL).enforcement_status()

    assert status.usd_cap_active is False
    assert status.prices_configured is False


def test_no_configured_cap_means_there_is_nothing_to_enforce(tmp_path):
    """`KAIROS_DAILY_USD_CAP=0` is 'no dollar cap', which is a coherent
    choice. It is not the same as 'a cap that silently does nothing'."""
    status = budget(tmp_path, cap=0.0, prices=ZERO).enforcement_status()

    assert status.usd_cap_active is False
    assert status.usd_cap_configured is False
    assert "no daily USD cap" in status.summary


def test_the_token_ceiling_is_always_active(tmp_path):
    """It enforces on raw counts and never depends on a price."""
    for prices in (ZERO, PARTIAL, PRICED):
        assert budget(tmp_path, prices=prices).enforcement_status().token_ceiling_active


# ── the refusal ─────────────────────────────────────────────────────────────


def test_a_live_run_refuses_when_a_configured_cap_cannot_be_calculated(tmp_path):
    """The whole point. An operator who set a $3 cap and is running against
    a real model must not be told the run is protected when it is not."""
    with pytest.raises(UnenforceableSpendCap) as exc:
        budget(tmp_path, cap=3.0, prices=ZERO).require_enforceable_spend_cap()

    assert "KAIROS_PRICE" in str(exc.value)


def test_a_live_run_with_partial_prices_also_refuses(tmp_path):
    with pytest.raises(UnenforceableSpendCap):
        budget(tmp_path, cap=3.0, prices=PARTIAL).require_enforceable_spend_cap()


def test_a_live_run_with_real_prices_proceeds(tmp_path):
    budget(tmp_path, cap=3.0, prices=PRICED).require_enforceable_spend_cap()


def test_a_live_run_with_no_cap_configured_proceeds(tmp_path):
    """Nothing to enforce, nothing to lie about. The token ceiling still
    applies, and the status says only the token ceiling is active."""
    b = budget(tmp_path, cap=0.0, prices=ZERO)

    b.require_enforceable_spend_cap()

    assert b.enforcement_status().token_ceiling_active is True


def test_the_refusal_names_what_to_do(tmp_path):
    with pytest.raises(UnenforceableSpendCap) as exc:
        budget(tmp_path, cap=3.0, prices=ZERO).require_enforceable_spend_cap()

    message = str(exc.value)
    assert "$3.00" in message
    # It must not invent a price or suggest one.
    assert "per 1M" in message or "per-token" in message.lower()


# ── dry-run and fixture paths are exempt ────────────────────────────────────


def test_a_dry_run_may_operate_with_zero_prices(tmp_path):
    """No model is called, so nothing is spent and there is nothing to cap.
    Refusing here would break `--dry-run` for exactly the person it exists
    to serve: someone with a clean clone and no AWS account."""
    budget(tmp_path, cap=3.0, prices=ZERO).require_enforceable_spend_cap(
        calls_models=False
    )


def test_a_fixture_path_may_operate_with_zero_prices(tmp_path):
    b = budget(tmp_path, cap=3.0, prices=ZERO)

    b.require_enforceable_spend_cap(calls_models=False)

    status = b.enforcement_status()
    assert status.usd_cap_active is False


# ── enforcement still works when prices are real ────────────────────────────


def test_a_real_price_makes_the_daily_cap_trip(tmp_path):
    """The cap being *enforceable* is only interesting if it enforces."""
    # The token ceiling is raised out of the way so this isolates the
    # dollar cap; whichever cap trips first is the one that raises.
    b = budget(tmp_path, cap=0.01, prices=PRICED, max_run_tokens=10_000_000)

    with pytest.raises(BudgetExceeded) as exc:
        b.charge(tier="reasoning", input_tokens=1_000_000, output_tokens=1_000_000)

    assert exc.value.cap == "DAILY_USD_CAP"


def test_zero_prices_record_tokens_but_never_dollars(tmp_path):
    b = budget(tmp_path, cap=0.01, prices=ZERO, max_run_tokens=10_000_000)

    b.charge(tier="reasoning", input_tokens=1_000_000, output_tokens=1_000_000)

    assert b.usage.total_tokens == 2_000_000
    assert b.usage.usd_estimate == 0.0


def test_the_token_ceiling_still_halts_at_zero_prices(tmp_path):
    """The guarantee that survives an unpriced deployment."""
    b = budget(tmp_path, cap=3.0, prices=ZERO, max_run_tokens=1_000)

    with pytest.raises(BudgetExceeded) as exc:
        b.charge(tier="reasoning", input_tokens=900, output_tokens=900)

    assert exc.value.cap == "RUN_TOKEN_CEILING"


# ── the live paths actually call the check ──────────────────────────────────


def test_a_scheduled_job_refuses_to_start_on_an_unenforceable_cap(
    monkeypatch, tmp_path
):
    """A check nothing calls is decoration. This asserts the job path calls
    it, fails closed, and records why — before any model is invoked."""
    import asyncio

    from agent import config
    from api import jobs as job_module
    from api.repository import SqliteRepository
    from agent.scheduler import RunLock, ScheduledRunFailureLog
    from tests.factories import profile as make_profile

    monkeypatch.setenv("KAIROS_DAILY_USD_CAP", "3.0")
    monkeypatch.setenv("KAIROS_PRICE_REASONING_OUT_PER_MTOK", "0")
    monkeypatch.setenv("KAIROS_PRICE_CLASSIFY_OUT_PER_MTOK", "0")
    monkeypatch.setenv("KAIROS_STATE_DIR", str(tmp_path / "state"))
    config.settings.cache_clear()

    repo = SqliteRepository("sqlite:///:memory:")
    repo.save_profile(make_profile(founder_id="founder_demo"))
    failure_log = ScheduledRunFailureLog(tmp_path / "failures.jsonl")
    lock = RunLock(tmp_path / "locks")
    lease = lock.acquire(founder_id="founder_demo", run_kind=job_module.RUN_KIND)

    job = job_module.new_job(
        founder_id="founder_demo",
        idempotency_key=None,
        source="scheduled",
        use_demo_catalog=True,
        include_grants_gov=False,
    )
    repo.save_job(job)

    asyncio.run(job_module.execute_job(job, repo, lease, failure_log))

    stored = repo.get_job(job.job_id)
    assert stored.status == "failed"
    assert "cap" in (stored.error or "").lower()
    config.settings.cache_clear()
