from __future__ import annotations

from agent.models import InboxItem, RunReport
from api.main import app
from tests.factories import draft, generated, opportunity, profile
from backend_method_suites.conftest import json_body


def test_profile_put_requires_full_body_and_matching_founder_id(api_client):
    good = api_client.put(
        "/founders/founder_demo",
        json=json_body(profile(institution="Rutgers University")),
    )
    partial = api_client.put("/founders/founder_demo", json={"founder_id": "founder_demo"})
    mismatch = api_client.put(
        "/founders/founder_demo",
        json=json_body(profile(founder_id="somebody_else")),
    )

    assert good.status_code == 200
    assert good.json()["institution"] == "Rutgers University"
    assert partial.status_code == 422
    assert mismatch.status_code == 400


def test_run_opportunity_draft_and_inbox_endpoints_return_persisted_ground_truth(api_client):
    repo = app.state.repo
    repo.save_profile(profile())
    repo.save_run(RunReport(run_id="run_method", founder_id="founder_demo", scanned=3))
    repo.save_opportunity(opportunity(id="opp_method", award_max=18_000))
    repo.save_draft(
        draft(
            generated("traction", "We have 40 active users."),
            draft_id="draft_method",
            opportunity_id="opp_method",
        )
    )
    repo.save_inbox_item(
        InboxItem(
            item_id="run_method:opp_method",
            founder_id="founder_demo",
            opportunity_id="opp_method",
            kind="APPLY",
            headline="[DEMO] Fit",
            summary="Worth applying.",
        )
    )

    run = api_client.get("/founders/founder_demo/runs/run_method")
    opp = api_client.get("/opportunities/opp_method")
    drafts = api_client.get("/founders/founder_demo/drafts?opportunity_id=opp_method")
    patched = api_client.patch("/inbox/run_method:opp_method", json={"state": "opened"})

    assert run.status_code == 200
    assert run.json()["scanned"] == 3
    assert opp.status_code == 200
    assert opp.json()["award_max"] == 18000
    assert drafts.status_code == 200
    assert drafts.json()[0]["counts"]["GENERATED"] == 1
    assert patched.status_code == 200
    assert patched.json()["state"] == "opened"
