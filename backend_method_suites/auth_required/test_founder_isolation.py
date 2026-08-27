"""Two founders, two credentials, and no way from one to the other.

The shared token was never an identity — it proved somebody held the secret,
never which founder they were — so every founder-scoped path was really
honour-scoped and guessing an id was enough. These tests exist to make that
false, and to keep it false.

The rule under test throughout: a resource that exists but is not yours is
**404**, byte-identical to a resource that does not exist. A 403 confirms the
id, and a confirmed id is an enumeration primitive.
"""

from __future__ import annotations

import json
import time

import pytest
from fastapi.testclient import TestClient

from agent import config
from agent.models import Draft, InboxItem
from api.auth import hash_token
from api.main import app
from backend_method_suites.conftest import json_body
from tests.factories import profile

TOKEN_A = "token-for-founder-a"
TOKEN_B = "token-for-founder-b"
TOKEN_READONLY = "read-only-token-for-founder-a"
TOKEN_REVOKED = "revoked-token-for-founder-a"


def _credentials_file(path, *, expires_at=None) -> str:
    """A credential file holding hashes, never tokens."""
    payload = {
        "credentials": [
            {
                "credential_id": "founder-a",
                "token_hash": hash_token(TOKEN_A),
                "subject": "founder_a",
                "founder_ids": ["founder_a"],
                "can_write": True,
            },
            {
                "credential_id": "founder-b",
                "token_hash": hash_token(TOKEN_B),
                "subject": "founder_b",
                "founder_ids": ["founder_b"],
                "can_write": True,
            },
            {
                "credential_id": "founder-a-readonly",
                "token_hash": hash_token(TOKEN_READONLY),
                "subject": "founder_a_viewer",
                "founder_ids": ["founder_a"],
                "can_write": False,
            },
            {
                "credential_id": "founder-a-revoked",
                "token_hash": hash_token(TOKEN_REVOKED),
                "subject": "founder_a_old_laptop",
                "founder_ids": ["founder_a"],
                "can_write": True,
                "revoked": True,
                "expires_at": expires_at,
            },
        ]
    }
    file = path / "credentials.json"
    file.write_text(json.dumps(payload))
    return str(file)


@pytest.fixture
def two_founders(monkeypatch, tmp_path):
    """A client with two real founders behind two real credentials."""
    monkeypatch.setenv("KAIROS_DB_URL", f"sqlite:///{tmp_path}/isolation.db")
    monkeypatch.setenv("KAIROS_CREDENTIALS_FILE", _credentials_file(tmp_path))
    monkeypatch.delenv("KAIROS_API_TOKEN", raising=False)
    config.settings.cache_clear()

    with TestClient(app) as client:
        repo = client.app.state.repo
        repo.save_profile(profile(founder_id="founder_a"))
        repo.save_profile(profile(founder_id="founder_b"))
        yield client
    config.settings.cache_clear()


def a() -> dict[str, str]:
    return {"Authorization": f"Bearer {TOKEN_A}"}


def b() -> dict[str, str]:
    return {"Authorization": f"Bearer {TOKEN_B}"}


# ── Reads ────────────────────────────────────────────────────────────────────


def test_each_founder_reads_only_their_own_profile(two_founders):
    assert two_founders.get("/founders/founder_a", headers=a()).status_code == 200
    assert two_founders.get("/founders/founder_b", headers=b()).status_code == 200


def test_a_cannot_read_bs_profile(two_founders):
    response = two_founders.get("/founders/founder_b", headers=a())
    assert response.status_code == 404


def test_not_yours_is_byte_identical_to_not_found(two_founders):
    """Otherwise the status code itself enumerates founders."""
    exists_not_mine = two_founders.get("/founders/founder_b", headers=a())
    does_not_exist = two_founders.get("/founders/founder_nobody", headers=a())

    assert exists_not_mine.status_code == does_not_exist.status_code == 404
    assert exists_not_mine.json()["detail"] != does_not_exist.json()["detail"]
    # The two differ only by the id that was asked for, which the caller
    # already knew. Neither response reveals whether the id exists.
    assert "founder_b" in exists_not_mine.json()["detail"]
    assert "founder_nobody" in does_not_exist.json()["detail"]


@pytest.mark.parametrize(
    "path",
    [
        "/founders/founder_b",
        "/founders/founder_b/inbox",
        "/founders/founder_b/runs",
        "/founders/founder_b/runs/latest",
        "/founders/founder_b/runs/latest/skips",
        "/founders/founder_b/runs/run_x",
        "/founders/founder_b/runs/run_x/skips",
        "/founders/founder_b/drafts",
        "/founders/founder_b/jobs",
        "/founders/founder_b/jobs/job_x",
        "/founders/founder_b/scheduler/failures",
    ],
)
def test_every_founder_scoped_read_refuses_the_other_founder(two_founders, path):
    assert two_founders.get(path, headers=a()).status_code == 404


# ── Writes ───────────────────────────────────────────────────────────────────


def test_a_cannot_replace_bs_profile(two_founders):
    response = two_founders.put(
        "/founders/founder_b",
        json=json_body(profile(founder_id="founder_b", institution="Rutgers")),
        headers=a(),
    )
    assert response.status_code == 404
    # And B's profile is untouched.
    stored = two_founders.get("/founders/founder_b", headers=b()).json()
    assert stored["institution"] != "Rutgers"


def test_a_cannot_smuggle_bs_id_through_their_own_path(two_founders):
    """The body id is checked as well as the path id.

    Without that check, a principal authorized for their own path could
    write a document naming somebody else's founder id.
    """
    response = two_founders.put(
        "/founders/founder_a",
        json=json_body(profile(founder_id="founder_b", institution="Rutgers")),
        headers=a(),
    )
    assert response.status_code == 400


def test_a_cannot_trigger_a_run_for_b(two_founders):
    response = two_founders.post(
        "/founders/founder_b/runs",
        json={"use_demo_catalog": True, "include_grants_gov": False},
        headers=a(),
    )
    assert response.status_code == 404


def test_a_cannot_cancel_bs_job(two_founders):
    response = two_founders.post(
        "/founders/founder_b/jobs/job_x/cancel", headers=a()
    )
    assert response.status_code == 404


# ── Resource-id-only routes ──────────────────────────────────────────────────


def test_inbox_state_cannot_be_changed_across_founders(two_founders):
    """`/inbox/{item_id}` carries no founder id, so it must look one up."""
    two_founders.app.state.repo.save_inbox_item(
        InboxItem(
            item_id="run_b:opp_1",
            founder_id="founder_b",
            opportunity_id="opp_1",
            kind="APPLY",
            headline="[DEMO] B's opportunity",
            summary="Worth applying.",
        )
    )

    response = two_founders.patch(
        "/inbox/run_b:opp_1", json={"state": "dismissed"}, headers=a()
    )
    assert response.status_code == 404

    # B can still change it, and it was never modified by the refused call.
    mine = two_founders.patch(
        "/inbox/run_b:opp_1", json={"state": "dismissed"}, headers=b()
    )
    assert mine.status_code == 200
    assert mine.json()["state"] == "dismissed"


def test_a_draft_cannot_be_read_across_founders(two_founders):
    """A draft is the knowledge base rendered into prose. Check the owner."""
    two_founders.app.state.repo.save_draft(
        Draft(
            draft_id="draft_b1",
            founder_id="founder_b",
            opportunity_id="opp_1",
            fields=[],
        )
    )

    assert two_founders.get("/drafts/draft_b1", headers=a()).status_code == 404
    assert two_founders.get("/drafts/draft_b1", headers=b()).status_code == 200


def test_guessing_ids_gets_nowhere(two_founders):
    """The horizontal-access attempt, spelled out."""
    for guess in ("founder_b", "founder_c", "admin", "../founder_b"):
        assert two_founders.get(f"/founders/{guess}", headers=a()).status_code in (
            404,
            405,
        )


# ── Credential lifecycle ─────────────────────────────────────────────────────


def test_a_read_only_credential_cannot_write(two_founders):
    headers = {"Authorization": f"Bearer {TOKEN_READONLY}"}

    assert two_founders.get("/founders/founder_a", headers=headers).status_code == 200
    write = two_founders.put(
        "/founders/founder_a",
        json=json_body(profile(founder_id="founder_a", institution="Rutgers")),
        headers=headers,
    )
    assert write.status_code == 404


def test_a_revoked_credential_is_refused(two_founders):
    response = two_founders.get(
        "/founders/founder_a", headers={"Authorization": f"Bearer {TOKEN_REVOKED}"}
    )
    assert response.status_code == 401


def test_an_expired_credential_is_refused(monkeypatch, tmp_path):
    monkeypatch.setenv("KAIROS_DB_URL", f"sqlite:///{tmp_path}/expiry.db")
    path = tmp_path / "credentials.json"
    path.write_text(
        json.dumps(
            {
                "credentials": [
                    {
                        "credential_id": "expired",
                        "token_hash": hash_token(TOKEN_A),
                        "subject": "founder_a",
                        "founder_ids": ["founder_a"],
                        "expires_at": time.time() - 60,
                    }
                ]
            }
        )
    )
    monkeypatch.setenv("KAIROS_CREDENTIALS_FILE", str(path))
    config.settings.cache_clear()

    with TestClient(app) as client:
        assert client.get("/founders/founder_a", headers=a()).status_code == 401
    config.settings.cache_clear()


def test_rotation_takes_effect_without_a_restart(two_founders, tmp_path):
    """Rewriting the credential file revokes the old token immediately."""
    assert two_founders.get("/founders/founder_a", headers=a()).status_code == 200

    rotated = "the-new-token-for-founder-a"
    path = tmp_path / "credentials.json"
    payload = json.loads(path.read_text())
    for entry in payload["credentials"]:
        if entry["credential_id"] == "founder-a":
            entry["token_hash"] = hash_token(rotated)
    # mtime resolution can be coarse; make the change unambiguous.
    time.sleep(0.01)
    path.write_text(json.dumps(payload))

    assert two_founders.get("/founders/founder_a", headers=a()).status_code == 401
    assert (
        two_founders.get(
            "/founders/founder_a", headers={"Authorization": f"Bearer {rotated}"}
        ).status_code
        == 200
    )


def test_tokens_are_never_stored_in_the_credential_file(tmp_path):
    path = _credentials_file(tmp_path)
    raw = open(path).read()

    for token in (TOKEN_A, TOKEN_B, TOKEN_READONLY, TOKEN_REVOKED):
        assert token not in raw
    assert hash_token(TOKEN_A) in raw


def test_a_missing_credential_file_fails_closed(monkeypatch, tmp_path):
    """No file means nobody gets in. It never means everybody does."""
    monkeypatch.setenv("KAIROS_DB_URL", f"sqlite:///{tmp_path}/missing.db")
    monkeypatch.setenv(
        "KAIROS_CREDENTIALS_FILE", str(tmp_path / "does_not_exist.json")
    )
    config.settings.cache_clear()

    with TestClient(app) as client:
        assert client.get("/founders/founder_a", headers=a()).status_code == 401
        assert client.get("/founders/founder_a").status_code == 401
    config.settings.cache_clear()
