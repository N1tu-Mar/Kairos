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
    """Autouse: give every test a resolvable config and a private state directory.

    Each line is load-bearing:

    - The two model IDs are stamped with `[DEMO]` so anything that leaks into
      an assertion or a fixture file is visibly not a real model.
    - `KAIROS_STATE_DIR` points at `tmp_path`, so the spend ledger and the
      lease database are per-test files and never the developer's own.
    - The daily USD cap is zeroed, so a test cannot be halted by yesterday's
      spend.
    - `KAIROS_ENV` is pinned to `local`, because these suites assume the local
      posture throughout. A `.env` set to `production` otherwise makes the app
      refuse to start under the `api_client` fixture, and the failure reads as
      a broken endpoint rather than a misread environment.
    - The run ceilings are pinned to their documented defaults, so a developer
      who lowered either one in a `.env` does not exhaust the budget mid-test.
    - `KAIROS_API_TOKEN` and `KAIROS_CREDENTIALS_FILE` are deleted, because a
      developer's `.env` may set either and these suites assume the open local
      mode. A credential file in particular turns every unauthenticated
      request into a 401.

    `settings.cache_clear()` runs on both sides: before, so this fixture's
    values are what get read; after, so a test that set its own variables
    cannot leak them into the next one.

    Note what it does *not* pin — every other `KAIROS_*` variable a `.env` may
    set (the four `KAIROS_PRICE_*` in particular) is still visible to tests. A
    test that depends on one being unset has to `monkeypatch.delenv` it
    itself; assuming an empty environment is what makes a suite pass in CI and
    fail on a developer's laptop.
    """
    monkeypatch.setenv("BEDROCK_MODEL_REASONING", "[DEMO]reasoning-model")
    monkeypatch.setenv("BEDROCK_MODEL_CLASSIFY", "[DEMO]classify-model")
    monkeypatch.setenv("KAIROS_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("KAIROS_DAILY_USD_CAP", "0")
    monkeypatch.setenv("KAIROS_ENV", "local")
    monkeypatch.setenv("KAIROS_MAX_RUN_TOKENS", "250000")
    monkeypatch.setenv("KAIROS_MAX_ASSESSMENTS", "25")
    # A developer .env may set a token or a credential file; these suites
    # assume the open local mode. Deleting them is no longer enough to get it:
    # an absent token fails closed, so open mode has to be asked for by name.
    monkeypatch.delenv("KAIROS_API_TOKEN", raising=False)
    monkeypatch.delenv("KAIROS_CREDENTIALS_FILE", raising=False)
    monkeypatch.setenv("KAIROS_ALLOW_OPEN_API", "1")
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
        """Canned responses are consumed in order.

        An `Exception` in the list is raised rather than returned, which is
        how a failure path is exercised.

        `prompts` and `limits` record every call, so a test can assert what the
        pipeline would have sent and what budget it would have imposed.
        """
        self.responses = list(responses)
        self.prompts: list[str] = []
        self.limits: list[dict | None] = []
        self.usage = usage or dict(self.default_usage)
        self.stop_reason = stop_reason

    async def invoke_async(self, prompt, *, structured_output_model=None, limits=None):
        """Return the next canned response, wrapped as an `AgentResult`.

        Running out of responses is an `AssertionError` rather than a `None`
        return: an unexpected extra model call should fail the test loudly, not
        silently produce an empty answer.
        """
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
    """The `FakeAgent` class itself, not an instance.

    Tests construct one with their own canned responses.
    """
    return FakeAgent
