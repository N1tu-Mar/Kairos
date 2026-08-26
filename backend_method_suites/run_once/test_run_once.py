from __future__ import annotations

from dataclasses import dataclass

import pytest

from agent.budget import DailyLedger, RunBudget
from agent.models import EligibilityRules
from agent.runtime import SubAgents
from agent.scout import new_run_context, run_once
from tests.factories import TODAY, opportunity, profile
from backend_method_suites.conftest import FakeAgent, assessment, fake_agents

pytestmark = pytest.mark.asyncio


OPEN_RULES = EligibilityRules(
    degree_levels=["undergrad"],
    citizenships=["us_citizen"],
    entity_types=["none"],
)


@dataclass
class ListSource:
    opportunities: list
    name: str = "seed"

    def fetch(self, since):
        return self.opportunities


async def test_run_once_ranks_surfaces_passive_overflow_and_persists_seen_rows(
    memory_repo, run_budget
):
    opps = [
        opportunity(id="small_fast", eligibility=OPEN_RULES, award_max=6_000),
        opportunity(id="best", eligibility=OPEN_RULES, award_max=40_000),
        opportunity(id="medium", eligibility=OPEN_RULES, award_max=20_000),
        opportunity(id="overflow", eligibility=OPEN_RULES, award_max=12_000),
        opportunity(id="phd_wall", eligibility=EligibilityRules(degree_levels=["phd"])),
    ]
    ctx = new_run_context(
        profile=profile(),
        repo=memory_repo,
        budget=run_budget,
        agents=fake_agents(
            assessment("APPLY", hours=1),
            assessment("APPLY", hours=4),
            assessment("APPLY", hours=2),
            assessment("APPLY", hours=4),
        ),
        today=TODAY,
    )

    report = await run_once(ctx, [ListSource(opps)])

    assert report.scanned == 5
    assert report.filtered_out == 1
    assert report.judged == 4
    assert report.surfaced == 3
    assert [item.opportunity_id for item in memory_repo.list_inbox("founder_demo")][-1]
    assert sum(1 for item in memory_repo.list_inbox("founder_demo") if item.passive) == 1
    assert memory_repo.get_opportunity("phd_wall") is not None


async def test_run_once_halts_on_budget_and_surfaces_no_partial_digest(tmp_path, memory_repo):
    # Usage is reported by the agent; charging is the orchestrator's job.
    expensive = FakeAgent(
        *[assessment("APPLY", hours=1) for _ in range(4)],
        usage={"inputTokens": 10_000, "outputTokens": 0, "totalTokens": 10_000},
    )

    budget = RunBudget(
        max_run_tokens=5_000,
        max_assessments=25,
        daily_usd_cap=0.0,
        ledger=DailyLedger(tmp_path / "ledger"),
    )
    ctx = new_run_context(
        profile=profile(),
        repo=memory_repo,
        budget=budget,
        agents=SubAgents(
            assessor=expensive,
            assessor_version="v1",
            drafter=FakeAgent(),
            drafter_version="v1",
            auditor=FakeAgent(),
            auditor_version="v1",
        ),
        today=TODAY,
    )

    report = await run_once(
        ctx,
        [ListSource([opportunity(id="fit", eligibility=OPEN_RULES, award_max=20_000)])],
    )

    assert "RUN_TOKEN_CEILING" in report.halted_reason
    assert report.surfaced == 0
    assert memory_repo.list_inbox("founder_demo") == []
    assert memory_repo.latest_run("founder_demo").run_id == report.run_id
    assert memory_repo.get_opportunity("fit") is not None
