"""State that lives for exactly one run.

The heavy objects — opportunities, eligibility results, drafts — stay here,
in Python. Tools return compact summaries to the orchestrating model and keep
the real data in this context. That is not an optimisation detail: every byte
of an opportunity description that reaches the model is untrusted text from
the open web, and the less of it that has to round-trip through a context
window, the smaller the attack surface and the cheaper the run.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone

from agent.budget import RunBudget
from agent.models import (
    ApplicationForm,
    Assessment,
    Draft,
    EligibilityResult,
    FounderProfile,
    InboxItem,
    KnowledgeBase,
    Opportunity,
    RunReport,
)


@dataclass
class SubAgents:
    """The three judgment agents, each with the prompt version that defines it."""

    assessor: object
    assessor_version: str
    drafter: object
    drafter_version: str
    auditor: object
    auditor_version: str

    @classmethod
    def build(cls) -> SubAgents:
        """Construct all three. Requires a populated `.env`."""
        from agent.subagents import assessor, auditor, drafter

        assessor_agent, assessor_prompt = assessor.build()
        drafter_agent, drafter_prompt = drafter.build()
        auditor_agent, auditor_prompt = auditor.build()
        return cls(
            assessor=assessor_agent,
            assessor_version=assessor_prompt.version,
            drafter=drafter_agent,
            drafter_version=drafter_prompt.version,
            auditor=auditor_agent,
            auditor_version=auditor_prompt.version,
        )


@dataclass
class RunContext:
    """Everything one run needs, and nothing that outlives it."""

    profile: FounderProfile
    kb: KnowledgeBase
    budget: RunBudget
    report: RunReport
    repo: object
    agents: SubAgents | None = None
    today: date = field(default_factory=lambda: datetime.now(timezone.utc).date())

    retrieved: dict[str, Opportunity] = field(default_factory=dict)
    eligibility: dict[str, EligibilityResult] = field(default_factory=dict)
    assessments: dict[str, Assessment] = field(default_factory=dict)
    drafts: dict[str, Draft] = field(default_factory=dict)
    forms: dict[str, ApplicationForm] = field(default_factory=dict)

    #: Held in memory until the run completes. A halted run surfaces nothing,
    #: so nothing may be persisted before we know the run finished.
    pending_inbox: list[InboxItem] = field(default_factory=list)
