"""Shared fixtures for the method suites.

These suites exercise one backend method at a time against real
persistence — a temporary SQLite file per test — with the model layer
faked. The environment fixture mirrors `tests/conftest.py`; see its
docstring for why it is duplicated rather than imported.
"""

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

# One fake, imported rather than copied: a second copy drifts, and a drifted
# fake is a suite that has quietly stopped testing the real call shape.
from tests.conftest import FakeAgent  # noqa: E402


@pytest.fixture(autouse=True)
def method_suite_env(monkeypatch, tmp_path):
    """Autouse: the same environment as `tests/conftest.py`'s `fake_env`.

    Duplicated rather than imported because pytest does not inherit an
    autouse fixture across two independent rootdirs. If one of these is
    edited, edit both — a divergence here means the two suites are testing
    under different configurations without saying so.
    """
    monkeypatch.setenv("BEDROCK_MODEL_REASONING", "[DEMO]reasoning-model")
    monkeypatch.setenv("BEDROCK_MODEL_CLASSIFY", "[DEMO]classify-model")
    monkeypatch.setenv("KAIROS_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("KAIROS_DAILY_USD_CAP", "0")
    # A developer .env may set a token; these suites assume the open local mode.
    monkeypatch.delenv("KAIROS_API_TOKEN", raising=False)
    config.settings.cache_clear()
    yield
    config.settings.cache_clear()


@pytest.fixture
def api_client(monkeypatch, tmp_path):
    """A `TestClient` over the real app, against a fresh SQLite file per test.

    Entered as a context manager, so the app's `lifespan` actually runs —
    that is what builds the repository, the run lock and the executor, and
    what makes the orphan-recovery path reachable. A bare `TestClient(app)`
    without the `with` would skip all of it.
    """
    monkeypatch.setenv("KAIROS_DB_URL", f"sqlite:///{tmp_path}/api.db")
    config.settings.cache_clear()
    with TestClient(app) as client:
        yield client
    config.settings.cache_clear()


@pytest.fixture
def memory_repo():
    """An in-memory repository. Nothing survives the test, which is the point."""
    return SqliteRepository("sqlite:///:memory:")


@pytest.fixture
def run_budget(tmp_path):
    """A budget with caps high enough not to interfere, and a ledger under `tmp_path`.

    `daily_usd_cap=0.0` disables the dollar cap rather than setting a small
    one — a test that trips a cap it did not mean to set is a test that fails
    for the wrong reason.
    """
    return RunBudget(
        max_run_tokens=1_000_000,
        max_assessments=25,
        daily_usd_cap=0.0,
        ledger=DailyLedger(tmp_path / "ledger"),
    )




def fake_agents(*assessments: Assessment) -> SubAgents:
    """A `SubAgents` bundle whose Assessor returns the given assessments in order.

    The Drafter and Auditor are given no responses at all, so calling either
    raises out of `FakeAgent` — these suites cover the assessment path, and
    an unexpected drafting call should fail loudly rather than quietly
    returning nothing.
    """
    return SubAgents(
        assessor=FakeAgent(*assessments),
        assessor_version="method-suite-assessor",
        drafter=FakeAgent(),
        drafter_version="method-suite-drafter",
        auditor=FakeAgent(),
        auditor_version="method-suite-auditor",
    )


def assessment(verdict="APPLY", hours=4.0, **overrides) -> Assessment:
    """An `Assessment` with a `[DEMO]` reason. Override any field by keyword."""
    base = dict(
        verdict=verdict,
        reason=f"[DEMO] {verdict} reason",
        effort_hours=hours,
    )
    base.update(overrides)
    return Assessment(**base)


def json_body(model) -> dict:
    """Round-trip a Pydantic model through its JSON form, for request bodies.

    Going through `model_dump_json` rather than `model_dump` matters: it
    applies the same serialisation the API uses, so dates and datetimes are
    strings exactly as they would be on the wire.
    """
    return json.loads(model.model_dump_json())
