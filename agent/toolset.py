"""The agent's tools (Section 6).

Deterministic tools are plain Python. Judgment tools are sub-agents. Both are
exposed to Scout through Strands' `@tool` decorator, bound to a `RunContext`
by closure so the orchestrating model never has to carry state it could
corrupt.

Every tool returns a **compact summary string**, not a serialised object.
Two reasons, and the second one matters more:

1.  Opportunity descriptions are large; round-tripping them through a context
    window is expensive.
2.  They are untrusted text from the open web. The less of it that reaches
    the orchestrator, the smaller the injection surface. The structured data
    stays in `RunContext`, where Python reads it.

Policy is enforced inside these functions, in Python. The model chooses the
order it calls them in; it does not get to choose whether the escalation
policy applies.
"""

from __future__ import annotations

import logging
from datetime import datetime

from strands import tool

from agent import guardrails
from agent.models import InboxItem, SkipRecord
from agent.prompting import Abstention
from agent.runtime import RunContext
from agent.tools.discovery import Source, discover_opportunities
from agent.tools.eligibility import hard_eligibility_filter

log = logging.getLogger("kairos.tools")


def build_toolset(ctx: RunContext, sources: list[Source]) -> list:
    """Bind the tools to one run and return them for `Agent(tools=...)`."""

    @tool
    def discover(since_iso: str = "") -> str:
        """Pull new and updated opportunities from every enabled source.

        Args:
            since_iso: ISO-8601 timestamp to pull changes since. Empty means
                everything currently open.
        """
        since = (
            datetime.fromisoformat(since_iso)
            if since_iso
            else datetime(1970, 1, 1)
        )
        found, failures = discover_opportunities(sources, since)

        ctx.retrieved = {o.id: o for o in found}
        ctx.report.scanned = len(found)
        ctx.report.sources_failed.extend(failures)

        failed = (
            "; ".join(f"{f.source} failed ({f.detail})" for f in failures)
            or "none"
        )
        return (
            f"Scanned {len(found)} opportunities from "
            f"{len({o.source for o in found})} source(s). Sources failed: {failed}."
        )

    @tool
    def filter_eligibility() -> str:
        """Run the deterministic eligibility gate over everything discovered.

        Pure Python, no model call. Reads structured fields only, so text
        inside an opportunity description cannot influence the result.
        """
        survivors, rejections, results = hard_eligibility_filter(
            list(ctx.retrieved.values()), ctx.profile, ctx.today
        )
        ctx.eligibility = results
        ctx.report.filtered_out = len(rejections)
        ctx.report.rejections.extend(rejections)
        ctx.report.skips.extend(
            SkipRecord(
                opportunity_id=r.opportunity_id,
                opportunity_title=r.opportunity_title,
                stage="hard_filter",
                reason=f"{r.check}: {r.detail}",
            )
            for r in rejections
        )
        return (
            f"Dropped {len(rejections)} deterministically. {len(survivors)} remain "
            f"({sum(1 for r in results.values() if r.verdict == 'UNKNOWN')} with at "
            f"least one unconfirmed eligibility rule)."
        )

    @tool
    async def assess_fit(opportunity_id: str) -> str:
        """Judge whether one opportunity is worth this founder's time.

        Args:
            opportunity_id: An id returned by the discovery step.
        """
        opportunity = ctx.retrieved.get(opportunity_id)
        if opportunity is None:
            return f"No opportunity {opportunity_id!r} was retrieved this run."

        ctx.budget.take_assessment_slot()

        eligibility = ctx.eligibility.get(opportunity_id)
        if eligibility is None:
            return f"{opportunity_id} has not been through the eligibility filter yet."

        from agent.subagents.assessor import assess

        try:
            assessment = await assess(
                ctx.agents.assessor,
                ctx.agents.assessor_version,
                opportunity,
                ctx.profile,
                eligibility,
                ctx.today,
            )
        except Abstention as exc:
            # An abstention is an outcome, not an error. It surfaces as
            # "needs a human look" rather than disappearing.
            from agent.models import Assessment

            assessment = Assessment(
                verdict="INSUFFICIENT_INFO",
                reason="I could not judge this one from the material available.",
                effort_hours=0.0,
                opportunity_id=opportunity_id,
            )
            ctx.report.notes.append(f"assessor abstained on {opportunity_id}: {exc.detail}")

        ctx.assessments[opportunity_id] = assessment
        ctx.report.judged = len(ctx.assessments)
        return (
            f"{opportunity_id}: {assessment.verdict} "
            f"(~{assessment.effort_hours:.1f}h). {assessment.reason}"
        )

    @tool
    def recall(question: str) -> str:
        """Has the founder answered a semantically equivalent question before?

        Args:
            question: The form question, as written on the form.
        """
        found = ctx.repo.recall(ctx.profile.founder_id, question)
        if found is None:
            return "No previous answer to this question."
        return f"Answered before: {found.answer}"

    @tool
    async def draft_and_audit(opportunity_id: str) -> str:
        """Draft the application, audit it independently, and run the ship gate.

        Args:
            opportunity_id: An opportunity already assessed APPLY or MAYBE.
        """
        opportunity = ctx.retrieved.get(opportunity_id)
        form = ctx.forms.get(opportunity_id)
        if opportunity is None or form is None:
            return f"No application form is modelled for {opportunity_id}."

        from agent.subagents.auditor import audit_draft
        from agent.subagents.drafter import draft_application

        recalled = {}
        for spec in form.fields:
            found = ctx.repo.recall(ctx.profile.founder_id, spec.label)
            if found is not None:
                found.field_id = spec.field_id
                recalled[spec.field_id] = found

        draft = await draft_application(
            ctx.agents.drafter,
            ctx.agents.drafter_version,
            draft_id=f"{ctx.report.run_id}:{opportunity_id}",
            form=form,
            opportunity=opportunity,
            profile=ctx.profile,
            kb=ctx.kb,
            recalled=recalled,
        )

        audit = await audit_draft(
            ctx.agents.auditor, ctx.agents.auditor_version, draft, ctx.kb
        )

        gate = guardrails.ship_gate(
            draft,
            ctx.kb,
            retrieved=list(ctx.retrieved.values()),
            opportunity=opportunity,
            audit=audit,
            required_field_ids={f.field_id for f in form.fields if f.required},
        )

        ctx.drafts[opportunity_id] = draft
        counts = draft.counts()
        status = (
            "ready for your review"
            if gate.passed
            else f"BLOCKED at {gate.failed_check}"
        )
        return (
            f"{opportunity_id}: {counts['KNOWN']} filled, {counts['REUSED']} reused, "
            f"{counts['GENERATED']} drafted, {counts['NEEDS_FOUNDER']} need the "
            f"founder. Draft is {status}."
        )

    @tool
    def surface_to_founder(opportunity_id: str, headline: str, summary: str) -> str:
        """The ONLY path to the human. Everything else is logged, not shown.

        The escalation policy is applied here, in Python. Calling this on
        something the policy rejects logs a silent skip instead.

        Args:
            opportunity_id: The opportunity to surface.
            headline: One line, naming what the founder controls.
            summary: Two or three sentences of plain language.
        """
        opportunity = ctx.retrieved.get(opportunity_id)
        assessment = ctx.assessments.get(opportunity_id)
        if opportunity is None or assessment is None:
            return f"{opportunity_id} has not been assessed; nothing to surface."

        decision = guardrails.escalation_decision(
            assessment=assessment,
            opportunity=opportunity,
            eligibility=ctx.eligibility[opportunity_id].verdict,
            max_application_hours=ctx.profile.max_application_hours,
            min_award=ctx.profile.min_award,
            today=ctx.today,
            already_surfaced=ctx.repo.has_surfaced(
                ctx.profile.founder_id, opportunity_id
            ),
        )

        if not decision.surface:
            ctx.report.skips.append(
                SkipRecord(
                    opportunity_id=opportunity_id,
                    opportunity_title=opportunity.title,
                    stage="escalation_policy",
                    reason=decision.reason,
                )
            )
            return f"Not surfaced — {decision.reason}"

        draft = ctx.drafts.get(opportunity_id)
        ctx.pending_inbox.append(
            InboxItem(
                item_id=f"{ctx.report.run_id}:{opportunity_id}",
                founder_id=ctx.profile.founder_id,
                opportunity_id=opportunity_id,
                kind=decision.kind,
                headline=headline,
                summary=summary,
                assessment=assessment,
                draft_id=draft.draft_id if draft else None,
            )
        )
        return f"Queued for the founder: {opportunity.title}"

    return [
        discover,
        filter_eligibility,
        assess_fit,
        recall,
        draft_and_audit,
        surface_to_founder,
    ]
