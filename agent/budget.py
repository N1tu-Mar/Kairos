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

The daily ledger is SQLite, keyed by UTC date. Every `add` is one
`BEGIN IMMEDIATE` transaction — increment and read-back are a single atomic
step, so two concurrent processes cannot both read a stale total and both
conclude they are under the cap. A legacy `daily_spend.json` from earlier
versions is imported once and left in place as its own backup. DynamoDB
remains the documented upgrade path if workers ever span machines
(DECISIONS.md).
"""

from __future__ import annotations

import json
import sqlite3
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
    """Persisted daily spend, keyed by UTC date. Atomic across processes.

    Backed by SQLite so an increment is check-and-write in one transaction.
    The failure posture is unchanged from the JSON version: anything that
    prevents a verifiable total — a corrupt database, an unreadable legacy
    file — raises `BudgetExceeded` and the run halts. We refuse to spend
    money we cannot account for; we never reset the ledger to zero.
    """

    def __init__(self, state_dir: Path) -> None:
        self.dir = Path(state_dir)
        self.path = self.dir / "daily_spend.sqlite3"
        #: The pre-migration ledger. Imported once, then kept as a backup —
        #: this class never writes to it and never deletes it.
        self.legacy_path = self.dir / "daily_spend.json"

    def _refuse(self, exc: Exception) -> BudgetExceeded:
        return BudgetExceeded(
            "DAILY_USD_CAP",
            f"spend ledger at {self.path} is unreadable ({exc}); "
            f"refusing to run without a verifiable daily total",
        )

    def _connect(self) -> sqlite3.Connection:
        self.dir.mkdir(parents=True, exist_ok=True)
        try:
            conn = sqlite3.connect(self.path, timeout=10)
            conn.execute(
                "CREATE TABLE IF NOT EXISTS daily_spend"
                " (day TEXT PRIMARY KEY, usd REAL NOT NULL)"
            )
        except sqlite3.Error as exc:
            raise self._refuse(exc) from exc
        self._import_legacy(conn)
        return conn

    def _import_legacy(self, conn: sqlite3.Connection) -> None:
        """Carry earlier JSON totals forward, exactly once per day-key.

        `INSERT OR IGNORE` makes this idempotent: a day already in the
        database — imported before, or already accumulating new spend — is
        never touched again. A corrupt legacy file is still a refusal, not a
        reset, because its totals are part of today's proof.
        """
        if not self.legacy_path.exists():
            return
        try:
            data = json.loads(self.legacy_path.read_text())
        except (json.JSONDecodeError, OSError) as exc:
            raise self._refuse(exc) from exc
        try:
            with conn:
                for day, usd in data.items():
                    conn.execute(
                        "INSERT OR IGNORE INTO daily_spend (day, usd) VALUES (?, ?)",
                        (str(day), float(usd)),
                    )
        except (sqlite3.Error, TypeError, ValueError, AttributeError) as exc:
            raise self._refuse(exc) from exc

    def spent_today(self, today: date | None = None) -> float:
        today = today or datetime.now(timezone.utc).date()
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT usd FROM daily_spend WHERE day = ?", (today.isoformat(),)
            ).fetchone()
            return float(row[0]) if row else 0.0
        except sqlite3.Error as exc:
            raise self._refuse(exc) from exc
        finally:
            conn.close()

    def add(self, amount: float, today: date | None = None) -> float:
        """Increment today's total and return it — one atomic step.

        `BEGIN IMMEDIATE` takes the writer lock before the read, so two
        concurrent calls serialise: each sees a total that includes every
        earlier call, and the call that crosses the cap is the one whose
        returned total says so.
        """
        today = today or datetime.now(timezone.utc).date()
        key = today.isoformat()
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "INSERT INTO daily_spend (day, usd) VALUES (?, ?)"
                " ON CONFLICT (day) DO UPDATE SET usd = usd + excluded.usd",
                (key, float(amount)),
            )
            total = conn.execute(
                "SELECT usd FROM daily_spend WHERE day = ?", (key,)
            ).fetchone()[0]
            conn.commit()
            return float(total)
        except sqlite3.Error as exc:
            raise self._refuse(exc) from exc
        finally:
            conn.close()


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
