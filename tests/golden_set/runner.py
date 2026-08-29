"""Run one case through the real pipeline.

Nothing here reimplements the thing being measured. `draft_application`,
`audit_draft` and `ship_gate` are imported and called exactly as `toolset.py`
calls them; the only substitution is where the two model calls get their
answers. Offline that is the case fixture. With `--live` it is Bedrock.

That substitution is the whole design. It means the offline number measures
the deterministic defense layer given a stated model output, and the live
number measures the same layer given a real one — same scorer, same cases,
two honestly different claims.
"""

from __future__ import annotations

from dataclasses import dataclass

from agent import guardrails
from agent.budget import RunBudget
from agent.config import settings, stamp_placeholder_models
from agent.models import (
    AuditReport,
    Draft,
    FounderProfile,
    GateResult,
    KnowledgeBase,
)
from agent.subagents.auditor import ProposedAudit, audit_draft
from agent.subagents.drafter import draft_application
from tests.golden_set.loader import Case

def offline_settings():
    """Settings with no real Bedrock model IDs, for the fixture path.

    Same shape as `scripts/run_scout.py::_dry_run_settings` and for the same
    reason: `agent/config.py` refuses to start on empty model IDs, which is
    correct for a run that calls a model and wrong for one that replays a
    fixture. The IDs are stamped as obviously-fake strings rather than the
    check being relaxed — the offline eval must never be able to reach
    Bedrock by accident, and a `[GOLDEN-SET]` model ID on a `DraftField` says
    plainly where that field came from.
    """
    stamp_placeholder_models("[GOLDEN-SET]no-model")
    return settings()


#: The founder every case is drafted for. Deliberately sparse: the profile
#: carries no knowledge of its own, so the only material available is the
#: case's chunks and nothing leaks in from a fixture profile.
EVAL_FOUNDER = "founder_goldenset"


class ScriptedAgent:
    """Returns the case's fixture instead of calling a model.

    Reports zero usage, which is the honest number — no model was called, so
    nothing was spent, and putting a plausible token count into the same
    ledger that enforces the daily cap would be inventing a figure.
    """

    def __init__(self, response) -> None:
        """One response, returned for every call. `prompts` records what it was asked, so a test can assert what the pipeline would have sent."""
        self.response = response
        self.prompts: list[str] = []

    async def invoke_async(self, prompt, *, structured_output_model=None, limits=None):
        """Return the fixture, ignoring the prompt and the requested schema.

        The schema is ignored on purpose: the fixture is already a validated
        model, and re-validating it here would test the case file rather than the
        pipeline.
        """
        self.prompts.append(prompt)
        return _ScriptedResult(structured_output=self.response)


@dataclass
class _ScriptedMetrics:
    """Zero usage. No model was called, so no tokens were spent.

    A plausible-looking count here would flow into the same ledger that
    enforces the daily cap, which would make the offline eval charge for work
    it never did.
    """

    accumulated_usage: dict = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        """Default the usage dict without sharing one mutable default across instances."""
        if self.accumulated_usage is None:
            self.accumulated_usage = {
                "inputTokens": 0,
                "outputTokens": 0,
                "totalTokens": 0,
            }


@dataclass
class _ScriptedResult:
    """Stands in for `AgentResult`, with zero-usage metrics."""

    structured_output: object
    metrics: _ScriptedMetrics = None  # type: ignore[assignment]
    stop_reason: str = "end_turn"

    def __post_init__(self) -> None:
        """Default the metrics, again avoiding a shared mutable default."""
        if self.metrics is None:
            self.metrics = _ScriptedMetrics()


@dataclass
class CaseRun:
    """What the pipeline actually did with one case."""

    case: Case
    draft: Draft
    audit: AuditReport
    gate: GateResult

    def shipped(self) -> set[str]:
        """Fields that would reach a real application.

        A blocked draft ships nothing. That is the point of blocking it, and
        scoring per-field while ignoring the draft-level verdict would credit
        the system for text it never released.
        """
        if not self.gate.passed:
            return set()
        return {
            f.field_id
            for f in self.draft.fields
            if f.status in {"GENERATED", "KNOWN", "REUSED"}
        }

    def status_of(self, field_id: str) -> str:
        """The final status of one field, or `"MISSING"` if the draft has no such field.

        `MISSING` is distinct from `NEEDS_FOUNDER`: one means the pipeline never
        produced the field at all, the other means it deliberately declined to
        answer it.
        """
        for f in self.draft.fields:
            if f.field_id == field_id:
                return f.status
        return "MISSING"

    def note_of(self, field_id: str) -> str:
        """The audit note left on a field, or an empty string."""
        for f in self.draft.fields:
            if f.field_id == field_id:
                return f.audit_note or ""
        return ""


def profile_for(case: Case) -> FounderProfile:
    """The sparse founder profile every case is drafted against.

    Deliberately carries no knowledge base of its own, so the only material
    available to the Drafter is the case's own chunks — otherwise a case
    could appear grounded because of a fixture profile rather than because of
    its evidence.
    """
    return FounderProfile(
        founder_id=EVAL_FOUNDER,
        degree_level="undergrad",
        institution="[DEMO] Example University",
        citizenship="us_citizen",
        entity_type="none",
        team_size=2,
        stage="prototype",
        funding_range=(2_000, 50_000),
        equity_ok=False,
        has_faculty_advisor=False,
        max_application_hours=8.0,
        knowledge_base=list(case.chunks),
        traction=dict(case.traction),
    )


async def run_case(case: Case, *, drafter=None, auditor=None) -> CaseRun:
    """Execute one case. Pass real agents to score a live model instead."""
    profile = profile_for(case)
    kb: KnowledgeBase = case.knowledge_base(EVAL_FOUNDER)
    config = settings() if (drafter and auditor) else offline_settings()
    budget = RunBudget.from_settings(config)

    drafter = drafter or ScriptedAgent(case.draft_proposal())
    auditor = auditor or ScriptedAgent(ProposedAudit(fields=case.audit_fields()))

    draft = await draft_application(
        drafter,
        "golden-set",
        draft_id=f"golden:{case.case_id}",
        budget=budget,
        form=case.form(),
        opportunity=case.opportunity,
        profile=profile,
        kb=kb,
    )

    audit = await audit_draft(auditor, "golden-set", draft, kb, budget=budget)

    gate = guardrails.ship_gate(
        draft,
        kb,
        retrieved=[case.opportunity],
        opportunity=case.opportunity,
        audit=audit,
        required_field_ids={f.field_id for f in case.fields if f.required},
    )

    return CaseRun(case=case, draft=draft, audit=audit, gate=gate)
