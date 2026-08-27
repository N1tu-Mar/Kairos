"""Liveness and readiness are different questions, answered differently.

`/health` says the process is up. `/ready` says it can actually serve — and
it must detect a dead database, an unwritable state directory, and a
production deployment that is misconfigured, all without invoking a model and
without describing the deployment to an unauthenticated caller.
"""

from __future__ import annotations

import pytest

from agent import config


def test_health_is_dependency_free(api_client):
    response = api_client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_health_stays_ok_when_the_database_is_gone(api_client):
    """Liveness must not restart a healthy container over a storage hiccup."""
    from api.main import app

    class DeadRepo:
        def get_profile(self, founder_id):
            raise RuntimeError("disk gone")

    original = app.state.repo
    app.state.repo = DeadRepo()
    try:
        assert api_client.get("/health").status_code == 200
    finally:
        app.state.repo = original


def test_ready_is_ok_in_local_mode(api_client):
    body = api_client.get("/ready").json()
    assert body["status"] == "ready"
    assert body["checks"]["database"] == "ok"
    assert body["checks"]["state_storage"] == "ok"
    assert body["checks"]["configuration"] == "ok"


def test_ready_detects_an_unavailable_database(api_client):
    from api.main import app

    class DeadRepo:
        def get_profile(self, founder_id):
            raise RuntimeError("disk gone")

    original = app.state.repo
    app.state.repo = DeadRepo()
    try:
        response = api_client.get("/ready")
        assert response.status_code == 503
        assert response.json()["checks"]["database"] == "unavailable"
    finally:
        app.state.repo = original


def test_ready_detects_an_unwritable_state_directory(api_client, monkeypatch, tmp_path):
    blocker = tmp_path / "not_a_directory"
    blocker.write_text("i am a file")
    monkeypatch.setenv("KAIROS_STATE_DIR", str(blocker))
    config.settings.cache_clear()

    response = api_client.get("/ready")
    assert response.status_code == 503
    assert response.json()["checks"]["state_storage"] == "unwritable"


def test_ready_fails_production_without_a_token(api_client, monkeypatch):
    monkeypatch.setenv("KAIROS_ENV", "production")
    monkeypatch.delenv("KAIROS_API_TOKEN", raising=False)
    config.settings.cache_clear()

    response = api_client.get("/ready")
    assert response.status_code == 503
    assert response.json()["checks"]["authentication"] == "missing"


def test_ready_flags_an_unenforceable_spend_cap_in_production(api_client, monkeypatch):
    """Zero prices with a dollar cap set means the cap can never trip."""
    monkeypatch.setenv("KAIROS_ENV", "production")
    monkeypatch.setenv("KAIROS_API_TOKEN", "a-real-token")
    monkeypatch.setenv("KAIROS_DAILY_USD_CAP", "3.0")
    config.settings.cache_clear()

    body = api_client.get("/ready").json()
    assert body["checks"]["spend_cap"] == "unenforceable"


def test_ready_passes_production_when_prices_are_configured(api_client, monkeypatch):
    monkeypatch.setenv("KAIROS_ENV", "production")
    monkeypatch.setenv("KAIROS_API_TOKEN", "a-real-token")
    monkeypatch.setenv("KAIROS_DAILY_USD_CAP", "3.0")
    monkeypatch.setenv("KAIROS_PRICE_REASONING_OUT_PER_MTOK", "15.0")
    monkeypatch.setenv("KAIROS_PRICE_CLASSIFY_OUT_PER_MTOK", "4.0")
    config.settings.cache_clear()

    body = api_client.get("/ready").json()
    assert body["checks"]["spend_cap"] == "ok"
    assert body["checks"]["authentication"] == "ok"


def test_local_mode_does_not_demand_production_settings(api_client, monkeypatch):
    """Open API, zero prices — the documented demo posture, not a fault."""
    monkeypatch.setenv("KAIROS_DAILY_USD_CAP", "3.0")
    config.settings.cache_clear()

    body = api_client.get("/ready").json()
    assert body["status"] == "ready"
    assert "authentication" not in body["checks"]
    assert "spend_cap" not in body["checks"]


def test_readiness_never_leaks_configuration(api_client, monkeypatch):
    """It is unauthenticated. It must not describe the deployment."""
    monkeypatch.setenv("KAIROS_API_TOKEN", "super-secret-token")
    monkeypatch.setenv("BEDROCK_MODEL_REASONING", "some.private.model.id")
    config.settings.cache_clear()

    raw = api_client.get("/ready").text
    assert "super-secret-token" not in raw
    assert "some.private.model.id" not in raw
    # Nor the paths it probed.
    assert "/tmp" not in raw and "sqlite" not in raw


@pytest.mark.parametrize("path", ["/health", "/ready"])
def test_probes_stay_reachable_without_a_credential(monkeypatch, tmp_path, path):
    """A load balancer holds no credential, so both probes must be exempt."""
    from fastapi.testclient import TestClient

    from api.main import app

    monkeypatch.setenv("KAIROS_DB_URL", f"sqlite:///{tmp_path}/probe.db")
    monkeypatch.setenv("KAIROS_API_TOKEN", "a-real-token")
    config.settings.cache_clear()

    with TestClient(app) as client:
        # Every other endpoint is a 401 now.
        assert client.get("/founders/founder_demo").status_code == 401
        assert client.get(path).status_code in (200, 503)
