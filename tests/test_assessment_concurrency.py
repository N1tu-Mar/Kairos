"""Bounded concurrent assessments: same answers, bounded spend, same halts.

Every test drives the real `run_once` with an Assessor fake that sleeps, so
completion order genuinely differs from rank order.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest

from agent.budget import BudgetExceeded, DailyLedger, RunBudget, TierPrice
from agent.config import settings
from agent.dryrun import StubDrafter, StubAuditor
from agent.models import Assessment
from agent.prompting import Abstention, Throttled
from agent.runtime import SubAgents
from agent.scout import new_run_context, run_once
from api.repository import SqliteRepository
from tests.conftest import FakeAgentResult, FakeMetrics
from tests.factories import TODAY, opportunity, profile

N = 8


def catalog(n: int = N):
    """Eligible opportunities whose rank is set by deadline: opp_0 ranks first."""
    return [
        opportunity(
            id=f"opp_{i}",
            title=f"[DEMO] Fund {i}",
            deadline=TODAY + timedelta(days=10 + i),
        )
        for i in range(n)
    ]


class ListSource:
    name = "seed"

    def __init__(self, rows):
        self.rows = rows

    def fetch(self, since):
        return list(self.rows)


class TimedAssessor:
    """Answers by title. Higher-ranked rows are slower, so they finish last.

    Usage honours the per-call limit the way Strands' limits do, which is the
    assumption the reservation arithmetic rests on.
    """

    def __init__(self, *, want_tokens=1_000, fail=None, delays=None):
        self.want_tokens = want_tokens
        self.fail = fail or {}
        self.delays = delays
        self.in_flight = 0
        self.max_in_flight = 0
        self.started: list[str] = []
        self.finished: list[str] = []

    async def invoke_async(self, prompt, *, structured_output_model=None, limits=None):
        index = next(i for i in range(100) if f"[DEMO] Fund {i}\n" in prompt)
        oid = f"opp_{index}"
        self.started.append(oid)
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            delay = self.delays[index] if self.delays else 0.002 * (N - index)
            await asyncio.sleep(delay)
            if oid in self.fail:
                raise self.fail[oid]
            tokens = min(self.want_tokens, (limits or {}).get("total_tokens", self.want_tokens))
            self.finished.append(oid)
            return FakeAgentResult(
                structured_output=Assessment(
                    verdict="APPLY", reason=f"fits {oid}", effort_hours=4.0
                ),
                metrics=FakeMetrics(
                    accumulated_usage={
                        "inputTokens": tokens // 2,
                        "outputTokens": tokens - tokens // 2,
                        "totalTokens": tokens,
                    }
                ),
            )
        finally:
            self.in_flight -= 1


def make_ctx(tmp_path, assessor, *, concurrency, max_tokens=250_000, max_assessments=25, **budget_kw):
    ctx = new_run_context(
        profile=profile(),
        repo=SqliteRepository("sqlite:///:memory:"),
        budget=RunBudget(
            max_run_tokens=max_tokens,
            max_assessments=max_assessments,
            daily_usd_cap=budget_kw.pop("daily_usd_cap", 0.0),
            ledger=DailyLedger(tmp_path / f"ledger{concurrency}"),
            assessment_concurrency=concurrency,
            **budget_kw,
        ),
        today=TODAY,
    )
    ctx.agents = SubAgents(
        assessor=assessor,
        assessor_version="v",
        drafter=StubDrafter(),
        drafter_version="v",
        auditor=StubAuditor(),
        auditor_version="v",
    )
    return ctx


def fingerprint(ctx, report):
    return {
        "assessments": [(k, v.verdict, v.reason) for k, v in ctx.assessments.items()],
        "notes": report.notes,
        "skips": [(s.opportunity_id, s.stage, s.reason) for s in report.skips],
        "counters": (report.scanned, report.filtered_out, report.judged, report.surfaced),
        "halted": report.halted_reason,
        "tokens": report.usage.total_tokens,
        "inbox": [(i.opportunity_id, i.passive) for i in ctx.pending_inbox],
    }


# ── Determinism and bounds ───────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("max_assessments", [25, 5])
async def test_output_is_identical_for_every_concurrency_level(tmp_path, max_assessments):
    prints = {}
    for concurrency in (1, 2, 4):
        assessor = TimedAssessor()
        ctx = make_ctx(tmp_path, assessor, concurrency=concurrency, max_assessments=max_assessments)
        report = await run_once(ctx, [ListSource(catalog())])
        prints[concurrency] = fingerprint(ctx, report)
        if concurrency > 1:
            # Completion order really was different from rank order.
            assert assessor.finished != sorted(assessor.finished)

    assert prints[1]["halted"] is None
    assert prints[1] == prints[2] == prints[4]
    assert [a[0] for a in prints[1]["assessments"]] == [f"opp_{i}" for i in range(min(N, max_assessments))]


@pytest.mark.asyncio
@pytest.mark.parametrize("concurrency", [2, 3, 4])
async def test_in_flight_calls_never_exceed_the_limit_and_start_in_rank_order(tmp_path, concurrency):
    assessor = TimedAssessor()
    ctx = make_ctx(tmp_path, assessor, concurrency=concurrency)
    await run_once(ctx, [ListSource(catalog())])

    assert 1 < assessor.max_in_flight <= concurrency
    assert assessor.started == [f"opp_{i}" for i in range(N)]
    assert ctx.budget.reserved_tokens == 0 and ctx.budget.reserved_usd == 0


@pytest.mark.asyncio
async def test_sequential_path_takes_no_reservations(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("sequential runs must not reserve")

    monkeypatch.setattr(RunBudget, "reserve", boom)
    ctx = make_ctx(tmp_path, TimedAssessor(), concurrency=1)
    report = await run_once(ctx, [ListSource(catalog())])
    assert report.halted_reason is None and report.judged == N


def test_concurrency_setting_is_bounded(monkeypatch):
    assert settings().assessment_concurrency == 1
    for bad in ("0", "5", "-1"):
        monkeypatch.setenv("KAIROS_ASSESSMENT_CONCURRENCY", bad)
        settings.cache_clear()
        with pytest.raises(ValueError):
            settings()
    monkeypatch.setenv("KAIROS_ASSESSMENT_CONCURRENCY", "3")
    settings.cache_clear()
    assert settings().assessment_concurrency == 3
    assert RunBudget.from_settings(settings()).assessment_concurrency == 3


# ── Budget races ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("concurrency", [2, 4])
async def test_token_ceiling_is_never_overspent_under_concurrency(tmp_path, concurrency):
    """Calls that honour their limit can never push the run past its ceiling,
    because every limit is carved from what is left after other reservations."""
    ceiling = 3_000
    ctx = make_ctx(tmp_path, TimedAssessor(want_tokens=10_000), concurrency=concurrency, max_tokens=ceiling)
    report = await run_once(ctx, [ListSource(catalog())])

    assert report.usage.total_tokens <= ceiling
    assert ctx.budget.reserved_tokens == 0


def test_naive_limits_would_overspend_the_same_ceiling():
    """The failure the reservation exists to prevent, stated as arithmetic:
    eight calls each told 25% of the same remaining budget."""
    budget = RunBudget(max_run_tokens=3_000, max_assessments=25, daily_usd_cap=0.0, ledger=None)
    naive = sum(budget.strands_limits()["total_tokens"] for _ in range(N))
    assert naive > budget.max_run_tokens

    reserved = 0
    while budget.reserve(tier="reasoning") is not None and reserved < N:
        reserved += 1
    assert budget.reserved_tokens <= budget.max_run_tokens


@pytest.mark.asyncio
async def test_daily_cap_is_reserved_before_calls_start(tmp_path):
    """Priced calls under a nearly spent daily cap: admission stops before the
    cap can be crossed, the run halts on DAILY_USD_CAP and surfaces nothing."""
    ledger_dir = tmp_path / "ledger4"
    DailyLedger(ledger_dir).add(0.95)
    ctx = make_ctx(
        tmp_path,
        TimedAssessor(want_tokens=2_000),
        concurrency=4,
        daily_usd_cap=1.0,
        prices={"reasoning": TierPrice(10.0, 10.0), "classify": TierPrice(10.0, 10.0)},
    )
    report = await run_once(ctx, [ListSource(catalog())])

    assert ctx.budget.ledger.spent_today() <= 1.0
    assert report.halted_reason and report.halted_reason.startswith("DAILY_USD_CAP")
    assert ctx.pending_inbox == []
    assert ctx.budget.reserved_usd == pytest.approx(0.0)


# ── Failures halt and surface nothing ────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error, prefix",
    [
        (Throttled("assessor", "busy"), "THROTTLED"),
        (BudgetExceeded("RUN_TOKEN_CEILING", "over"), "RUN_TOKEN_CEILING"),
    ],
)
async def test_a_failure_halts_and_matches_the_sequential_outcome(tmp_path, error, prefix):
    prints = {}
    for concurrency in (1, 4):
        assessor = TimedAssessor(fail={"opp_3": error})
        ctx = make_ctx(tmp_path, assessor, concurrency=concurrency)
        report = await run_once(ctx, [ListSource(catalog())])
        prints[concurrency] = fingerprint(ctx, report)
        assert report.halted_reason.startswith(prefix)
        assert ctx.pending_inbox == []
        assert ctx.budget.reserved_tokens == 0
        if concurrency > 1:
            # Nothing was admitted after the failure became known, and every
            # call already in flight finished and was charged.
            assert assessor.in_flight == 0
            assert set(assessor.started) <= {f"opp_{i}" for i in range(3 + 4)}

    # Only the rows ranked above the failure are recorded, as sequentially.
    assert [a[0] for a in prints[4]["assessments"]] == ["opp_0", "opp_1", "opp_2"]
    assert prints[1]["assessments"] == prints[4]["assessments"]
    assert prints[1]["halted"] == prints[4]["halted"]
    assert prints[1]["inbox"] == prints[4]["inbox"] == []


@pytest.mark.asyncio
async def test_a_crash_outside_the_model_call_halts_like_sequential(tmp_path):
    """An exception that escapes the Assessor wrapper (here: building the
    per-call agent for the fourth-ranked row) halts the run either way."""
    prints = {}
    for concurrency in (1, 4):
        assessor = TimedAssessor()
        ctx = make_ctx(tmp_path, assessor, concurrency=concurrency)
        calls = {"n": 0}

        def assessor_for_call(agent=assessor):
            calls["n"] += 1
            if calls["n"] == 4:
                raise RuntimeError("could not build assessor")
            return agent, "v"

        ctx.agents.assessor_for_call = assessor_for_call
        report = await run_once(ctx, [ListSource(catalog())])
        prints[concurrency] = fingerprint(ctx, report)
        assert report.halted_reason.startswith("RuntimeError")
        assert ctx.pending_inbox == []

    assert [a[0] for a in prints[4]["assessments"]] == ["opp_0", "opp_1", "opp_2"]
    assert prints[1]["assessments"] == prints[4]["assessments"]
    assert prints[1]["halted"] == prints[4]["halted"]


@pytest.mark.asyncio
async def test_a_model_error_is_retried_into_an_abstention_like_sequential(tmp_path):
    """A provider exception inside the model call is retried and then becomes
    INSUFFICIENT_INFO today; concurrency must not turn it into a halt."""
    prints = {}
    for concurrency in (1, 4):
        ctx = make_ctx(tmp_path, TimedAssessor(fail={"opp_3": RuntimeError("model exploded")}), concurrency=concurrency)
        report = await run_once(ctx, [ListSource(catalog())])
        prints[concurrency] = fingerprint(ctx, report)
    assert prints[1]["halted"] is None
    assert prints[1] == prints[4]


@pytest.mark.asyncio
async def test_the_highest_ranked_failure_is_reported_regardless_of_timing(tmp_path):
    delays = {i: 0.001 for i in range(N)}
    delays[1] = 0.05  # opp_1 fails last in time but first in rank
    assessor = TimedAssessor(
        fail={"opp_1": Throttled("assessor", "first"), "opp_2": Throttled("assessor", "second")},
        delays=delays,
    )
    ctx = make_ctx(tmp_path, assessor, concurrency=4)
    report = await run_once(ctx, [ListSource(catalog())])

    assert "first" in report.halted_reason
    assert list(ctx.assessments) == ["opp_0"]


@pytest.mark.asyncio
async def test_an_abstention_is_an_outcome_not_a_halt(tmp_path):
    prints = {}
    for concurrency in (1, 4):
        ctx = make_ctx(tmp_path, TimedAssessor(fail={"opp_2": Abstention("assessor", "no idea")}), concurrency=concurrency)
        report = await run_once(ctx, [ListSource(catalog())])
        prints[concurrency] = fingerprint(ctx, report)
    assert prints[1]["halted"] is None
    assert dict((k, v) for k, v, _ in prints[4]["assessments"])["opp_2"] == "INSUFFICIENT_INFO"
    assert prints[1] == prints[4]


@pytest.mark.asyncio
async def test_external_cancellation_cancels_in_flight_calls_and_releases_budget(tmp_path):
    assessor = TimedAssessor(delays={i: 5.0 for i in range(N)})
    ctx = make_ctx(tmp_path, assessor, concurrency=4)
    run = asyncio.create_task(run_once(ctx, [ListSource(catalog())]))
    while assessor.in_flight < 4:
        await asyncio.sleep(0.001)

    run.cancel()
    with pytest.raises(asyncio.CancelledError):
        await run

    assert assessor.in_flight == 0
    assert assessor.finished == []
    assert ctx.budget.reserved_tokens == 0 and ctx.budget.reserved_usd == 0
    assert ctx.pending_inbox == []
