"""Test environment.

The deterministic layers need no configuration at all. The orchestration
tests need `settings()` to resolve, so this stamps obviously-fake model IDs
into the environment rather than reaching for AWS. Nothing here talks to
Bedrock — the sub-agents are replaced with fakes that return canned
structured output.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from agent import config


@pytest.fixture(autouse=True)
def fake_env(monkeypatch, tmp_path):
    monkeypatch.setenv("BEDROCK_MODEL_REASONING", "[DEMO]reasoning-model")
    monkeypatch.setenv("BEDROCK_MODEL_CLASSIFY", "[DEMO]classify-model")
    monkeypatch.setenv("KAIROS_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("KAIROS_DAILY_USD_CAP", "0")
    # A developer .env may set a token; these suites assume the open local mode.
    monkeypatch.delenv("KAIROS_API_TOKEN", raising=False)
    config.settings.cache_clear()
    yield
    config.settings.cache_clear()


@dataclass
class FakeMetrics:
    """The one field of `EventLoopMetrics` this codebase reads.

    `accumulated_usage` is a `Usage` TypedDict — `inputTokens` /
    `outputTokens` / `totalTokens`, verified against strands-agents 1.53.0.
    Spelled the same way here on purpose: if the real key names ever change,
    the fake stops matching and `charge_agent_result` fails loudly instead of
    charging zero.
    """

    accumulated_usage: dict


@dataclass
class FakeAgentResult:
    """Stands in for `strands.agent.AgentResult`."""

    structured_output: object | None
    metrics: FakeMetrics
    stop_reason: str = "end_turn"
    message: dict = field(default_factory=dict)
    state: object = None


class FakeAgent:
    """Stands in for a Strands Agent. Records what it was asked.

    Mirrors `Agent.invoke_async(prompt, structured_output_model=..., limits=...)`
    rather than the deprecated `structured_output_async`, because the thing
    worth faking is the `AgentResult` — that is where the token usage lives,
    and a fake that hands back only the parsed model is a fake that cannot
    catch an unbilled call.
    """

    #: Charged per call unless a response overrides it.
    default_usage = {"inputTokens": 100, "outputTokens": 50, "totalTokens": 150}

    def __init__(self, *responses, usage: dict | None = None, stop_reason: str = "end_turn"):
        self.responses = list(responses)
        self.prompts: list[str] = []
        self.limits: list[dict | None] = []
        self.usage = usage or dict(self.default_usage)
        self.stop_reason = stop_reason

    async def invoke_async(self, prompt, *, structured_output_model=None, limits=None):
        self.prompts.append(prompt)
        self.limits.append(limits)
        if not self.responses:
            raise AssertionError("FakeAgent ran out of canned responses")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        if isinstance(response, FakeAgentResult):
            return response
        return FakeAgentResult(
            structured_output=response,
            metrics=FakeMetrics(accumulated_usage=dict(self.usage)),
            stop_reason=self.stop_reason,
        )


@pytest.fixture
def fake_agent():
    return FakeAgent
