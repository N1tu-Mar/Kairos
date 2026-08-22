"""Test environment.

The deterministic layers need no configuration at all. The orchestration
tests need `settings()` to resolve, so this stamps obviously-fake model IDs
into the environment rather than reaching for AWS. Nothing here talks to
Bedrock — the sub-agents are replaced with fakes that return canned
structured output.
"""

from __future__ import annotations

import pytest

from agent import config


@pytest.fixture(autouse=True)
def fake_env(monkeypatch, tmp_path):
    monkeypatch.setenv("BEDROCK_MODEL_REASONING", "[DEMO]reasoning-model")
    monkeypatch.setenv("BEDROCK_MODEL_CLASSIFY", "[DEMO]classify-model")
    monkeypatch.setenv("PROVISION_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("PROVISION_DAILY_USD_CAP", "0")
    config.settings.cache_clear()
    yield
    config.settings.cache_clear()


class FakeAgent:
    """Stands in for a Strands Agent. Records what it was asked."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.prompts: list[str] = []

    async def structured_output_async(self, output_model, prompt):
        self.prompts.append(prompt)
        if not self.responses:
            raise AssertionError("FakeAgent ran out of canned responses")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


@pytest.fixture
def fake_agent():
    return FakeAgent
