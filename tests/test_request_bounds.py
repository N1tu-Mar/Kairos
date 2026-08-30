"""A request body is bounded before it becomes memory, and again before it becomes a row.

`api/main.py` bounds every *parameter* that reaches the database — id length,
list limits — with the reasoning written out beside them. What it did not
bound is the **body**, and `PUT /founders/{id}` takes the largest object in
the system: `knowledge_base` is a list with no length limit whose every entry
carries `text` with no length limit either.

Uvicorn imposes no body ceiling of its own, so the request was buffered in
full before Pydantic ever looked at it. One `PUT` carrying a few hundred
megabytes of knowledge chunks is a memory spike; a slow stream of them is the
same denial-of-service the `?limit=10000000` ceiling already exists to
prevent, written in a different place.

Two ceilings, on purpose, because they fail in different places:

*   **`Content-Length`** is refused by middleware before the body is read at
    all — the cheap early exit, and the only one that helps when the body is
    enormous.
*   **Field bounds** are Pydantic's, and they hold for a body that is small
    enough to read but still absurd as a profile — 40,000 knowledge chunks of
    100 bytes each. They also hold on every other path that builds a
    `FounderProfile`, including the seed loader, which no HTTP middleware
    sees.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from agent import config
from agent.models import FounderProfile, KnowledgeChunk
from api.main import MAX_BODY_BYTES, app


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("KAIROS_DB_URL", f"sqlite:///{tmp_path}/bounds.db")
    config.settings.cache_clear()
    with TestClient(app) as test_client:
        yield test_client
    config.settings.cache_clear()


# ── The early exit ───────────────────────────────────────────────────────────


def test_an_oversized_body_is_refused_before_it_is_parsed(client):
    """413, not 422. The body was never read, so there is nothing to validate."""
    oversized = b"x" * (MAX_BODY_BYTES + 1)

    response = client.put(
        "/founders/founder_demo",
        content=oversized,
        headers={"content-type": "application/json"},
    )

    assert response.status_code == 413


def test_the_refusal_names_the_limit_but_not_the_deployment(client):
    """An operator can act on it; a stranger learns nothing about the host."""
    response = client.put(
        "/founders/founder_demo",
        content=b"x" * (MAX_BODY_BYTES + 1),
        headers={"content-type": "application/json"},
    )
    body = response.text

    assert str(MAX_BODY_BYTES) in body
    assert "sqlite" not in body.lower()
    assert "/data" not in body


def test_an_ordinary_body_is_unaffected(client):
    """The ceiling must be invisible to every real caller."""
    response = client.get("/founders/founder_demo")

    assert response.status_code == 200


def test_the_health_probe_is_not_broken_by_the_middleware(client):
    """The bound runs on every request; it must not disturb the probes."""
    assert client.get("/health").status_code == 200


# ── The field bounds ─────────────────────────────────────────────────────────


def _profile(**overrides) -> dict:
    base = {
        "founder_id": "founder_demo",
        "degree_level": "undergrad",
        "institution": "Georgia Institute of Technology",
        "citizenship": "us_citizen",
        "entity_type": "none",
        "team_size": 2,
        "stage": "mvp",
        "funding_range": [2000, 50000],
        "equity_ok": False,
        "has_faculty_advisor": False,
        "max_application_hours": 8,
    }
    base.update(overrides)
    return base


def test_a_knowledge_base_longer_than_the_cap_is_rejected():
    """The list that grows without bound is the one that has to be capped."""
    chunks = [
        {"chunk_id": f"c{i}", "text": "a fact", "source": "intake"}
        for i in range(FounderProfile.MAX_KNOWLEDGE_CHUNKS + 1)
    ]

    with pytest.raises(ValidationError):
        FounderProfile.model_validate(_profile(knowledge_base=chunks))


def test_a_knowledge_chunk_longer_than_the_cap_is_rejected():
    with pytest.raises(ValidationError):
        KnowledgeChunk(
            chunk_id="c1",
            text="a" * (KnowledgeChunk.MAX_TEXT + 1),
            source="intake",
        )


def test_an_absurd_institution_is_rejected():
    with pytest.raises(ValidationError):
        FounderProfile.model_validate(_profile(institution="a" * 10_000))


def test_a_realistic_profile_still_validates():
    """Every bound must be generous enough that no real founder trips it."""
    profile = FounderProfile.model_validate(
        _profile(
            knowledge_base=[
                {
                    "chunk_id": f"c{i}",
                    "text": "We interviewed 12 users at Rutgers. " * 20,
                    "source": "intake_call_2026_08",
                }
                for i in range(50)
            ],
            geographies=["NJ", "NY", "PA"],
            traction={"users": 40, "interviews": 12, "pilots": 1},
        )
    )

    assert len(profile.knowledge_base) == 50


def test_the_seeded_demo_founder_still_validates():
    """The bounds cannot be tighter than the data the app seeds itself with."""
    from agent.config import REPO_ROOT

    raw = (REPO_ROOT / "data" / "demo_founder.json").read_text()

    profile = FounderProfile.model_validate_json(raw)

    assert profile.founder_id == "founder_demo"


def test_a_body_under_the_http_ceiling_but_absurd_as_a_profile_is_still_refused(client):
    """The two ceilings cover different things, and this is the gap between.

    Small enough for the middleware to admit, far too large to be a profile.
    Pydantic is what catches it, which is why the bound lives on the model and
    not only in the HTTP layer.
    """
    chunks = [
        {"chunk_id": f"c{i}", "text": "x" * 40, "source": "s"}
        for i in range(FounderProfile.MAX_KNOWLEDGE_CHUNKS + 100)
    ]
    payload = json.dumps(_profile(knowledge_base=chunks))
    assert len(payload) < MAX_BODY_BYTES, "this test is meaningless if the body is oversized"

    response = client.put("/founders/founder_demo", content=payload,
                          headers={"content-type": "application/json"})

    assert response.status_code == 422
