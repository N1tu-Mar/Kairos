"""`GET /me` — the seam between a verified identity and a dashboard.

Every founder-scoped route takes the founder id in its path, and until now the
dashboard got that id from `KAIROS_FOUNDER_ID`, a single server-side variable.
That works for exactly one founder. With real sign-in it is wrong in a way
that is worse than a missing feature: two people sign in as themselves and
both are shown the same founder's inbox.

This endpoint is how the frontend stops guessing. It reports what the
principal behind this request actually owns, so the dashboard renders the
founder the *session* owns rather than the one the *deployment* names.

It answers only about the caller. There is no `founder_id` in the path and no
way to ask about anyone else, which is what keeps it from becoming the
enumeration oracle that `authorize()`'s 404-not-403 rule exists to prevent.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import api.main as api_main
from api.main import app


@pytest.fixture
def client(monkeypatch, tmp_path):
    """A `TestClient` over the real app, backed by a fresh SQLite file."""
    monkeypatch.setenv("KAIROS_DB_URL", f"sqlite:///{tmp_path}/test.db")
    from agent import config

    config.settings.cache_clear()
    with TestClient(app) as client:
        yield client
    config.settings.cache_clear()


@pytest.fixture
def token_client(monkeypatch, tmp_path):
    """The same app running with a shared token, so a credential is required."""
    monkeypatch.setenv("KAIROS_DB_URL", f"sqlite:///{tmp_path}/test.db")
    monkeypatch.setenv("KAIROS_API_TOKEN", "test-token")
    monkeypatch.delenv("KAIROS_ALLOW_OPEN_API", raising=False)
    from agent import config

    config.settings.cache_clear()
    with TestClient(app) as client:
        yield client
    config.settings.cache_clear()


# ── What it reports ──────────────────────────────────────────────────────────


def test_it_reports_the_founders_this_principal_owns(client):
    """The list the dashboard needs, straight from the principal."""
    body = client.get("/me").json()

    assert body["founder_ids"] == ["founder_demo"]


def test_it_names_one_founder_to_render(client):
    """`founder_id` is the single id a one-founder dashboard should show.

    Choosing in the API rather than in the frontend keeps the choice in one
    place, and makes it deterministic: the same session picks the same
    founder on every request, which a `set` iteration order would not.
    """
    assert client.get("/me").json()["founder_id"] == "founder_demo"


def test_it_reports_write_access(client):
    """Read-only sessions exist; the dashboard has to be able to hide actions."""
    assert client.get("/me").json()["can_write"] is True


def test_it_names_how_the_caller_was_authenticated(client):
    """`method` distinguishes a real person from the shared demo credential.

    The dashboard shows a different thing to an anonymous local session than
    to somebody signed in, and this is what it reads to tell them apart.
    """
    assert client.get("/me").json()["method"] == "open"


def test_a_shared_token_session_reports_its_method(token_client):
    """Same shape, different method, so one code path handles both."""
    body = token_client.get(
        "/me", headers={"Authorization": "Bearer test-token"}
    ).json()

    assert body["method"] == "shared_token"
    assert body["founder_ids"] == ["founder_demo"]


def test_founder_ids_are_sorted(client, monkeypatch):
    """A stable order, because `frozenset` has none.

    Without this the "primary" founder could change between two requests of
    an unchanged session, which reads as the dashboard silently switching
    accounts.
    """
    repo = api_main.app.state.repo
    for founder_id in ("founder_ccc", "founder_aaa", "founder_bbb"):
        repo.link_member("shared-token", founder_id)

    body = client.get("/me").json()

    assert body["founder_ids"] == sorted(body["founder_ids"])


# ── What it refuses ──────────────────────────────────────────────────────────


def test_it_requires_a_credential(token_client):
    """Not exempt from authentication. Who you are is not public."""
    assert token_client.get("/me").status_code == 401


def test_a_wrong_credential_is_refused(token_client):
    """The same 401 as everywhere else, with no hint as to which part was wrong."""
    response = token_client.get("/me", headers={"Authorization": "Bearer nope"})

    assert response.status_code == 401


def test_it_takes_no_founder_id(client):
    """There is no way to ask about another founder — no path parameter exists.

    A `/me/{founder_id}` would answer "does this id exist" to anyone holding
    any credential, which is precisely the enumeration `owned()` refuses.
    """
    assert client.get("/me/founder_demo").status_code == 404
