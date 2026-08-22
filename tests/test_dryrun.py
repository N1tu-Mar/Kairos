"""The dry-run path, so the README's clean-clone claim cannot rot."""

from __future__ import annotations

import pytest

from agent.budget import DailyLedger, RunBudget
from agent.dryrun import LABEL, build_stub_agents
from agent.scout import new_run_context, run_once
from agent.tools.discovery import SeedCatalog
from api.repository import SqliteRepository
from tests.factories import TODAY, profile

pytestmark = pytest.mark.asyncio

DEMO_CATALOG = "data/opportunities.demo.json"


async def dry_run(tmp_path):
    ctx = new_run_context(
        profile=profile(),
        repo=SqliteRepository("sqlite:///:memory:"),
        budget=RunBudget(
            max_run_tokens=1_000_000,
            max_assessments=25,
            daily_usd_cap=0.0,
            ledger=DailyLedger(tmp_path),
        ),
        today=TODAY,
    )
    ctx.agents = build_stub_agents(ctx)
    report = await run_once(ctx, [SeedCatalog(DEMO_CATALOG, allow_unverified=True)])
    return ctx, report


async def test_the_pipeline_runs_with_no_aws_account(tmp_path):
    _, report = await dry_run(tmp_path)

    assert report.scanned == 5
    assert report.halted_reason is None
    assert report.usage.total_tokens == 0


async def test_the_deterministic_filter_still_does_the_real_work(tmp_path):
    _, report = await dry_run(tmp_path)

    checks = {r.check for r in report.rejections}
    assert checks == {"DEGREE_LEVEL", "EQUITY"}


async def test_the_escalation_policy_still_does_the_real_work(tmp_path):
    _, report = await dry_run(tmp_path)

    reasons = " ".join(s.reason for s in report.skips if s.stage == "escalation_policy")
    assert "ceiling is 8h" in reasons
    assert "below the founder's floor" in reasons


async def test_every_stubbed_verdict_is_labelled_as_such(tmp_path):
    """Nothing this produces may be mistakable for the agent's judgment."""
    ctx, _ = await dry_run(tmp_path)

    assert ctx.assessments
    for assessment in ctx.assessments.values():
        assert assessment.reason.startswith(LABEL)


async def test_the_stub_reads_live_run_state_not_a_stale_snapshot(tmp_path):
    """The stubs are built before discovery replaces ctx.retrieved."""
    ctx, report = await dry_run(tmp_path)

    assert report.judged == 3
    assert all(a.verdict != "INSUFFICIENT_INFO" for a in ctx.assessments.values()), (
        "an INSUFFICIENT_INFO everywhere means the stub could not see ctx.retrieved"
    )


async def test_the_dry_run_is_reproducible(tmp_path):
    _, first = await dry_run(tmp_path)
    _, second = await dry_run(tmp_path)

    assert first.headline() == second.headline()
