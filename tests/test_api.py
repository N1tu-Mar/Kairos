"""The read surface. No model calls — these exercise stored run output."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from agent.models import Assessment, InboxItem, Rejection, RunReport, SkipRecord
from agent.scraping.agent import GENERAL_LANE, UNIVERSITY_LANE
import api.main as api_main
from api.main import app
from api.repository import SqliteRepository
from tests.factories import draft, generated, opportunity, profile


@pytest.fixture
def client(monkeypatch, tmp_path):
    """A `TestClient` over the real app, backed by a fresh SQLite file.

    Entered as a context manager so the app's `lifespan` runs — that is what
    builds the repository and the executor on `app.state`.
    """
    monkeypatch.setenv("KAIROS_DB_URL", f"sqlite:///{tmp_path}/test.db")
    from agent import config

    config.settings.cache_clear()
    with TestClient(app) as client:
        yield client
    config.settings.cache_clear()


def seed_run(repo: SqliteRepository) -> RunReport:
    """Persist a profile and one finished run with rejections and skips.

    The fixture the read endpoints are asserted against: every number these
    tests check has to come back out of storage unchanged.
    """
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


def test_past_deadline_opportunities_leave_the_founder_inbox(client):
    expired = opportunity(id="expired", deadline=date.today() - timedelta(days=1))
    current = opportunity(id="current", deadline=date.today() + timedelta(days=1))
    app.state.repo.save_opportunity(expired)
    app.state.repo.save_opportunity(current)
    for opp in (expired, current):
        app.state.repo.save_inbox_item(
            InboxItem(
                item_id=f"item_{opp.id}",
                founder_id="founder_demo",
                opportunity_id=opp.id,
                kind="APPLY",
                headline=opp.title,
                summary="[DEMO] summary",
            )
        )

    body = client.get("/founders/founder_demo/inbox").json()

    assert [item["opportunity_id"] for item in body] == ["current"]


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


# ── The write and lookup surface added for the dashboard ─────────────────────


def test_a_run_is_reachable_by_id_after_it_falls_out_of_the_recent_list(client):
    seed_run(app.state.repo)

    body = client.get("/founders/founder_demo/runs/run_1").json()

    assert body["run_id"] == "run_1"
    assert body["scanned"] == 214


def test_a_run_belonging_to_another_founder_is_404(client):
    seed_run(app.state.repo)

    assert client.get("/founders/somebody_else/runs/run_1").status_code == 404


def test_the_silent_path_is_available_for_a_specific_run(client):
    seed_run(app.state.repo)

    latest = client.get("/founders/founder_demo/runs/latest/skips").json()
    by_id = client.get("/founders/founder_demo/runs/run_1/skips").json()

    # One shape, one story. The two routes must not drift.
    assert latest == by_id
    assert by_id["rejections"][0]["check"] == "DEGREE_LEVEL"


def test_an_opportunity_carries_structured_award_and_deadline(client):
    app.state.repo.save_opportunity(opportunity(id="opp_1"))

    body = client.get("/opportunities/opp_1").json()

    assert body["id"] == "opp_1"
    # Structured fields, not a sentence to be parsed.
    assert isinstance(body["award_max"], int)
    assert body["deadline"]
    assert "[DEMO]" in body["title"]


def test_an_unknown_opportunity_is_404(client):
    assert client.get("/opportunities/nothing").status_code == 404


def scraped_candidate(scrape_id: str, title: str, scraped_at: str) -> dict:
    """One scraper candidate row as the JSON the API serves it in."""
    url = f"https://example.edu/{scrape_id}"
    return {
        "scrape_id": scrape_id,
        "title": title,
        "organization": "Example Innovation Center",
        "source_url": url,
        "award_type": "cash prize",
        "award_min": 1000,
        "award_max": 5000,
        "institution": ["Example University"],
        "degree_levels": ["undergraduate"],
        "applicant_type": ["student founder"],
        "equity_required": False,
        "deadline": "May 1, 2027",
        "deadline_iso": "2027-05-01",
        "evidence": {},
        "unknown_fields": [],
        "caveats": ["[university web search] needs review"],
        "founder_reviews": [],
        "fetch": {
            "url": url,
            "final_url": url,
            "status_code": 200,
            "fetched_at": scraped_at,
            "content_hash": scrape_id,
        },
        "scraped_at": scraped_at,
        "review_status": "NEEDS_HUMAN_REVIEW",
    }


def test_scraper_candidates_are_grouped_by_lane(client, monkeypatch, tmp_path):
    university_path = tmp_path / "university.json"
    general_path = tmp_path / "general.json"
    university_path.write_text(
        json.dumps(
            [
                scraped_candidate(
                    "university_old",
                    "Older Campus Prize",
                    "2026-08-20T00:00:00Z",
                ),
                scraped_candidate(
                    "university_new",
                    "New Campus Prize",
                    "2026-08-28T00:00:00Z",
                ),
            ]
        )
    )
    general_path.write_text(
        json.dumps(
            [
                scraped_candidate(
                    "general_1",
                    "Public Founder Grant",
                    "2026-08-27T00:00:00Z",
                )
            ]
        )
    )
    monkeypatch.setattr(
        api_main,
        "SCRAPER_CANDIDATE_LANES",
        {
            "university": replace(UNIVERSITY_LANE, output_path=university_path),
            "general": replace(GENERAL_LANE, output_path=general_path),
        },
    )

    body = client.get("/scraper/candidates?limit=1").json()

    assert set(body) == {"university", "general"}
    assert body["university"]["total"] == 2
    assert body["university"]["candidates"][0]["title"] == "New Campus Prize"
    assert body["general"]["total"] == 1
    assert body["general"]["candidates"][0]["title"] == "Public Founder Grant"


def test_missing_scraper_candidate_file_is_an_empty_lane(client, monkeypatch, tmp_path):
    monkeypatch.setattr(
        api_main,
        "SCRAPER_CANDIDATE_LANES",
        {
            "university": replace(
                UNIVERSITY_LANE, output_path=tmp_path / "not-written-yet.json"
            ),
            "general": replace(GENERAL_LANE, output_path=tmp_path / "also-missing.json"),
        },
    )

    body = client.get("/scraper/candidates?lane=university").json()

    assert set(body) == {"university"}
    assert body["university"]["total"] == 0
    assert body["university"]["candidates"] == []


def test_a_founder_can_record_what_they_did_with_an_item(client):
    seed_run(app.state.repo)

    body = client.patch("/inbox/item_1", json={"state": "applied"}).json()

    assert body["state"] == "applied"
    assert app.state.repo.get_inbox_item("item_1").state == "applied"


def test_patching_an_item_cannot_rewrite_what_the_run_decided(client):
    seed_run(app.state.repo)

    before = app.state.repo.get_inbox_item("item_1")
    client.patch("/inbox/item_1", json={"state": "dismissed"})
    after = app.state.repo.get_inbox_item("item_1")

    assert after.kind == before.kind
    assert after.headline == before.headline
    assert after.assessment == before.assessment


def test_an_invalid_inbox_state_is_rejected(client):
    seed_run(app.state.repo)

    assert client.patch("/inbox/item_1", json={"state": "approved"}).status_code == 422


def test_patching_an_unknown_item_is_404(client):
    assert client.patch("/inbox/nothing", json={"state": "opened"}).status_code == 404


def test_drafts_are_listable_without_going_through_the_inbox(client):
    repo = app.state.repo
    repo.save_profile(profile())
    repo.save_draft(
        draft(
            generated("traction", "[DEMO] 40 students used the pilot."),
            draft_id="draft_1",
            opportunity_id="opp_1",
        )
    )
    repo.save_draft(
        draft(
            generated("traction", "[DEMO] 40 students used the pilot."),
            draft_id="draft_2",
            opportunity_id="opp_2",
        )
    )

    everything = client.get("/founders/founder_demo/drafts").json()
    one = client.get("/founders/founder_demo/drafts?opportunity_id=opp_2").json()

    assert len(everything) == 2
    assert len(one) == 1
    assert one[0]["draft"]["draft_id"] == "draft_2"
    # Counts are computed in Python, never by a model.
    assert one[0]["counts"]["GENERATED"] == 1


def test_a_profile_can_be_replaced(client):
    updated = profile(institution="Rutgers University", has_faculty_advisor=True)

    body = client.put("/founders/founder_demo", json=json.loads(updated.model_dump_json())).json()

    assert body["institution"] == "Rutgers University"
    assert app.state.repo.get_profile("founder_demo").has_faculty_advisor is True


def test_a_profile_write_must_agree_with_the_path(client):
    mismatched = profile(founder_id="someone_else")

    response = client.put(
        "/founders/founder_demo", json=json.loads(mismatched.model_dump_json())
    )

    assert response.status_code == 400
    assert "does not match" in response.json()["detail"]


def test_a_partial_profile_write_is_rejected_rather_than_merged(client):
    # These fields are what the eligibility filter compares against. A
    # half-applied update is the one outcome worth ruling out entirely.
    response = client.put("/founders/founder_demo", json={"founder_id": "founder_demo"})

    assert response.status_code == 422
