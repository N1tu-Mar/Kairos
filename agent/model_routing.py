"""Single source of truth for which Bedrock model performs each role.

The registry contains role names and tier policy, never credentials.  Model IDs
are resolved from :mod:`agent.config` at call time so tests and deployments use
the same routing code without contacting AWS during import.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal

from agent.config import Settings, settings

ModelTierName = Literal["reasoning", "classify", "deterministic"]
ModelRole = Literal[
    "intake_interviewer",
    "eligibility_equivalence",
    "application_field_mapper",
    "opportunity_assessor",
    "application_drafter",
    "draft_auditor",
    "interactive_scout",
    "scheduled_orchestration",
]
TemperaturePolicy = Literal["zero", "drafting"]


@dataclass(frozen=True)
class RouteSpec:
    tier: ModelTierName
    temperature: TemperaturePolicy = "zero"


@dataclass(frozen=True)
class ResolvedRoute:
    role: ModelRole
    tier: ModelTierName
    model_id: str
    temperature: float
    max_tokens: int


ROLE_ROUTES = MappingProxyType(
    {
        "intake_interviewer": RouteSpec("reasoning"),
        "eligibility_equivalence": RouteSpec("classify"),
        "application_field_mapper": RouteSpec("classify"),
        "opportunity_assessor": RouteSpec("classify"),
        "application_drafter": RouteSpec("classify", "drafting"),
        "draft_auditor": RouteSpec("reasoning"),
        "interactive_scout": RouteSpec("classify"),
        "scheduled_orchestration": RouteSpec("deterministic"),
    }
)


def route_for(role: ModelRole, config: Settings | None = None) -> ResolvedRoute:
    """Resolve one role to its deployed model and sampling settings."""
    resolved = config or settings()
    spec = ROLE_ROUTES[role]
    if spec.tier == "deterministic":
        return ResolvedRoute(role, spec.tier, "", 0.0, 0)
    tier = resolved.reasoning if spec.tier == "reasoning" else resolved.classify
    temperature = (
        resolved.drafting_temperature if spec.temperature == "drafting" else 0.0
    )
    return ResolvedRoute(role, spec.tier, tier.model_id, temperature, tier.max_tokens)


def usage_snapshot(budget) -> tuple[int, int, int, float]:
    """Capture cumulative usage before a call so its own cost can be stamped."""
    usage = budget.usage
    return (
        usage.input_tokens,
        usage.output_tokens,
        usage.total_tokens,
        usage.usd_estimate,
    )


def call_receipt(
    role: ModelRole,
    prompt_version: str,
    before: tuple[int, int, int, float],
    budget,
):
    """Build server-owned metadata for one operation, including charged retries."""
    from agent.models import ModelCallReceipt

    route = route_for(role)
    usage = budget.usage
    return ModelCallReceipt(
        role=role,
        tier=route.tier,
        model_id=route.model_id,
        prompt_version=prompt_version,
        input_tokens=usage.input_tokens - before[0],
        output_tokens=usage.output_tokens - before[1],
        total_tokens=usage.total_tokens - before[2],
        usd_estimate=usage.usd_estimate - before[3],
    )
