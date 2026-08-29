"""Audit events record *that* something happened, never *what was in it*.

The line these tests defend: an audit log is security telemetry, not a second
copy of the founder's data. A leaked audit log should embarrass nobody.
"""

from __future__ import annotations

import logging

import pytest
from fastapi.testclient import TestClient

from agent import config
from agent.models import InboxItem
from api.main import app
from backend_method_suites.conftest import json_body
from tests.factories import profile

TOKEN = "audit-suite-token"


@pytest.fixture
def audited(monkeypatch, tmp_path, caplog):
    """A client whose audit events are captured rather than only logged."""
    monkeypatch.setenv("KAIROS_DB_URL", f"sqlite:///{tmp_path}/audit.db")
    monkeypatch.setenv("KAIROS_API_TOKEN", TOKEN)
    config.settings.cache_clear()
    caplog.set_level(logging.INFO, logger="kairos.audit")
    with TestClient(app) as client:
        yield client
    config.settings.cache_clear()


def auth() -> dict[str, str]:
    """An `Authorization` header for the configured test token."""
    return {"Authorization": f"Bearer {TOKEN}"}


def events(caplog, action: str) -> list[logging.LogRecord]:
    """The audit events recorded so far, in order."""
    return [
        record
        for record in caplog.records
        if record.name == "kairos.audit" and getattr(record, "action", None) == action
    ]


def test_profile_write_is_audited(audited, caplog):
    audited.put(
        "/founders/founder_demo",
        json=json_body(profile(institution="Rutgers University")),
        headers=auth(),
    )

    recorded = events(caplog, "profile.write")
    assert len(recorded) == 1
    assert recorded[0].resource == "founder_demo"
    assert recorded[0].actor == "shared-token"


def test_the_profile_itself_never_reaches_the_audit_log(audited, caplog):
    """Citizenship, institution and traction are not security telemetry."""
    audited.put(
        "/founders/founder_demo",
        json=json_body(
            profile(institution="Rutgers University", citizenship="f1_visa")
        ),
        headers=auth(),
    )

    text = " ".join(
        f"{record.getMessage()} {record.__dict__}" for record in caplog.records
    )
    assert "Rutgers University" not in text
    assert "f1_visa" not in text


def test_run_trigger_is_audited(audited, caplog):
    audited.post(
        "/founders/founder_demo/runs",
        json={
            "use_demo_catalog": True,
            "include_grants_gov": False,
            "source": "manual",
        },
        headers=auth(),
    )

    recorded = events(caplog, "run.trigger")
    assert len(recorded) == 1
    assert recorded[0].founder_id == "founder_demo"
    assert recorded[0].source == "manual"


def test_inbox_state_change_is_audited(audited, caplog):
    audited.app.state.repo.save_inbox_item(
        InboxItem(
            item_id="run_1:opp_1",
            founder_id="founder_demo",
            opportunity_id="opp_1",
            kind="APPLY",
            headline="[DEMO] Fit",
            summary="Worth applying.",
        )
    )

    audited.patch(
        "/inbox/run_1:opp_1", json={"state": "dismissed"}, headers=auth()
    )

    recorded = events(caplog, "inbox.state_change")
    assert len(recorded) == 1
    assert recorded[0].resource == "run_1:opp_1"
    assert recorded[0].new_state == "dismissed"


def test_a_rejected_credential_is_audited(audited, caplog):
    audited.get("/founders/founder_demo", headers={"Authorization": "Bearer wrong"})

    recorded = events(caplog, "auth.rejected")
    assert len(recorded) == 1
    assert recorded[0].outcome == "denied"


def test_the_bearer_token_never_reaches_the_audit_log(audited, caplog):
    """Not on success, and not on the rejection either."""
    audited.get("/founders/founder_demo", headers=auth())
    audited.get(
        "/founders/founder_demo",
        headers={"Authorization": "Bearer a-wrong-token-value"},
    )

    text = " ".join(
        f"{record.getMessage()} {record.__dict__}" for record in caplog.records
    )
    assert TOKEN not in text
    assert "a-wrong-token-value" not in text


def test_reads_are_not_audited(audited, caplog):
    """Auditing every GET buries the writes that matter in noise."""
    audited.get("/founders/founder_demo", headers=auth())
    audited.get("/founders/founder_demo/runs", headers=auth())

    assert [r for r in caplog.records if r.name == "kairos.audit"] == []
