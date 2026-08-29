"""Stub sub-agents for running the pipeline with no AWS account.

This exists so `uv run python scripts/run_scout.py --dry-run` works from a
clean clone, which is what makes the README's setup instructions honest. It
exercises discovery, the deterministic filter, ranking, the escalation
policy, idempotency and the ship gate — everything except the judgment.

Section 0.5 rule 4 forbids silent fallbacks and rule 6 forbids placeholder
data that could be mistaken for real. So this is:

*   **Never automatic.** It requires an explicit `--dry-run` flag. Nothing
    falls back to it when Bedrock is unreachable — a failed model call fails.
*   **Loudly labelled.** Every string it produces is prefixed
    `[DRY RUN — no model was called]`, and the runner prints a banner. It
    must never appear in the demo video.

The verdicts come from a deterministic rule over structured fields, not from
randomness, so the same catalog always produces the same counters.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from agent.models import Assessment

LABEL = "[DRY RUN — no model was called]"

BANNER = f"""
{'=' * 68}
  DRY RUN — no model was called. Every verdict below comes from a
  hard-coded rule, not from judgment. Do not screenshot this as a
  demonstration of the agent's reasoning.
{'=' * 68}
"""


@dataclass
class StubMetrics:
    """Zero usage, and that is the honest number.

    No model was called, so nothing was spent. Reporting a plausible-looking
    token count here would put a fabricated figure into the same ledger that
    enforces the daily cap.
    """

    accumulated_usage: dict = field(
        default_factory=lambda: {"inputTokens": 0, "outputTokens": 0, "totalTokens": 0}
    )


@dataclass
class StubResult:
    """The shape `structured_call` reads back from `Agent.invoke_async`."""

    structured_output: object
    metrics: StubMetrics = field(default_factory=StubMetrics)
    stop_reason: str = "end_turn"


class StubAgent:
    """Records prompts and answers from a rule. Subclasses implement `respond`."""

    def __init__(self) -> None:
        """`prompts` accumulates every prompt this stub was given, so a test can assert what the pipeline would have sent without a model existing."""
        self.prompts: list[str] = []

    async def invoke_async(self, prompt, *, structured_output_model=None, limits=None):
        """Same signature as a real Strands agent, so the pipeline cannot tell the difference.

        `limits` is accepted and ignored — the stub costs nothing, so there is
        no budget to enforce. That also means a dry run exercises none of the
        budget-enforcement paths.
        """
        self.prompts.append(prompt)
        return StubResult(structured_output=self.respond(structured_output_model, prompt))

    def respond(self, output_model, prompt):  # pragma: no cover - overridden
        """Produce the structured output for one call. Subclass responsibility."""
        raise NotImplementedError


class StubAssessor(StubAgent):
    """Judges on structured fields alone, so the result is reproducible.

    Holds the `RunContext` rather than a snapshot of it: discovery replaces
    `ctx.retrieved` wholesale, and a copy taken at construction time would
    still be empty by the time the first assessment runs.
    """

    def __init__(self, ctx) -> None:
        """Holds `ctx` by reference, not by copy — see the class docstring."""
        super().__init__()
        self.ctx = ctx

    def respond(self, output_model, prompt):
        """Identify which opportunity the prompt is about, then judge it deterministically."""
        return self._judge(self._match(prompt))

    def _match(self, prompt: str):
        """Find the retrieved opportunity whose title appears in the prompt.

        Substring matching on the title, which is enough for a dry run and is
        also its main limitation: two opportunities whose titles nest (one a
        prefix of the other) resolve to whichever the dict yields first. Returns
        None when nothing matches, which becomes an INSUFFICIENT_INFO verdict.
        """
        for opportunity in self.ctx.retrieved.values():
            if opportunity.title and opportunity.title in prompt:
                return opportunity
        return None

    def _judge(self, opportunity) -> Assessment:
        """Map structured eligibility fields to a verdict, with no model involved.

        The ordering is the behaviour: resolvable blockers are checked before the
        award floor, so an opportunity that is both under-budget and missing a
        faculty PI surfaces as an actionable MAYBE rather than a silent SKIP. An
        unstated degree requirement is INSUFFICIENT_INFO rather than a pass —
        the same three-valued discipline the real Assessor runs under.

        This is a fixture, not a second implementation of the rules. It is not
        kept in sync with `agent/tools/eligibility.py` and must not be read as a
        reference for what the real filter does.
        """
        if opportunity is None:
            return Assessment(
                verdict="INSUFFICIENT_INFO",
                reason=f"{LABEL} could not identify the opportunity.",
                effort_hours=0.0,
            )

        hours = opportunity.effort_hours_estimate or 6.0
        award = opportunity.best_award or 0
        rules = opportunity.eligibility
        profile = self.ctx.profile

        if rules.requires_faculty_pi and not profile.has_faculty_advisor:
            return Assessment(
                verdict="MAYBE",
                reason=f"{LABEL} the program requires a faculty PI and you do not have one yet.",
                effort_hours=hours,
                blocker="requires a faculty principal investigator",
                blocker_founder_resolvable=True,
            )
        if rules.entity_types and profile.entity_type not in rules.entity_types:
            return Assessment(
                verdict="MAYBE",
                reason=f"{LABEL} the program requires a formed legal entity.",
                effort_hours=hours,
                blocker="requires a formed legal entity",
                blocker_founder_resolvable=True,
            )
        if award < profile.min_award:
            return Assessment(
                verdict="SKIP",
                reason=f"{LABEL} the award is below your stated floor.",
                effort_hours=hours,
            )
        if rules.degree_levels is None:
            return Assessment(
                verdict="INSUFFICIENT_INFO",
                reason=f"{LABEL} the source does not state a degree requirement.",
                effort_hours=hours,
            )
        return Assessment(
            verdict="APPLY",
            reason=f"{LABEL} every stated eligibility rule matched your profile.",
            effort_hours=hours,
        )


class StubDrafter(StubAgent):
    """Refuses to write anything. Every field goes to the founder."""

    def respond(self, output_model, prompt):
        # `output_model` is the Drafter's DraftProposal. Returning zero fields
        # makes every asked field NEEDS_FOUNDER, which is the correct shape
        # for "no model was consulted".
        """Return an empty field list, so every asked field becomes NEEDS_FOUNDER."""
        return output_model(fields=[])


class StubAuditor(StubAgent):
    """Audits nothing. Every field comes back without a verdict.

    Combined with `StubDrafter`, a dry run produces a draft with no GENERATED
    fields, so there is nothing for the auditor to have an opinion about.
    """

    def respond(self, output_model, prompt):
        """Return an empty audit. Nothing was generated, so nothing is audited."""
        return output_model(fields=[])


def build_stub_agents(ctx):
    """A `SubAgents` bundle backed by stubs. Import-time cheap, no AWS."""
    from agent.runtime import SubAgents

    return SubAgents(
        assessor=StubAssessor(ctx),
        assessor_version="dry-run",
        drafter=StubDrafter(),
        drafter_version="dry-run",
        auditor=StubAuditor(),
        auditor_version="dry-run",
    )
