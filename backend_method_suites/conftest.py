from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from agent import config
from agent.budget import DailyLedger, RunBudget
from agent.models import Assessment
from agent.runtime import SubAgents
from api.main import app
from api.repository import SqliteRepository


@pytest.fixture(autouse=True)
def method_suite_env(monkeypatch, tmp_path):
    monkeypatch.setenv("BEDROCK_MODEL_REASONING", "[DEMO]reasoning-model")
    monkeypatch.setenv("BEDROCK_MODEL_CLASSIFY", "[DEMO]classify-model")
    monkeypatch.setenv("KAIROS_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("KAIROS_DAILY_USD_CAP", "0")
    config.settings.cache_clear()
    yield
    config.settings.cache_clear()


@pytest.fixture
def api_client(monkeypatch, tmp_path):
    monkeypatch.setenv("KAIROS_DB_URL", f"sqlite:///{tmp_path}/api.db")
    config.settings.cache_clear()
    with TestClient(app) as client:
        yield client
    config.settings.cache_clear()


@pytest.fixture
def memory_repo():
    return SqliteRepository("sqlite:///:memory:")


@pytest.fixture
def run_budget(tmp_path):
    return RunBudget(
        max_run_tokens=1_000_000,
        max_assessments=25,
        daily_usd_cap=0.0,
        ledger=DailyLedger(tmp_path / "ledger"),
    )


class FakeAgent:
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


def fake_agents(*assessments: Assessment) -> SubAgents:
    return SubAgents(
        assessor=FakeAgent(*assessments),
        assessor_version="method-suite-assessor",
        drafter=FakeAgent(),
        drafter_version="method-suite-drafter",
        auditor=FakeAgent(),
        auditor_version="method-suite-auditor",
    )


def assessment(verdict="APPLY", hours=4.0, **overrides) -> Assessment:
    base = dict(
        verdict=verdict,
        reason=f"[DEMO] {verdict} reason",
        effort_hours=hours,
    )
    base.update(overrides)
    return Assessment(**base)


def json_body(model) -> dict:
    return json.loads(model.model_dump_json())
