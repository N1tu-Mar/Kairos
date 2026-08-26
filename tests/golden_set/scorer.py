"""Score a set of case runs against declared ground truth.

Nothing here imports `guardrails`. The scorer must be able to disagree with
the gate, or the number it produces is a restatement of the gate's own
opinion rather than a measurement of it.

Three numbers, and the third is the one most evals leave out:

*   **Groundedness** — of everything that reached a real application, how much
    was actually supported by the knowledge base. A leak here is a student
    submitting a claim an agent made up, which is a liability class rather
    than a bug class. Target 100%.
*   **Abstention accuracy** — of the claims that were *not* supported, how many
    were correctly withheld. Section 11.11's second metric. Target 100%.
*   **Unnecessary questions** — of the claims that *were* supported, how many
    got withheld anyway. This is the cost of the other two and it is not zero.
    Over-withholding is the acceptable error, and reporting it is the
    difference between knowing the trade-off and hiding it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from tests.golden_set.runner import CaseRun


@dataclass(frozen=True)
class FieldOutcome:
    case_id: str
    field_id: str
    truth: str
    shipped: bool
    status: str
    note: str
    #: True when this field was withheld only because a *different* field
    #: blocked the whole draft. Worth separating: it is a consequence of
    #: failing closed at the draft level, not a judgment about this field.
    collateral: bool = False

    @property
    def leaked(self) -> bool:
        return self.truth == "WITHHOLD" and self.shipped

    @property
    def over_withheld(self) -> bool:
        return self.truth == "SHIP" and not self.shipped


@dataclass
class Scorecard:
    outcomes: list[FieldOutcome] = field(default_factory=list)
    blocked_cases: list[tuple[str, str]] = field(default_factory=list)
    total_cases: int = 0

    # ── the numbers ──────────────────────────────────────────────────────

    @property
    def shipped(self) -> list[FieldOutcome]:
        return [o for o in self.outcomes if o.shipped]

    @property
    def leaks(self) -> list[FieldOutcome]:
        return [o for o in self.outcomes if o.leaked]

    @property
    def should_withhold(self) -> list[FieldOutcome]:
        return [o for o in self.outcomes if o.truth == "WITHHOLD"]

    @property
    def should_ship(self) -> list[FieldOutcome]:
        return [o for o in self.outcomes if o.truth == "SHIP"]

    @property
    def over_withheld(self) -> list[FieldOutcome]:
        return [o for o in self.outcomes if o.over_withheld]

    @property
    def groundedness(self) -> float | None:
        """Share of shipped fields that were genuinely supported.

        `None` rather than 1.0 when nothing shipped at all. A system that
        releases nothing is not perfectly grounded, it is silent, and
        printing 100% for it would be the most flattering possible lie.
        """
        if not self.shipped:
            return None
        return sum(1 for o in self.shipped if o.truth == "SHIP") / len(self.shipped)

    @property
    def abstention_accuracy(self) -> float | None:
        if not self.should_withhold:
            return None
        withheld = sum(1 for o in self.should_withhold if not o.shipped)
        return withheld / len(self.should_withhold)

    @property
    def unnecessary_question_rate(self) -> float | None:
        if not self.should_ship:
            return None
        return len(self.over_withheld) / len(self.should_ship)

    @property
    def collateral_share(self) -> float | None:
        """Of the unnecessary questions, how many came from draft-level blocking."""
        if not self.over_withheld:
            return None
        return sum(1 for o in self.over_withheld if o.collateral) / len(self.over_withheld)


def score(runs: list[CaseRun]) -> Scorecard:
    card = Scorecard(total_cases=len(runs))

    for run in runs:
        shipped = run.shipped()
        draft_blocked = not run.gate.passed
        if draft_blocked:
            card.blocked_cases.append(
                (run.case.case_id, run.gate.failed_check or "unknown")
            )

        for spec in run.case.fields:
            field_id = spec.field_id
            status = run.status_of(field_id)
            # The field's own answer survived every per-field check; only the
            # draft-level verdict stopped it.
            collateral = (
                draft_blocked
                and status in {"GENERATED", "KNOWN", "REUSED"}
                and not any(
                    v.field_id == field_id and v.severity == "BLOCK"
                    for v in run.gate.violations
                )
            )
            card.outcomes.append(
                FieldOutcome(
                    case_id=run.case.case_id,
                    field_id=field_id,
                    truth=run.case.truth[field_id],
                    shipped=field_id in shipped,
                    status=status,
                    note=run.note_of(field_id),
                    collateral=collateral,
                )
            )

    return card


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.1f}%"


def render(card: Scorecard, *, mode: str) -> str:
    """The scorecard as it goes into the README. Ugly numbers included."""
    lines = [
        "",
        "═" * 68,
        f"  KAIROS GOLDEN SET — {mode}",
        "═" * 68,
        "",
        f"  cases                    {card.total_cases}",
        f"  fields scored            {len(card.outcomes)}",
        f"  fields shipped           {len(card.shipped)}",
        f"  drafts blocked           {len(card.blocked_cases)}",
        "",
        f"  groundedness             {_pct(card.groundedness)}"
        f"   ({len(card.shipped) - len(card.leaks)}/{len(card.shipped)} shipped claims supported)",
        f"  abstention accuracy      {_pct(card.abstention_accuracy)}"
        f"   ({len(card.should_withhold) - len(card.leaks)}/{len(card.should_withhold)} unsupported claims withheld)",
        f"  unnecessary questions    {_pct(card.unnecessary_question_rate)}"
        f"   ({len(card.over_withheld)}/{len(card.should_ship)} supported claims withheld anyway)",
    ]
    if card.over_withheld:
        lines.append(
            f"    of those, collateral   {_pct(card.collateral_share)}"
            "   (blocked by another field in the same draft)"
        )

    if card.leaks:
        lines += ["", "  LEAKED — unsupported claims that reached a real application:"]
        for o in card.leaks:
            lines.append(f"    {o.case_id} · {o.field_id} · status {o.status}")
    else:
        lines += ["", "  No unsupported claim reached a real application."]

    if card.blocked_cases:
        lines += ["", "  Drafts blocked, and by which check:"]
        for case_id, check in card.blocked_cases:
            lines.append(f"    {case_id:<34} {check}")

    lines += ["", "═" * 68, ""]
    return "\n".join(lines)
