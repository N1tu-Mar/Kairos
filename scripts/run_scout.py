"""Run Scout once from the command line.

    uv run python scripts/run_scout.py --demo          # synthetic catalog
    uv run python scripts/run_scout.py --no-grants-gov # offline
    uv run python scripts/run_scout.py --schedule      # local APScheduler loop

This is the same code path EventBridge invokes. When the console prints
"Scanned 214. Discarded 198. Judged 16. Surfaced 3.", the project is real.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.budget import RunBudget  # noqa: E402
from agent.config import REPO_ROOT, settings  # noqa: E402
from agent.models import ApplicationForm, FounderProfile  # noqa: E402
from agent.runtime import SubAgents  # noqa: E402
from agent.scout import new_run_context, run_once  # noqa: E402
from agent.tools.discovery import GrantsGovClient, GrantsGovSource, SeedCatalog  # noqa: E402
from api.repository import SqliteRepository  # noqa: E402


def build_sources(demo: bool, grants_gov: bool):
    config = settings()
    catalog = "opportunities.demo.json" if demo else "opportunities.seed.json"
    sources = [
        SeedCatalog(
            config.data_dir / catalog,
            allow_unverified=demo or config.allow_unverified_seed,
        )
    ]
    if grants_gov:
        sources.append(
            GrantsGovSource(
                GrantsGovClient(config.grants_gov_base_url, config.http_timeout_s)
            )
        )
    return sources


def _dry_run_settings():
    """Settings without real Bedrock model IDs.

    `agent/config.py` refuses to start with empty model IDs, which is correct
    for a real run and wrong for a dry run that never calls a model. The IDs
    are stamped as obviously-fake strings rather than the check being relaxed.
    """
    import os

    os.environ.setdefault("BEDROCK_MODEL_REASONING", "[DRY-RUN]no-model")
    os.environ.setdefault("BEDROCK_MODEL_CLASSIFY", "[DRY-RUN]no-model")
    settings.cache_clear()
    return settings()


def load_forms() -> dict[str, ApplicationForm]:
    directory = REPO_ROOT / "data" / "forms"
    return {
        (form := ApplicationForm.model_validate(json.loads(p.read_text()))).opportunity_id: form
        for p in sorted(directory.glob("*.json"))
    } if directory.exists() else {}


async def one_run(args) -> int:
    config = settings() if not args.dry_run else _dry_run_settings()
    repo = SqliteRepository(config.db_url)

    profile = repo.get_profile(args.founder)
    if profile is None:
        path = REPO_ROOT / "data" / "demo_founder.json"
        if not path.exists():
            print(f"no profile for {args.founder} and no demo profile to fall back on")
            return 1
        profile = FounderProfile.model_validate_json(path.read_text())
        repo.save_profile(profile)

    ctx = new_run_context(
        profile=profile,
        repo=repo,
        budget=RunBudget.from_settings(config),
    )
    ctx.forms = load_forms()

    if args.dry_run:
        from agent.dryrun import BANNER, build_stub_agents

        print(BANNER, flush=True)
        ctx.agents = build_stub_agents(ctx)
    else:
        ctx.agents = SubAgents.build()

    report = await run_once(ctx, build_sources(args.demo, not args.no_grants_gov))

    print()
    print(report.headline())
    print(f"  {report.duration_s:.1f}s · {report.usage.total_tokens:,} tokens · "
          f"${report.usage.usd_estimate:.4f} estimated")
    if report.sources_failed:
        for failure in report.sources_failed:
            print(f"  source failed: {failure.source} — {failure.detail}")
    if report.halted_reason:
        print(f"  HALTED: {report.halted_reason}")
    for item in repo.list_inbox(profile.founder_id)[: report.surfaced]:
        print(f"  → {item.headline}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--founder", default="founder_demo")
    parser.add_argument("--demo", action="store_true", help="use the synthetic catalog")
    parser.add_argument("--no-grants-gov", action="store_true", help="skip the live source")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="run the pipeline with stub judgment and no AWS account. Never "
             "falls back to this automatically; output is labelled [DRY RUN].",
    )
    parser.add_argument("--schedule", action="store_true", help="run on a local cron loop")
    parser.add_argument("--hour", type=int, default=6, help="local schedule hour")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    if not args.schedule:
        return asyncio.run(one_run(args))

    # Locally APScheduler stands in for EventBridge Scheduler. Capped at one
    # run a day: Bedrock tokens bill separately from the credits, and a cron
    # loop left running overnight is how that bill gets surprising.
    from apscheduler.schedulers.blocking import BlockingScheduler

    scheduler = BlockingScheduler()
    scheduler.add_job(
        lambda: asyncio.run(one_run(args)), "cron", hour=args.hour, minute=0
    )
    print(f"scheduled: daily at {args.hour:02d}:00 local. Ctrl-C to stop.")
    scheduler.start()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
