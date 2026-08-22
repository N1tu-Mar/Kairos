"""The read surface. No model calls — these exercise stored run output."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from agent.models import Assessment, InboxItem, Rejection, RunReport, SkipRecord
from api.main import app
from api.repository import SqliteRepository
from tests.factories import profile


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("KAIROS_DB_URL", f"sqlite:///{tmp_path}/test.db")
    from agent import config

    config.settings.cache_clear()
    with TestClient(app) as client:
        yield client
    config.settings.cache_clear()


def seed_run(repo: SqliteRepository) -> RunReport:
    repo.save_profile(profile())
    report = RunReport(
        run_id="run_1",
        founder_id="founder_demo",
        scanned=214,
        filtered_out=198,
        judged=16,
        surfaced=3,
        rejections=[
            Rejection(
                opportunity_id="drop_1",
                opportunity_title="[DEMO] Doctoral Award",
                check="DEGREE_LEVEL",
                detail="open to phd only",
                founder_value="undergrad",
                required_value="phd",
            )
        ],
        skips=[
            SkipRecord(
                opportunity_id="skip_1",
                opportunity_title="[DEMO] Tiny Grant",
                stage="escalation_policy",
                reason="award $500 is below the founder's floor $2,000",
            )
        ],
    )
    repo.save_run(report)
    repo.save_inbox_item(
        InboxItem(
            item_id="item_1",
            founder_id="founder_demo",
            opportunity_id="opp_1",
            kind="APPLY",
            headline="[DEMO] Campus Innovation Fund · up to $10,000",
            summary="Your pilot clears this fund's stated requirement.",
            assessment=Assessment(verdict="APPLY", reason="[DEMO]", effort_hours=5.0),
        )
    )
    repo.save_inbox_item(
        InboxItem(
            item_id="item_2",
            founder_id="founder_demo",
            opportunity_id="opp_2",
            kind="MAYBE",
            headline="[DEMO] Also found",
            summary="Lower value per hour.",
            passive=True,
        )
    )
    return report


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_demo_profile_is_seeded_on_startup(client):
    response = client.get("/founders/founder_demo")
    assert response.status_code == 200
    assert response.json()["institution"]


def test_unknown_founder_is_404(client):
    assert client.get("/founders/nobody").status_code == 404


def test_run_counters_are_served_verbatim(client):
    seed_run(app.state.repo)

    body = client.get("/founders/founder_demo/runs/latest").json()

    assert (body["scanned"], body["filtered_out"], body["judged"], body["surfaced"]) == (
        214,
        198,
        16,
        3,
    )


def test_the_silent_path_is_one_request_away(client):
    seed_run(app.state.repo)

    body = client.get("/founders/founder_demo/runs/latest/skips").json()

    assert body["headline"] == "Scanned 214. Discarded 198. Judged 16. Surfaced 3."
    assert body["rejections"][0]["check"] == "DEGREE_LEVEL"
    assert "below the founder's floor" in body["skips"][0]["reason"]


def test_passive_items_can_be_excluded_from_the_inbox(client):
    seed_run(app.state.repo)

    everything = client.get("/founders/founder_demo/inbox").json()
    announced = client.get("/founders/founder_demo/inbox?include_passive=false").json()

    assert len(everything) == 2
    assert len(announced) == 1
    assert announced[0]["kind"] == "APPLY"


def test_no_runs_yet_is_404_not_an_empty_success(client):
    assert client.get("/founders/founder_demo/runs/latest").status_code == 404


def test_cors_allows_a_vercel_preview_origin(client):
    response = client.get(
        "/health", headers={"Origin": "https://kairos-git-abc123.vercel.app"}
    )
    assert (
        response.headers.get("access-control-allow-origin")
        == "https://kairos-git-abc123.vercel.app"
    )
