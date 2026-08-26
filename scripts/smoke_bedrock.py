"""One real Bedrock call per tier. Deliberately not a test.

    uv run python scripts/smoke_bedrock.py --tier classify
    uv run python scripts/smoke_bedrock.py --tier reasoning
    uv run python scripts/smoke_bedrock.py --tier both

The test suite runs offline and must keep doing so — that property is the
reason the deterministic layer is worth anything. But an offline suite cannot
tell you whether the model ID in `.env` exists in your region, whether it needs
an inference profile, or whether `metrics.accumulated_usage` still spells its
keys the way `RunBudget.charge_agent_result` reads them. A key-name mismatch
there charges zero and the run looks free, which is exactly the bug this
script exists to make impossible to miss.

So: smallest possible prompt, tightest possible ceiling, one tier at a time,
and every number printed.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pydantic import BaseModel, Field  # noqa: E402
from strands import Agent  # noqa: E402

from agent.budget import RunBudget  # noqa: E402
from agent.config import ConfigError, settings  # noqa: E402
from agent.prompting import structured_call  # noqa: E402
from agent.subagents.base import build_model  # noqa: E402

#: Deliberately trivial. This checks the wiring, not the model.
PROMPT = "Name the capital of France and how confident you are, 0 to 1."

#: A ceiling low enough that a bug cannot cost real money, but above a
#: sane answer to the prompt above.
SMOKE_TOKEN_CEILING = 4_000


class Smoke(BaseModel):
    """The smallest schema that still proves structured output works."""

    answer: str = Field(description="The answer, in one word.")
    confidence: float = Field(ge=0.0, le=1.0)


def explain(exc: Exception) -> str:
    """The two failures worth naming, because both look like a broken build."""
    text = str(exc)
    if "AccessDeniedException" in text or "AccessDenied" in text:
        return (
            "Bedrock refused the call. Anthropic models need access enabled per\n"
            "  model in the Bedrock console (Model access), which is a click this\n"
            "  script cannot make for you. Enable it, then re-run."
        )
    if "on-demand throughput" in text or "inference profile" in text:
        return (
            "This model is not reachable by its bare foundation-model ID. Run\n"
            "    aws bedrock list-inference-profiles --region "
            f"{settings().region}\n"
            "  and put that profile ID (prefixed `us.` or `global.`) in .env instead."
        )
    if "ValidationException" in text:
        return (
            "Bedrock rejected the request shape or the model ID. Confirm the ID\n"
            "  against `aws bedrock list-foundation-models` for this region."
        )
    if "NoCredentials" in text or "Unable to locate credentials" in text:
        return "No AWS credentials on this machine. Run `aws configure` first."
    return ""


async def smoke(tier_name: str) -> bool:
    config = settings()
    tier = getattr(config, tier_name)
    budget = RunBudget.from_settings(config)
    budget.max_run_tokens = SMOKE_TOKEN_CEILING

    print(f"\n── {tier_name} ─────────────────────────────────────────────")
    print(f"  region   {config.region}")
    print(f"  model    {tier.model_id}")

    agent = Agent(
        model=build_model(tier),
        system_prompt="Answer with structured output. Nothing else.",
        name=f"smoke-{tier_name}",
        callback_handler=None,
    )

    started = time.monotonic()
    try:
        result = await structured_call(
            agent, Smoke, PROMPT, agent_name=f"smoke-{tier_name}",
            budget=budget, tier=tier_name,
        )
    except Exception as exc:  # noqa: BLE001 — this script's whole job is the error
        elapsed = time.monotonic() - started
        print(f"  FAILED   after {elapsed:.1f}s")
        print(f"  {type(exc).__name__}: {exc}")
        hint = explain(exc)
        if hint:
            print(f"\n  {hint}")
        return False

    elapsed = time.monotonic() - started
    usage = budget.usage
    print(f"  answer   {result.answer!r} (confidence {result.confidence})")
    print(f"  latency  {elapsed:.1f}s")
    print(f"  tokens   {usage.input_tokens} in, {usage.output_tokens} out")
    print(f"  cost     ${usage.usd_estimate:.6f}")

    if usage.total_tokens == 0:
        print(
            "\n  WARNING: the call succeeded but billed zero tokens. That means\n"
            "  metrics.accumulated_usage no longer has the keys\n"
            "  RunBudget.charge_agent_result reads, so the run ceiling and the\n"
            "  daily cap are not enforcing. Fix this before any scheduled run."
        )
        return False
    if usage.usd_estimate == 0:
        print(
            "\n  Note: cost reads $0 because the KAIROS_PRICE_* values in .env are\n"
            "  still 0. Token accounting is working; only the price is missing."
        )
    return True


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--tier",
        choices=["reasoning", "classify", "both"],
        default="classify",
        help="Which model tier to call. Defaults to the cheap one.",
    )
    args = parser.parse_args()

    try:
        settings()
    except ConfigError as exc:
        print(f"\n{exc}\n")
        return 2

    tiers = ["classify", "reasoning"] if args.tier == "both" else [args.tier]
    results = [await smoke(name) for name in tiers]
    print()
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
