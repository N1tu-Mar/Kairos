from __future__ import annotations

import pytest

from agent.models import InboxItem
from api.main import app
from tests.factories import profile
from backend_method_suites.conftest import json_body

pytestmark = pytest.mark.xfail(
    reason="Authentication and authorization are not implemented yet.",
    strict=False,
)


def test_profile_replace_requires_authenticated_founder_identity(api_client):
    response = api_client.put(
        "/founders/founder_demo",
        json=json_body(profile(institution="Rutgers University")),
    )

    assert response.status_code in {401, 403}


def test_inbox_state_update_requires_access_to_that_founder_item(api_client):
    app.state.repo.save_inbox_item(
        InboxItem(
            item_id="run_1:opp_1",
            founder_id="founder_demo",
            opportunity_id="opp_1",
            kind="APPLY",
            headline="[DEMO] Fit",
            summary="Worth applying.",
        )
    )

    response = api_client.patch("/inbox/run_1:opp_1", json={"state": "dismissed"})

    assert response.status_code in {401, 403}
