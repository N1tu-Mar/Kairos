"""Cost and rate caps. Code, not discipline (Section 9, rule 12).

Three caps, all enforced here:

*   **Per-run token ceiling** — cumulative across every model call in a run.
*   **Assessment cap** — the Assessor judges at most N opportunities.
*   **Daily USD cap** — global, persisted across runs, survives a restart.

The $50 of hackathon credits covers compute; Bedrock model tokens bill
separately. A cron loop left running overnight is the specific failure this
file exists to prevent.

When a cap trips, the run **halts and reports**. It does not degrade, it does
not surface a partial digest, and it does not quietly skip the remaining
work. `RunReport.halted_reason` carries the explanation to the UI.

The daily ledger is a JSON file keyed by UTC date. Cheap, inspectable, and
correct for a single-process scheduled run. It is not safe across concurrent
processes — noted in DECISIONS.md, alongside the DynamoDB atomic-counter
upgrade path.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, timezone, datetime
from pathlib import Path

from agent.models import TokenUsage


class BudgetExceeded(RuntimeError):
    """A cap tripped. Caught once, at the orchestrator, and reported."""

    def __init__(self, cap: str, detail: str) -> None:
        super().__init__(f"{cap}: {detail}")
        self.cap = cap
        self.detail = detail


@dataclass(frozen=True)
class TierPrice:
    """USD per 1M tokens. Zeroes produce a visibly-zero estimate, not a guess."""

    input_per_mtok: float = 0.0
    output_per_mtok: float = 0.0

    def cost(self, input_tokens: int, output_tokens: int) -> float:
        return (
            input_tokens * self.input_per_mtok + output_tokens * self.output_per_mtok
        ) / 1_000_000


class DailyLedger:
    """Persisted daily spend, keyed by UTC date."""

    def __init__(self, state_dir: Path) -> None:
        self.path = Path(state_dir) / "daily_spend.json"

    def _load(self) -> dict[str, float]:
        if not self.path.exists():
            return {}
        try:
            return json.loads(self.path.read_text())
        except (json.JSONDecodeError, OSError) as exc:
            # No silent fallback: a corrupt ledger means we cannot prove we
            # are under the cap, so we refuse to spend rather than reset it.
            raise BudgetExceeded(
                "DAILY_USD_CAP",
                f"spend ledger at {self.path} is unreadable ({exc}); "
                f"refusing to run without a verifiable daily total",
            ) from exc

    def spent_today(self, today: date | None = None) -> float:
        today = today or datetime.now(timezone.utc).date()
        return float(self._load().get(today.isoformat(), 0.0))

    def add(self, amount: float, today: date | None = None) -> float:
        today = today or datetime.now(timezone.utc).date()
        data = self._load()
        key = today.isoformat()
        data[key] = float(data.get(key, 0.0)) + amount
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, indent=2, sort_keys=True))
        return data[key]


@dataclass
class RunBudget:
    """Per-run accounting. One instance per scheduled run.

    Call `charge()` after every model invocation. It raises the moment a cap
    is crossed — the caller does not get to check a flag and forget.
    """

    max_run_tokens: int
    max_assessments: int
    daily_usd_cap: float
    ledger: DailyLedger
    prices: dict[str, TierPrice] = field(default_factory=dict)

    usage: TokenUsage = field(default_factory=TokenUsage)
    assessments_made: int = 0

    @classmethod
    def from_settings(cls, settings) -> RunBudget:
        """Build from `agent.config.settings()`. Kept out of the constructor
        so tests can run with no `.env` and no AWS account."""
        return cls(
            max_run_tokens=settings.max_run_tokens,
            max_assessments=settings.max_assessments,
            daily_usd_cap=settings.daily_usd_cap,
            ledger=DailyLedger(settings.state_dir),
            prices={
                "reasoning": TierPrice(
                    settings.prices.reasoning_in, settings.prices.reasoning_out
                ),
                "classify": TierPrice(
                    settings.prices.classify_in, settings.prices.classify_out
                ),
            },
        )

    # ── Token + spend ────────────────────────────────────────────────────

    def charge(self, *, tier: str, input_tokens: int, output_tokens: int) -> TokenUsage:
        """Record one model call and enforce the ceilings.

        Both caps are checked *after* recording, so the report always shows
        what was actually spent, including the call that crossed the line.
        """
        price = self.prices.get(tier, TierPrice())
        cost = price.cost(input_tokens, output_tokens)

        self.usage.add(
            TokenUsage(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=input_tokens + output_tokens,
                usd_estimate=cost,
            )
        )

        spent_today = self.ledger.add(cost) if cost else self.ledger.spent_today()

        if self.usage.total_tokens > self.max_run_tokens:
            raise BudgetExceeded(
                "RUN_TOKEN_CEILING",
                f"{self.usage.total_tokens:,} tokens used, ceiling is "
                f"{self.max_run_tokens:,}",
            )
        if self.daily_usd_cap > 0 and spent_today > self.daily_usd_cap:
            raise BudgetExceeded(
                "DAILY_USD_CAP",
                f"${spent_today:.2f} spent today, cap is ${self.daily_usd_cap:.2f}",
            )
        return self.usage

    def charge_agent_result(self, result, *, tier: str) -> TokenUsage:
        """Charge from a Strands `AgentResult`.

        `result.metrics.accumulated_usage` is a `Usage` TypedDict with
        `inputTokens` / `outputTokens` / `totalTokens`. Verified against
        strands-agents 1.53.0; see DECISIONS.md.
        """
        usage = result.metrics.accumulated_usage
        return self.charge(
            tier=tier,
            input_tokens=int(usage["inputTokens"]),
            output_tokens=int(usage["outputTokens"]),
        )

    # ── Rate ─────────────────────────────────────────────────────────────

    def take_assessment_slot(self) -> None:
        """Consume one of the run's assessment slots, or halt."""
        if self.assessments_made >= self.max_assessments:
            raise BudgetExceeded(
                "ASSESSMENT_CAP",
                f"already judged {self.assessments_made} opportunities this run",
            )
        self.assessments_made += 1

    def remaining_tokens(self) -> int:
        return max(0, self.max_run_tokens - self.usage.total_tokens)

    def strands_limits(self, share: float = 0.25) -> dict[str, int]:
        """A per-invocation `Limits` dict for `Agent.invoke_async`.

        Strands enforces its own soft cap per call; this file enforces the
        hard cap for the run. Belt and braces: a single runaway sub-agent
        cannot burn the whole run's budget before our check fires.
        """
        return {"total_tokens": max(1, int(self.remaining_tokens() * share))}
