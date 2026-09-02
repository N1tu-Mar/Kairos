"""The scheduler token can start a scheduled run and cannot do anything else."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from agent import config
from agent.models import InboxItem
from api.auth import (
    SCOPE_RUN_TRIGGER,
    SCHEDULER_SCOPES,
    AuthError,
    SchedulerTokenAuthenticator,
    build_authenticator,
)
from api.main import app
from tests.factories import profile

SCHEDULER = "scheduler-secret-for-tests"
USER_TOKEN = "founder-shared-token"
FOUNDER = "founder_demo"


@pytest.fixture
def scheduler_client(monkeypatch, tmp_path):
    """API with both a human shared token and a scheduler token."""
    monkeypatch.setenv("KAIROS_DB_URL", f"sqlite:///{tmp_path}/sched.db")
    monkeypatch.setenv("KAIROS_API_TOKEN", USER_TOKEN)
    monkeypatch.setenv("KAIROS_SCHEDULER_TOKEN", SCHEDULER)
    monkeypatch.setenv("KAIROS_SCHEDULER_FOUNDER_ID", FOUNDER)
    monkeypatch.setenv("KAIROS_ALLOW_OPEN_API", "0")
    config.settings.cache_clear()
    with TestClient(app) as client:
        yield client
    config.settings.cache_clear()


def sched_header() -> dict[str, str]:
    return {"Authorization": f"Bearer {SCHEDULER}"}


def user_header() -> dict[str, str]:
    return {"Authorization": f"Bearer {USER_TOKEN}"}


def test_scheduler_principal_has_only_run_trigger():
    auth = SchedulerTokenAuthenticator(SCHEDULER, FOUNDER)
    principal = auth.authenticate(f"Bearer {SCHEDULER}")

    assert principal.is_scheduler
    assert principal.scopes == SCHEDULER_SCOPES
    assert principal.has_scope(SCOPE_RUN_TRIGGER)
    assert principal.owns(FOUNDER)


def test_a_wrong_scheduler_token_is_not_a_principal():
    auth = SchedulerTokenAuthenticator(SCHEDULER, FOUNDER)
    assert auth.try_authenticate("Bearer other") is None
    with pytest.raises(AuthError):
        auth.authenticate("Bearer other")


def test_build_authenticator_prefers_a_matching_scheduler_token(monkeypatch):
    monkeypatch.setenv("KAIROS_API_TOKEN", USER_TOKEN)
    monkeypatch.setenv("KAIROS_SCHEDULER_TOKEN", SCHEDULER)
    monkeypatch.setenv("KAIROS_ALLOW_OPEN_API", "0")
    config.settings.cache_clear()
    auth = build_authenticator(config.settings())

    principal = auth.authenticate(f"Bearer {SCHEDULER}")
    assert principal.is_scheduler

    human = auth.authenticate(f"Bearer {USER_TOKEN}")
    assert human.method == "shared_token"
    assert not human.is_scheduler


def test_scheduler_can_trigger_a_scheduled_run(scheduler_client):
    response = scheduler_client.post(
        f"/founders/{FOUNDER}/runs",
        json={
            "use_demo_catalog": False,
            "include_grants_gov": False,
            "source": "manual",
            "idempotency_key": "evt-1",
        },
        headers=sched_header(),
    )

    assert response.status_code == 202
    assert response.json()["source"] == "scheduled"


def test_scheduler_cannot_request_the_demo_catalog(scheduler_client):
    response = scheduler_client.post(
        f"/founders/{FOUNDER}/runs",
        json={"use_demo_catalog": True, "include_grants_gov": False},
        headers=sched_header(),
    )

    assert response.status_code == 400
    assert "demo catalog" in response.json()["detail"]


def test_scheduler_cannot_read_a_profile(scheduler_client):
    response = scheduler_client.get(f"/founders/{FOUNDER}", headers=sched_header())
    assert response.status_code == 404
    assert response.json()["detail"] == f"no profile for {FOUNDER}"


def test_scheduler_cannot_write_a_profile(scheduler_client):
    body = profile().model_dump(mode="json")
    response = scheduler_client.put(
        f"/founders/{FOUNDER}", json=body, headers=sched_header()
    )
    assert response.status_code == 404


def test_scheduler_cannot_read_inbox_or_drafts(scheduler_client):
    assert (
        scheduler_client.get(
            f"/founders/{FOUNDER}/inbox", headers=sched_header()
        ).status_code
        == 404
    )
    assert (
        scheduler_client.get(
            f"/founders/{FOUNDER}/drafts", headers=sched_header()
        ).status_code
        == 404
    )


def test_scheduler_cannot_answer_eligibility(scheduler_client):
    response = scheduler_client.put(
        f"/founders/{FOUNDER}/eligibility-questions/eq_1/answer",
        json={"answer": "yes"},
        headers=sched_header(),
    )
    assert response.status_code == 404


def test_scheduler_cannot_cancel_a_job(scheduler_client):
    response = scheduler_client.post(
        f"/founders/{FOUNDER}/jobs/job_abc/cancel",
        headers=sched_header(),
    )
    assert response.status_code == 404


def test_scheduler_cannot_patch_inbox(scheduler_client):
    scheduler_client.app.state.repo.save_inbox_item(
        InboxItem(
            item_id="run_1:opp_1",
            founder_id=FOUNDER,
            opportunity_id="opp_1",
            kind="APPLY",
            headline="[DEMO] Fit",
            summary="Worth applying.",
        )
    )
    response = scheduler_client.patch(
        "/inbox/run_1:opp_1",
        json={"state": "dismissed"},
        headers=sched_header(),
    )
    assert response.status_code == 404


def test_a_human_token_still_reads_the_profile(scheduler_client):
    response = scheduler_client.get(f"/founders/{FOUNDER}", headers=user_header())
    assert response.status_code == 200
