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

from agent.budget import RunBudget, UnenforceableSpendCap  # noqa: E402
from agent.config import REPO_ROOT, settings, stamp_placeholder_models  # noqa: E402
from agent.models import ApplicationForm, FounderProfile  # noqa: E402
from agent.runtime import SubAgents  # noqa: E402
from agent.scout import new_run_context, run_once  # noqa: E402
from agent.tools.campus import CampusDiscoverySource  # noqa: E402
from agent.tools.discovery import (  # noqa: E402
    GrantsGovClient,
    GrantsGovSource,
    SeedCatalog,
    keywords_for_profile,
)
from api.repository import SqliteRepository  # noqa: E402


def build_sources(
    demo: bool,
    grants_gov: bool,
    profile: FounderProfile | None = None,
    live_campus_scrape: bool = False,
):
    """Assemble the CLI's discovery sources, mirroring `api/jobs.build_sources`.

    Two copies of this list exist on purpose — the flags differ between a
    terminal and a scheduled job — but they must agree on what each flag
    means. A source added to one and not the other makes the CLI and the
    dashboard disagree about what a run searched.
    """
    config = settings()
    catalog = "opportunities.demo.json" if demo else "opportunities.seed.json"
    sources = [
        SeedCatalog(
            config.data_dir / catalog,
            allow_unverified=demo or config.allow_unverified_seed,
        )
    ]
    if grants_gov:
        keywords = (
            keywords_for_profile(profile)
            if profile is not None
            else ("student", "undergraduate", "entrepreneurship")
        )
        sources.append(
            GrantsGovSource(
                GrantsGovClient(config.grants_gov_base_url, config.http_timeout_s),
                keywords=keywords,
            )
        )
    # Tier 3. Off unless KAIROS_ENABLE_BROWSER is set, and even then it adds
    # only campus rows a human marked ACCEPTED. A live sweep needs a second,
    # explicit opt-in because it makes network requests during a run.
    sources.append(
        CampusDiscoverySource(
            enabled=config.enable_browser,
            allow_live_scrape=config.enable_browser and live_campus_scrape,
        )
    )
    return sources


def _dry_run_settings():
    """Settings without real Bedrock model IDs.

    `agent/config.py` refuses to start with empty model IDs, which is correct
    for a real run and wrong for a dry run that never calls a model. The IDs
    are stamped as obviously-fake strings rather than the check being relaxed.
    """
    stamp_placeholder_models("[DRY-RUN]no-model")
    return settings()


def load_forms() -> dict[str, ApplicationForm]:
    """Load the transcribed application forms, keyed by opportunity id."""
    directory = REPO_ROOT / "data" / "forms"
    return {
        (form := ApplicationForm.model_validate(json.loads(p.read_text()))).opportunity_id: form
        for p in sorted(directory.glob("*.json"))
    } if directory.exists() else {}


async def one_run(args) -> int:
    """Execute a single pipeline run and print its report."""
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

    budget = RunBudget.from_settings(config)
    # A dry run calls no model and spends nothing, so an unenforceable
    # dollar cap is irrelevant there. A real run must not proceed under a
    # cap that arithmetic cannot enforce.
    try:
        budget.require_enforceable_spend_cap(calls_models=not args.dry_run)
    except UnenforceableSpendCap as exc:
        print(f"refusing to run: {exc}")
        return 2

    ctx = new_run_context(
        profile=profile,
        repo=repo,
        budget=budget,
    )
    ctx.forms = load_forms()

    if args.dry_run:
        from agent.dryrun import BANNER, build_stub_agents

        print(BANNER, flush=True)
        ctx.agents = build_stub_agents(ctx)
    else:
        ctx.agents = SubAgents.build()

    # Say which caps are actually doing work, every run, before the work.
    print(f"budget: {budget.enforcement_status().summary}")

    report = await run_once(
        ctx,
        build_sources(
            args.demo,
            not args.no_grants_gov,
            profile=profile,
            live_campus_scrape=args.campus_scrape,
        ),
    )

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
    """CLI entry for a local run. 0 on a completed run, non-zero when it could not start.

    A run that halts on a budget cap is a completed run with a report, so it
    exits 0 — the halt is in the output, not in the exit code.
    """
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
    parser.add_argument(
        "--campus-scrape",
        action="store_true",
        help="also run a live campus sweep during the run. Needs "
             "KAIROS_ENABLE_BROWSER=true. What it collects lands in the review "
             "file as NEEDS_HUMAN_REVIEW and cannot affect this run.",
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
