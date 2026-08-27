"""Every path, query and body parameter, at its boundaries.

Two properties, both of which have to hold for a public HTTP surface:

1.  **A malformed request is a 4xx, never a 5xx.** A 500 says the server
    broke; a 422 says the caller sent something wrong. Confusing the two
    sends an operator hunting a bug that does not exist, and hides the ones
    that do.
2.  **A limit is bounded.** `?limit=100000000` on a list endpoint is a
    denial-of-service request written in query-string form, and `?limit=-1`
    is a value SQL will happily interpret as "no limit at all".

The frontend sends `limit=50` on the runs page, so the ceilings here are set
above what it asks for — bounds that break the existing dashboard would be a
different bug, not a fix.
"""

from __future__ import annotations

import pytest

from tests.factories import profile


@pytest.fixture
def client(api_client):
    api_client.put(
        "/founders/founder_demo",
        json=profile(founder_id="founder_demo").model_dump(mode="json"),
    )
    return api_client


LIST_ENDPOINTS = [
    "/founders/founder_demo/runs",
    "/founders/founder_demo/jobs",
    "/founders/founder_demo/inbox",
    "/founders/founder_demo/scheduler/failures",
]


# ── limits: the lower bound ─────────────────────────────────────────────────


@pytest.mark.parametrize("path", LIST_ENDPOINTS)
@pytest.mark.parametrize("limit", [0, -1, -100])
def test_a_non_positive_limit_is_rejected(client, path, limit):
    """Zero asks for nothing and negative asks for something SQL reads as
    unbounded. Neither is a question worth answering."""
    response = client.get(path, params={"limit": limit})

    assert response.status_code == 422


@pytest.mark.parametrize("path", LIST_ENDPOINTS)
def test_the_smallest_useful_limit_is_accepted(client, path):
    assert client.get(path, params={"limit": 1}).status_code == 200


# ── limits: the upper bound ─────────────────────────────────────────────────


@pytest.mark.parametrize("path", LIST_ENDPOINTS)
@pytest.mark.parametrize("limit", [1_001, 100_000, 2**31])
def test_an_unreasonably_large_limit_is_rejected(client, path, limit):
    response = client.get(path, params={"limit": limit})

    assert response.status_code == 422


@pytest.mark.parametrize("path", LIST_ENDPOINTS)
def test_the_ceiling_itself_is_accepted(client, path):
    assert client.get(path, params={"limit": 1000}).status_code == 200


@pytest.mark.parametrize("path", LIST_ENDPOINTS)
def test_the_limit_the_dashboard_actually_sends_is_accepted(client, path):
    """frontend/src/app/runs/page.tsx asks for 50. A bound that breaks the
    existing dashboard is a regression wearing a fix's clothes."""
    assert client.get(path, params={"limit": 50}).status_code == 200


# ── limits: malformed rather than out of range ──────────────────────────────


@pytest.mark.parametrize("path", LIST_ENDPOINTS)
@pytest.mark.parametrize("limit", ["abc", "1.5", "", "1;DROP TABLE runs", "NaN"])
def test_a_non_integer_limit_is_a_422_not_a_500(client, path, limit):
    response = client.get(path, params={"limit": limit})

    assert response.status_code == 422


# ── boolean query parameters ────────────────────────────────────────────────


@pytest.mark.parametrize("value", ["true", "false", "1", "0"])
def test_include_passive_accepts_the_usual_boolean_spellings(client, value):
    assert (
        client.get(
            "/founders/founder_demo/inbox", params={"include_passive": value}
        ).status_code
        == 200
    )


def test_a_non_boolean_include_passive_is_rejected(client):
    response = client.get(
        "/founders/founder_demo/inbox", params={"include_passive": "maybe"}
    )

    assert response.status_code == 422


# ── identifiers ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "path",
    [
        "/founders/{id}",
        "/founders/{id}/runs",
        "/founders/{id}/inbox",
        "/founders/{id}/drafts",
    ],
)
def test_an_absurdly_long_founder_id_is_rejected_not_queried(client, path):
    """An unbounded id is an unbounded key in every index it reaches."""
    response = client.get(path.format(id="f" * 5_000))

    assert response.status_code == 422


def test_a_whitespace_only_founder_id_is_rejected(client):
    response = client.get("/founders/%20%20%20")

    assert response.status_code in (404, 422)
    assert response.status_code != 500


@pytest.mark.parametrize(
    "identifier",
    ["../../etc/passwd", "founder demo", "'; DROP TABLE profiles--", "%2e%2e%2f"],
)
def test_a_hostile_identifier_is_a_4xx_never_a_500(client, identifier):
    response = client.get(f"/founders/{identifier}")

    assert 400 <= response.status_code < 500


def test_a_nul_byte_never_reaches_the_application(client):
    """A NUL cannot be expressed in a URL, so this is rejected before the
    server sees it. Asserted rather than assumed, because "the layer below
    handles it" is exactly the belief that is wrong often enough to test.

    The exception type is looked up on the client rather than imported, so
    this keeps working across the httpx -> httpx2 test-client change."""
    with pytest.raises(Exception) as exc:
        client.get("/founders/founder\x00demo")

    assert "InvalidURL" in type(exc.value).__name__


@pytest.mark.parametrize(
    "path",
    [
        "/opportunities/{id}",
        "/drafts/{id}",
    ],
)
def test_an_oversized_resource_id_is_rejected(client, path):
    response = client.get(path.format(id="x" * 5_000))

    assert response.status_code == 422


def test_an_oversized_run_id_is_rejected(client):
    response = client.get(f"/founders/founder_demo/runs/{'r' * 5_000}")

    assert response.status_code == 422


def test_an_oversized_inbox_item_id_is_rejected(client):
    response = client.patch(
        f"/inbox/{'i' * 5_000}", json={"state": "opened"}
    )

    assert response.status_code == 422


# ── enum-like inputs ────────────────────────────────────────────────────────


def test_an_unknown_inbox_state_is_rejected(client):
    response = client.patch("/inbox/whatever", json={"state": "archived"})

    assert response.status_code == 422


@pytest.mark.parametrize("state", ["new", "opened", "dismissed", "applied"])
def test_every_declared_inbox_state_is_accepted_by_validation(client, state):
    """The item does not exist, so the answer is 404 — but a 404 proves the
    body validated, which is what this asserts. A 422 here would mean a
    legitimate state was being rejected."""
    response = client.patch("/inbox/does-not-exist", json={"state": state})

    assert response.status_code == 404


def test_an_unknown_run_source_is_rejected_rather_than_silently_coerced(client):
    """`source` is recorded on the job and on failure-log entries, so
    'did last night's *scheduled* run fail?' depends on it. Quietly rewriting
    an unrecognised value to 'unknown' loses the caller's mistake."""
    response = client.post(
        "/founders/founder_demo/runs",
        json={"source": "definitely-not-a-source"},
    )

    assert response.status_code == 422


@pytest.mark.parametrize("source", ["manual", "scheduled", "unknown"])
def test_the_declared_run_sources_are_accepted(client, source):
    response = client.post(
        "/founders/founder_demo/runs",
        json={"source": source, "idempotency_key": f"key-{source}"},
    )

    assert response.status_code in (200, 202, 409)


def test_an_oversized_idempotency_key_is_rejected(client):
    response = client.post(
        "/founders/founder_demo/runs",
        json={"idempotency_key": "k" * 5_000},
    )

    assert response.status_code == 422


# ── bodies ──────────────────────────────────────────────────────────────────


def test_a_body_founder_id_that_disagrees_with_the_path_is_rejected(client):
    body = profile(founder_id="someone_else").model_dump(mode="json")

    response = client.put("/founders/founder_demo", json=body)

    assert response.status_code == 400
    assert "does not match" in response.json()["detail"]


def test_a_malformed_json_body_is_a_422_not_a_500(client):
    response = client.put(
        "/founders/founder_demo",
        content=b"{not json",
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 422


def test_a_missing_body_is_a_422_not_a_500(client):
    assert client.put("/founders/founder_demo").status_code == 422


def test_an_unknown_field_in_a_profile_body_is_rejected(client):
    body = profile(founder_id="founder_demo").model_dump(mode="json")
    body["surprise"] = "value"

    response = client.put("/founders/founder_demo", json=body)

    assert response.status_code == 422


def test_a_profile_with_an_impossible_team_size_is_rejected(client):
    body = profile(founder_id="founder_demo").model_dump(mode="json")
    body["team_size"] = 0

    assert client.put("/founders/founder_demo", json=body).status_code == 422


def test_a_profile_with_a_non_positive_hour_ceiling_is_rejected(client):
    body = profile(founder_id="founder_demo").model_dump(mode="json")
    body["max_application_hours"] = 0

    assert client.put("/founders/founder_demo", json=body).status_code == 422


def test_an_unknown_field_in_a_run_trigger_is_rejected(client):
    response = client.post(
        "/founders/founder_demo/runs",
        json={"use_demo_catalog": True, "surprise": "value"},
    )

    assert response.status_code == 422


def test_a_non_boolean_run_trigger_flag_is_rejected(client):
    response = client.post(
        "/founders/founder_demo/runs", json={"use_demo_catalog": "yes please"}
    )

    assert response.status_code == 422


# ── nothing above returns a 500 ─────────────────────────────────────────────


def test_no_malformed_request_in_this_suite_produced_a_500(client):
    """A sweep, so a newly added route inherits the property by default."""
    hostile = [
        ("GET", "/founders/founder_demo/runs", {"params": {"limit": -5}}),
        ("GET", "/founders/founder_demo/jobs", {"params": {"limit": "abc"}}),
        ("GET", "/founders/" + "f" * 900, {}),
        ("GET", "/drafts/" + "d" * 900, {}),
        ("GET", "/opportunities/" + "o" * 900, {}),
        ("PATCH", "/inbox/x", {"json": {"state": "nope"}}),
        ("PUT", "/founders/founder_demo", {"json": {"founder_id": "founder_demo"}}),
        ("POST", "/founders/founder_demo/runs", {"json": {"source": 12}}),
    ]

    for method, path, kwargs in hostile:
        response = client.request(method, path, **kwargs)
        assert response.status_code < 500, f"{method} {path} -> {response.status_code}"
