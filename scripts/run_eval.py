"""The Section 11.11 golden set. Prints the number, whatever it is.

    uv run python scripts/run_eval.py             # the defense layer, offline
    uv run python scripts/run_eval.py --live      # the whole system, needs Bedrock
    uv run python scripts/run_eval.py --verbose   # per-field detail

Two modes, two honestly different claims:

*   **offline** replays a fixture Drafter proposal per case through the real
    `draft_application`, `audit_draft` and `ship_gate`. It measures the
    deterministic defense layer given a stated model output. No AWS account,
    no tokens, about a second.
*   **--live** puts a real Bedrock Drafter and Auditor in front of the same
    cases and the same scorer. That is the number Section 11.11 asks for, and
    it is the only one that may be published without the "defense layer"
    qualifier.

The README must say which mode produced the figure it prints.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.golden_set.loader import load_cases  # noqa: E402
from tests.golden_set.runner import run_case  # noqa: E402
from tests.golden_set.scorer import render, score  # noqa: E402


def live_agents():
    """Real sub-agents. Imported lazily so the offline path needs no `.env`."""
    from agent.subagents import auditor, drafter

    drafter_agent, _ = drafter.build()
    auditor_agent, _ = auditor.build()
    return drafter_agent, auditor_agent


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--live",
        action="store_true",
        help="Call Bedrock instead of replaying fixtures. Costs tokens.",
    )
    parser.add_argument("--verbose", action="store_true", help="Per-field outcomes.")
    parser.add_argument("--case", help="Run one case by id.")
    args = parser.parse_args()

    case_set = load_cases()
    cases = case_set.cases
    if args.case:
        cases = [c for c in cases if c.case_id == args.case]
        if not cases:
            print(f"no case with id {args.case!r}")
            return 2

    drafter_agent = auditor_agent = None
    if args.live:
        drafter_agent, auditor_agent = live_agents()

    runs = []
    for case in cases:
        runs.append(await run_case(case, drafter=drafter_agent, auditor=auditor_agent))

    card = score(runs)
    mode = (
        "LIVE — real Bedrock Drafter and Auditor"
        if args.live
        else "OFFLINE — fixture model output, real defense layer"
    )
    print(render(card, mode=mode))
    print(f"  {case_set.describe()}\n")

    if args.verbose:
        print("  per-field:")
        for outcome in card.outcomes:
            mark = "LEAK " if outcome.leaked else ("extra" if outcome.over_withheld else "  ok ")
            print(
                f"    [{mark}] {outcome.case_id:<40} {outcome.field_id:<14} "
                f"truth={outcome.truth:<8} status={outcome.status:<13} "
                f"shipped={str(outcome.shipped):<5}"
                + ("  (collateral)" if outcome.collateral else "")
            )
            if outcome.note and (outcome.leaked or outcome.over_withheld):
                print(f"             {outcome.note}")
        print()

    # A leak is the only failing condition. Over-withholding is a cost to
    # report, not a build break.
    return 1 if card.leaks else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
