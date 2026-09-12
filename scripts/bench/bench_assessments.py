"""Sequential versus bounded-concurrent assessments, with delayed stubs.

    uv run python scripts/bench/bench_assessments.py [--delay 0.2] [--repeat 3]

Offline. The stub Assessor sleeps to stand in for model latency and reports
nonzero token usage so the budget reservation path does real work. Prints
wall time per concurrency level and whether every level produced the same
report.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
os.chdir(REPO)
os.environ.update(
    BEDROCK_MODEL_REASONING="[DRY-RUN]no-model",
    BEDROCK_MODEL_CLASSIFY="[DRY-RUN]no-model",
    KAIROS_ENV="local",
)

from agent.budget import DailyLedger, RunBudget  # noqa: E402
from agent.dryrun import StubAssessor, StubMetrics, StubResult, build_stub_agents  # noqa: E402
from agent.models import FounderProfile  # noqa: E402
from agent.scout import new_run_context, run_once  # noqa: E402
from agent.tools.discovery import SeedCatalog  # noqa: E402
from api.repository import SqliteRepository  # noqa: E402


class DelayedAssessor(StubAssessor):
    delay = 0.2

    async def invoke_async(self, prompt, *, structured_output_model=None, limits=None):
        await asyncio.sleep(self.delay)
        self.prompts.append(prompt)
        return StubResult(
            structured_output=self.respond(structured_output_model, prompt),
            metrics=StubMetrics(accumulated_usage={"inputTokens": 1_000, "outputTokens": 200, "totalTokens": 1_200}),
        )


async def one(concurrency: int, db_dir: str):
    profile = FounderProfile.model_validate_json((REPO / "data/demo_founder.json").read_text())
    ctx = new_run_context(
        profile=profile,
        repo=SqliteRepository(f"sqlite:///{db_dir}/bench.db"),
        budget=RunBudget(
            max_run_tokens=250_000,
            max_assessments=25,
            daily_usd_cap=0.0,
            ledger=DailyLedger(Path(db_dir)),
            assessment_concurrency=concurrency,
        ),
    )
    agents = build_stub_agents(ctx)
    agents.assessor = DelayedAssessor(ctx)
    ctx.agents = agents
    started = time.perf_counter()
    report = await run_once(ctx, [SeedCatalog(REPO / "data/opportunities.seed.json")])
    wall = time.perf_counter() - started
    fingerprint = {
        "assessments": [(k, v.verdict, v.reason) for k, v in ctx.assessments.items()],
        "skips": [(s.opportunity_id, s.stage, s.reason) for s in report.skips],
        "counters": (report.scanned, report.filtered_out, report.judged, report.surfaced),
        "halted": report.halted_reason,
        "tokens": report.usage.total_tokens,
        "inbox": [i.opportunity_id for i in ctx.pending_inbox],
    }
    return wall, fingerprint


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--delay", type=float, default=0.2)
    parser.add_argument("--repeat", type=int, default=3)
    args = parser.parse_args()
    DelayedAssessor.delay = args.delay
    results = {}
    fingerprints = {}
    for concurrency in (1, 2, 4):
        walls = []
        for _ in range(args.repeat):
            with tempfile.TemporaryDirectory() as d:
                wall, fingerprint = asyncio.run(one(concurrency, d))
            walls.append(wall)
            fingerprints.setdefault(concurrency, fingerprint)
            assert fingerprint == fingerprints[concurrency], "nondeterministic within a level"
        results[concurrency] = round(statistics.median(walls), 3)
    same = all(fp == fingerprints[1] for fp in fingerprints.values())
    print(json.dumps({
        "delay_s": args.delay,
        "assessments": len(fingerprints[1]["assessments"]),
        "wall_s_median": results,
        "speedup_vs_sequential": {k: round(results[1] / v, 2) for k, v in results.items()},
        "identical_reports": same,
        "tokens": fingerprints[1]["tokens"],
    }, indent=1))


if __name__ == "__main__":
    main()
