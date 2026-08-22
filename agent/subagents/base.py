"""Shared construction for the three judgment sub-agents.

Each sub-agent is a real `strands.Agent` with its own system prompt, its own
model tier, and its own failure mode — that is the architectural claim in
Section 4, so it needs to be true rather than decorative:

| Sub-agent | Tier      | Temperature | Fails by            |
|-----------|-----------|-------------|---------------------|
| Assessor  | reasoning | 0           | judging badly       |
| Drafter   | reasoning | >0          | inventing facts     |
| Auditor   | reasoning | 0           | missing an invention|

Temperature discipline (Section 9, rule 10): everything runs at 0 except the
Drafter's prose, and that output is still grounding-checked afterwards.

Nothing here calls AWS at import time. `settings()` raises when a model ID is
missing, so it is resolved inside the factory rather than at module load —
otherwise the deterministic test suite could not import this package without
a populated `.env`.

API surface used, verified against strands-agents 1.53.0 by reading the
installed source (see DECISIONS.md):
    strands.Agent(model=..., system_prompt=..., name=..., description=...)
    strands.models.BedrockModel(region_name=..., **BedrockConfig)
    Agent.structured_output_async(output_model, prompt) -> BaseModel
"""

from __future__ import annotations

from strands import Agent
from strands.models import BedrockModel

from agent.config import ModelTier, settings
from agent.prompting import Prompt, load_prompt


def build_model(tier: ModelTier, temperature: float | None = None) -> BedrockModel:
    """One Bedrock model. The model ID comes from `.env`, never from memory."""
    return BedrockModel(
        region_name=settings().region,
        model_id=tier.model_id,
        temperature=tier.temperature if temperature is None else temperature,
        max_tokens=tier.max_tokens,
    )


def build_subagent(
    *,
    name: str,
    prompt_name: str,
    description: str,
    tier: ModelTier,
    temperature: float | None = None,
) -> tuple[Agent, Prompt]:
    """Construct a sub-agent and return it with the prompt that defines it.

    The `Prompt` comes back alongside so callers can stamp `prompt_version`
    onto every field the agent produces — a receipt is only useful if it
    points at an exact revision.
    """
    prompt = load_prompt(prompt_name)
    agent = Agent(
        model=build_model(tier, temperature),
        system_prompt=prompt.text,
        name=name,
        description=description,
        # Each judgment is independent. Carrying conversation state between
        # opportunities would let one assessment contaminate the next.
        callback_handler=None,
    )
    return agent, prompt
