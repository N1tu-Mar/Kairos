"""Assessor — APPLY / MAYBE / SKIP / INSUFFICIENT_INFO.

Sees the opportunity, the founder profile, and the structured output of the
deterministic filter. It does not re-decide eligibility; that already
happened in Python, and letting a model revisit it would put the load-bearing
check back inside the blast radius of a prompt injection.
"""

from __future__ import annotations

from datetime import date

from agent.config import settings
from agent.guardrails import days_until
from agent.models import Assessment, EligibilityResult, FounderProfile, Opportunity
from agent.prompting import structured_call
from agent.sanitize import wrap_untrusted
from agent.subagents.base import build_subagent

DESCRIPTION = (
    "Judges whether one funding opportunity is worth a specific founder's time. "
    "Returns APPLY, MAYBE, SKIP, or INSUFFICIENT_INFO with a founder-facing reason."
)


def build() -> tuple:
    return build_subagent(
        name="assessor",
        prompt_name="assessor",
        description=DESCRIPTION,
        tier=settings().reasoning,
    )


def render_context(
    opportunity: Opportunity,
    profile: FounderProfile,
    eligibility: EligibilityResult,
    today: date,
) -> str:
    """Build the Assessor's user message.

    Every number in here is computed in Python and handed over as a fact
    (Section 9, rule 8). The model is never asked to subtract two dates.
    """
    remaining = days_until(opportunity.deadline, today)
    deadline_line = (
        "rolling, no fixed deadline"
        if opportunity.rolling and opportunity.deadline is None
        else f"{opportunity.deadline} ({remaining} days from today)"
        if opportunity.deadline
        else "not stated on the source page"
    )
    award_line = (
        f"${opportunity.award_min:,} to ${opportunity.award_max:,}"
        if opportunity.award_min is not None and opportunity.award_max is not None
        else f"${opportunity.best_award:,}"
        if opportunity.best_award is not None
        else "not stated on the source page"
    )

    unknowns = (
        ", ".join(eligibility.unknown_checks)
        if eligibility.unknown_checks
        else "none — every stated rule was checked and matched"
    )
    blockers = (
        "\n".join(
            f"  - {b.check}: {b.detail} (the founder could: {b.remedy})"
            for b in eligibility.resolvable_blockers
        )
        or "  none"
    )

    criteria = "\n".join(f"  - {c.text}" for c in opportunity.criteria) or "  none extracted"

    return f"""## Opportunity

Title: {opportunity.title}
Funder: {opportunity.funder}
Award: {award_line}
Deadline: {deadline_line}
Source: {opportunity.source_url}

Eligibility criteria extracted verbatim from the source:
{criteria}

## Deterministic filter results (already computed, do not re-decide)

Eligibility verdict: {eligibility.verdict}
Checks the source page did not answer: {unknowns}
Blockers the founder could remove themselves:
{blockers}

## Founder

Degree level: {profile.degree_level}
Institution: {profile.institution}
Stage: {profile.stage}
Team size: {profile.team_size}
Entity: {profile.entity_type}
Faculty advisor: {"yes" if profile.has_faculty_advisor else "no"}
Traction (structured, numbers only): {profile.traction or "none recorded"}
Wants: ${profile.min_award:,} to ${profile.max_award:,}, non-dilutive only
Time they will spend on one application: {profile.max_application_hours} hours

## Source description

{wrap_untrusted(opportunity.description_excerpt, opportunity.source)}

Return your assessment.
"""


async def assess(
    agent,
    prompt_version: str,
    opportunity: Opportunity,
    profile: FounderProfile,
    eligibility: EligibilityResult,
    today: date,
    *,
    budget,
) -> Assessment:
    """Run one assessment. Raises `Abstention` if it cannot produce valid output."""
    assessment = await structured_call(
        agent,
        Assessment,
        render_context(opportunity, profile, eligibility, today),
        agent_name="assessor",
        budget=budget,
        tier="reasoning",
    )
    # Stamped by us, not by the model — a model that can write its own
    # receipt can write a false one.
    assessment.opportunity_id = opportunity.id
    assessment.model_id = settings().reasoning.model_id
    assessment.prompt_version = prompt_version
    return assessment
