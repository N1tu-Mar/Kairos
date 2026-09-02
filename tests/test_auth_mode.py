"""KAIROS_AUTH_MODE is a closed set, and production cannot be a laptop."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from agent import config
from agent.config import ConfigError, validate_runtime_posture
from api.main import app


def _settings(monkeypatch, **env: str):
    for key in (
        "KAIROS_AUTH_MODE",
        "KAIROS_SUPABASE_ISSUER",
        "KAIROS_ENABLE_BROWSER",
        "KAIROS_ENV",
        "KAIROS_API_TOKEN",
        "KAIROS_SCHEDULER_TOKEN",
    ):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    config.settings.cache_clear()
    return config.settings()


def test_auth_mode_defaults_to_local_shared(monkeypatch):
    assert _settings(monkeypatch).auth_mode == "local_shared"


def test_auth_mode_accepts_supabase(monkeypatch):
    assert _settings(monkeypatch, KAIROS_AUTH_MODE="supabase").auth_mode == "supabase"


def test_an_unknown_auth_mode_is_a_startup_error(monkeypatch):
    monkeypatch.setenv("KAIROS_AUTH_MODE", "open_sesame")
    config.settings.cache_clear()
    with pytest.raises(ConfigError, match="local_shared"):
        config.settings()


def test_production_refuses_local_shared_mode(monkeypatch):
    cfg = _settings(monkeypatch, KAIROS_ENV="production")
    with pytest.raises(ConfigError, match="supabase"):
        validate_runtime_posture(cfg)


def test_production_refuses_playwright(monkeypatch):
    cfg = _settings(
        monkeypatch,
        KAIROS_ENV="production",
        KAIROS_AUTH_MODE="supabase",
        KAIROS_SUPABASE_ISSUER="https://abcdefghijklm.supabase.co/auth/v1",
        KAIROS_ENABLE_BROWSER="true",
    )
    with pytest.raises(ConfigError, match="ENABLE_BROWSER"):
        validate_runtime_posture(cfg)


def test_supabase_mode_without_an_issuer_is_unusable(monkeypatch):
    cfg = _settings(monkeypatch, KAIROS_AUTH_MODE="supabase")
    with pytest.raises(ConfigError, match="KAIROS_SUPABASE_ISSUER"):
        validate_runtime_posture(cfg)


def test_a_complete_production_posture_is_accepted(monkeypatch):
    cfg = _settings(
        monkeypatch,
        KAIROS_ENV="production",
        KAIROS_AUTH_MODE="supabase",
        KAIROS_SUPABASE_ISSUER="https://abcdefghijklm.supabase.co/auth/v1",
        KAIROS_SCHEDULER_TOKEN="sched-secret",
    )
    validate_runtime_posture(cfg)


def test_the_api_refuses_to_boot_production_in_local_mode(monkeypatch, tmp_path):
    """Lifespan must fail closed, not come up and hope /ready is watched."""
    monkeypatch.setenv("KAIROS_DB_URL", f"sqlite:///{tmp_path}/boot.db")
    monkeypatch.setenv("KAIROS_ENV", "production")
    monkeypatch.setenv("KAIROS_AUTH_MODE", "local_shared")
    monkeypatch.setenv("KAIROS_API_TOKEN", "not-enough")
    config.settings.cache_clear()

    with pytest.raises(ConfigError, match="supabase"):
        with TestClient(app):
            pass
    config.settings.cache_clear()
