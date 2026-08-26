"""The contract between a sub-agent call and the wallet.

Every test here exists because of one bug: `structured_call` used the
deprecated `Agent.structured_output_async`, which hands back the parsed model
and nothing else. With no `AgentResult` there is no `metrics.accumulated_usage`,
so `RunBudget.charge_agent_result` was never called from production code and
both the run token ceiling and the daily USD cap were dead config. The suite
was green throughout, because the one test that exercised the ceiling charged
the ledger by hand.

So the first thing asserted below is the dullest: a model call costs money.
"""

from __future__ import annotations

import asyncio

import pytest
from botocore.exceptions import ClientError
from strands.types.exceptions import ModelThrottledException

from agent.budget import BudgetExceeded
from agent.models import Assessment
from agent.prompting import (
    MAX_THROTTLE_ATTEMPTS,
    Abstention,
    Throttled,
    backoff_delay,
    is_transient,
    structured_call,
)
from tests.conftest import FakeAgent, FakeAgentResult, FakeMetrics
from tests.factories import budget

pytestmark = pytest.mark.asyncio


def an_assessment() -> Assessment:
    return Assessment(verdict="APPLY", reason="[DEMO] fits", effort_hours=3.0)


async def call(agent, b, **kwargs):
    return await structured_call(
        agent, Assessment, "prompt", agent_name="assessor", budget=b, tier="reasoning", **kwargs
    )


# ── The call is billed ──────────────────────────────────────────────────────


async def test_a_successful_call_charges_the_budget():
    b = budget()
    agent = FakeAgent(an_assessment(), usage={"inputTokens": 700, "outputTokens": 300, "totalTokens": 1000})

    await call(agent, b)

    assert b.usage.input_tokens == 700
    assert b.usage.output_tokens == 300
    assert b.usage.total_tokens == 1000


async def test_a_call_that_returned_garbage_is_still_charged():
    """A ceiling that only counts the successes is not a ceiling."""
    b = budget()
    empty = FakeAgentResult(structured_output=None, metrics=FakeMetrics({"inputTokens": 500, "outputTokens": 0, "totalTokens": 500}))
    agent = FakeAgent(empty, empty, empty)

    with pytest.raises(Abstention):
        await call(agent, b)

    # Three attempts, each billed.
    assert b.usage.total_tokens == 1500


async def test_crossing_the_ceiling_raises_rather_than_returning():
    b = budget(max_run_tokens=1_000)
    agent = FakeAgent(
        an_assessment(),
        an_assessment(),
        usage={"inputTokens": 600, "outputTokens": 0, "totalTokens": 600},
    )

    await call(agent, b)
    with pytest.raises(BudgetExceeded):
        await call(agent, b)


async def test_the_per_call_limit_is_passed_down():
    """Strands' own cap bounds one runaway sub-agent; ours bounds the run."""
    b = budget(max_run_tokens=100_000)
    agent = FakeAgent(an_assessment())

    await call(agent, b)

    passed = agent.limits[0]["total_tokens"]
    assert 0 < passed < b.max_run_tokens


async def test_a_tripped_per_call_limit_abstains_without_retrying():
    b = budget()
    capped = FakeAgentResult(
        structured_output=None,
        metrics=FakeMetrics({"inputTokens": 10, "outputTokens": 0, "totalTokens": 10}),
        stop_reason="limit_total_tokens",
    )
    agent = FakeAgent(capped, an_assessment())

    with pytest.raises(Abstention):
        await call(agent, b)

    # Retrying would spend more against a limit that has already tripped.
    assert len(agent.prompts) == 1


# ── Throttling (Section 11.12) ──────────────────────────────────────────────


def throttle_error(code: str = "ThrottlingException") -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": "slow down"}}, "Converse")


async def test_transient_and_permanent_errors_are_told_apart():
    assert is_transient(ModelThrottledException("busy"))
    assert is_transient(throttle_error())
    assert is_transient(throttle_error("ServiceUnavailableException"))
    # A bad model ID or a missing permission is not something to wait out.
    assert not is_transient(throttle_error("AccessDeniedException"))
    assert not is_transient(throttle_error("ValidationException"))
    assert not is_transient(ValueError("bad json"))


async def test_backoff_grows_and_carries_jitter():
    for attempt in range(MAX_THROTTLE_ATTEMPTS):
        low, high = 2**attempt * 0.5, 2**attempt * 1.0
        assert low <= backoff_delay(attempt) <= high
    # Growth, not a flat retry: the third wait is longer than the first.
    assert backoff_delay(2) > backoff_delay(0)


async def test_throttling_backs_off_then_aborts(monkeypatch):
    slept: list[float] = []

    async def record(seconds):
        slept.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", record)
    agent = FakeAgent(*[ModelThrottledException("busy")] * MAX_THROTTLE_ATTEMPTS)

    with pytest.raises(Throttled):
        await call(agent, budget())

    assert len(agent.prompts) == MAX_THROTTLE_ATTEMPTS
    # One fewer sleep than attempts — nothing waits after the last failure.
    assert len(slept) == MAX_THROTTLE_ATTEMPTS - 1
    assert slept == sorted(slept)


async def test_a_throttle_that_clears_is_not_an_error(monkeypatch):
    async def instant(seconds):
        return None

    monkeypatch.setattr(asyncio, "sleep", instant)
    agent = FakeAgent(ModelThrottledException("busy"), an_assessment())

    result = await call(agent, budget())

    assert result.verdict == "APPLY"


async def test_a_permanent_error_is_not_retried_with_a_delay(monkeypatch):
    """AccessDenied means the credentials are wrong. Waiting does not fix it."""
    slept: list[float] = []

    async def record(seconds):
        slept.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", record)
    agent = FakeAgent(*[throttle_error("AccessDeniedException")] * 3)

    with pytest.raises(Abstention):
        await call(agent, budget())

    assert slept == []


async def test_throttling_is_not_an_abstention():
    """Distinct outcomes: one is the model declining, one is never reaching it."""
    assert not issubclass(Throttled, Abstention)
