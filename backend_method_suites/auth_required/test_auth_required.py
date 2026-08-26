"""The bearer-token gate in `api/main.py::require_api_token`.

The API protects every endpoint — reads included, because a profile is
citizenship, degree level and traction numbers — behind `KAIROS_API_TOKEN`.
An unset token runs the API open for the local single-founder demo, which is
why the open mode is tested here too rather than assumed.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from agent import config
from agent.models import InboxItem
from api.main import app
from backend_method_suites.conftest import json_body
from tests.factories import profile

TOKEN = "test-token-abc"


@pytest.fixture
def secured_client(monkeypatch, tmp_path):
    monkeypatch.setenv("KAIROS_DB_URL", f"sqlite:///{tmp_path}/auth.db")
    monkeypatch.setenv("KAIROS_API_TOKEN", TOKEN)
    config.settings.cache_clear()
    with TestClient(app) as client:
        yield client
    config.settings.cache_clear()


def auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {TOKEN}"}


def seed_item(client: TestClient) -> None:
    client.app.state.repo.save_inbox_item(
        InboxItem(
            item_id="run_1:opp_1",
            founder_id="founder_demo",
            opportunity_id="opp_1",
            kind="APPLY",
            headline="[DEMO] Fit",
            summary="Worth applying.",
        )
    )


def test_profile_replace_rejected_without_token(secured_client):
    response = secured_client.put(
        "/founders/founder_demo",
        json=json_body(profile(institution="Rutgers University")),
    )

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"


def test_inbox_state_update_rejected_without_token(secured_client):
    seed_item(secured_client)

    response = secured_client.patch("/inbox/run_1:opp_1", json={"state": "dismissed"})

    assert response.status_code == 401


def test_reads_are_protected_too(secured_client):
    response = secured_client.get("/founders/founder_demo")

    assert response.status_code == 401


def test_wrong_token_indistinguishable_from_missing(secured_client):
    response = secured_client.get(
        "/founders/founder_demo",
        headers={"Authorization": "Bearer wrong-token"},
    )

    assert response.status_code == 401


def test_correct_token_passes_the_gate(secured_client):
    seed_item(secured_client)

    response = secured_client.patch(
        "/inbox/run_1:opp_1", json={"state": "dismissed"}, headers=auth()
    )

    assert response.status_code == 200
    assert response.json()["state"] == "dismissed"


def test_health_stays_open_for_probes(secured_client):
    response = secured_client.get("/health")

    assert response.status_code == 200


def test_unset_token_runs_open_for_the_local_demo(api_client):
    # The default fixture sets no KAIROS_API_TOKEN: the demo profile the
    # backend seeds on startup must be readable with no credential at all.
    response = api_client.get("/founders/founder_demo")

    assert response.status_code == 200
