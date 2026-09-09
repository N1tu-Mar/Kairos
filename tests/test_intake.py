"""Deterministic intake persistence, confirmation, ownership, and completion."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from agent import config
from agent.intake import (
    IntakeConflict,
    IntakeIncomplete,
    apply_interview_memory,
    apply_model_proposals,
    confirm_proposal_batch,
    is_complete,
    missing_required,
    new_session,
    profile_from_session,
    update_claim,
    update_field,
    validate_field_value,
)
from agent.models import (
    IntakeEvidence,
    IntakeFieldState,
    IntakeKnowledgeClaim,
    IntakeSession,
    IntakeWorkingMemory,
)
from agent.subagents.intake_interviewer import (
    IntakeClaimProposal,
    IntakeInterviewResult,
    IntakeProposal,
)
from api.main import app
from tests.factories import profile


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("KAIROS_DB_URL", f"sqlite:///{tmp_path}/intake.db")
    monkeypatch.setenv("KAIROS_ALLOW_OPEN_API", "1")
    monkeypatch.delenv("KAIROS_API_TOKEN", raising=False)
    config.settings.cache_clear()
    with TestClient(app) as test_client:
        yield test_client
    config.settings.cache_clear()


def test_existing_profile_fields_start_confirmed_but_description_does_not():
    intake = new_session("founder_demo", profile())

    assert intake.fields["institution"].status == "confirmed"
    assert intake.fields["institution"].confirmed_by == "existing-profile"
    assert missing_required(intake) == ["startup_description"]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("team_size", True),
        ("equity_ok", 1),
        ("degree_level", "college"),
        ("funding_range", [50_000, 2_000]),
        ("geographies", ["US", ""]),
        ("unknown", "anything"),
    ],
)
def test_invalid_or_coerced_profile_values_are_rejected(field, value):
    with pytest.raises(ValueError):
        validate_field_value(field, value)


def test_model_proposal_is_not_complete_until_founder_confirms_it():
    intake = new_session("founder_demo", profile())
    intake.fields["startup_description"] = IntakeFieldState(
        field="startup_description",
        status="proposed",
        value="A scheduling tool for university laboratories.",
        confidence=0.99,
        evidence=[
            IntakeEvidence(source_type="message", source_id="message_1")
        ],
        proposed_at=datetime.now(timezone.utc),
    )

    assert not is_complete(intake)
    with pytest.raises(IntakeIncomplete):
        profile_from_session(intake, profile())

    confirmed = update_field(
        intake,
        field="startup_description",
        action="confirm",
        actor="founder-user",
    )
    assert is_complete(confirmed)
    assert confirmed.fields["startup_description"].confirmed_by == "founder-user"


def test_reject_removes_a_candidate_and_all_confirmation_metadata():
    intake = new_session("founder_demo", profile())
    rejected = update_field(
        intake,
        field="institution",
        action="reject",
        actor="founder-user",
    )

    assert rejected.fields["institution"].status == "missing"
    assert rejected.fields["institution"].value is None
    assert "institution" in missing_required(rejected)


def test_create_resumes_one_active_session(client):
    first = client.post("/founders/founder_demo/intake/sessions")
    second = client.post("/founders/founder_demo/intake/sessions")

    assert first.status_code == second.status_code == 200
    assert first.json()["session"]["session_id"] == second.json()["session"]["session_id"]


def test_stale_revision_cannot_overwrite_a_newer_confirmation(client):
    created = client.post("/founders/founder_demo/intake/sessions").json()
    session_id = created["session"]["session_id"]

    first = client.patch(
        f"/founders/founder_demo/intake/sessions/{session_id}/fields/startup_description",
        json={
            "action": "correct",
            "value": "A scheduling tool for shared university laboratories.",
            "expected_revision": 0,
        },
    )
    stale = client.patch(
        f"/founders/founder_demo/intake/sessions/{session_id}/fields/startup_description",
        json={
            "action": "correct",
            "value": "A stale replacement.",
            "expected_revision": 0,
        },
    )

    assert first.status_code == 200
    assert stale.status_code == 409
    loaded = client.get(
        f"/founders/founder_demo/intake/sessions/{session_id}"
    ).json()
    assert (
        loaded["session"]["fields"]["startup_description"]["value"]
        == "A scheduling tool for shared university laboratories."
    )


def test_complete_atomically_writes_only_confirmed_description(client):
    created = client.post("/founders/founder_demo/intake/sessions").json()
    session_id = created["session"]["session_id"]
    updated = client.patch(
        f"/founders/founder_demo/intake/sessions/{session_id}/fields/startup_description",
        json={
            "action": "correct",
            "value": "A founder-confirmed laboratory scheduling product.",
            "expected_revision": 0,
        },
    ).json()

    response = client.post(
        f"/founders/founder_demo/intake/sessions/{session_id}/complete",
        json={"expected_revision": updated["session"]["revision"]},
    )

    assert response.status_code == 200
    assert response.json()["knowledge_base"][-1]["text"] == (
        "A founder-confirmed laboratory scheduling product."
    )
    loaded = client.get(
        f"/founders/founder_demo/intake/sessions/{session_id}"
    ).json()
    assert loaded["session"]["status"] == "completed"
    assert loaded["ready_to_complete"] is False


def test_completion_refuses_missing_required_facts(client):
    created = client.post("/founders/founder_demo/intake/sessions").json()
    session_id = created["session"]["session_id"]

    response = client.post(
        f"/founders/founder_demo/intake/sessions/{session_id}/complete",
        json={"expected_revision": 0},
    )

    assert response.status_code == 409
    assert "startup_description" in response.json()["detail"]


def test_cross_founder_session_id_is_indistinguishable_from_missing(client):
    other = new_session("founder_other", None)
    app.state.repo.create_intake_session(other)

    response = client.get(
        f"/founders/founder_demo/intake/sessions/{other.session_id}"
    )

    assert response.status_code == 404
    assert response.json()["detail"] == (
        f"no intake session {other.session_id} for founder_demo"
    )


def test_extra_field_update_properties_are_rejected(client):
    created = client.post("/founders/founder_demo/intake/sessions").json()
    session_id = created["session"]["session_id"]

    response = client.patch(
        f"/founders/founder_demo/intake/sessions/{session_id}/fields/team_size",
        json={
            "action": "correct",
            "value": 2,
            "expected_revision": 0,
            "role": "admin",
        },
    )

    assert response.status_code == 422


def test_repository_message_idempotency_is_scoped_to_the_session(tmp_path):
    from agent.models import IntakeMessage
    from api.repository import SqliteRepository

    repo = SqliteRepository(f"sqlite:///{tmp_path}/messages.db")
    first = IntakeMessage(
        message_id="message_1",
        session_id="intake_1",
        founder_id="founder_demo",
        role="founder",
        text="Hello",
        client_message_id="client_1",
    )
    duplicate = first.model_copy(update={"message_id": "message_2", "text": "Again"})

    assert repo.save_intake_message(first) is True
    assert repo.save_intake_message(duplicate) is False
    assert repo.get_intake_message_by_client_id("intake_1", "client_1") == first


def test_session_model_rejects_mismatched_field_keys():
    with pytest.raises(ValueError):
        IntakeSession(
            session_id="intake_bad",
            founder_id="founder_demo",
            fields={"institution": IntakeFieldState(field="major")},
        )


def test_working_memory_rejects_a_claim_stored_under_the_wrong_key():
    claim = IntakeKnowledgeClaim(
        claim_id="claim_problem",
        category="problem",
        text="University labs lose time coordinating shared equipment.",
        confidence=0.9,
        evidence=[IntakeEvidence(source_type="message", source_id="message_1")],
    )

    with pytest.raises(ValueError, match="claim key"):
        IntakeWorkingMemory(claims={"claim_wrong": claim})


def test_repository_keeps_append_only_working_memory_revisions(tmp_path):
    from api.repository import SqliteRepository

    repo = SqliteRepository(f"sqlite:///{tmp_path}/working-memory.db")
    intake = new_session("founder_demo", profile())
    repo.create_intake_session(intake)

    claim = IntakeKnowledgeClaim(
        claim_id="claim_problem",
        category="problem",
        text="University labs lose time coordinating shared equipment.",
        confidence=0.9,
        evidence=[IntakeEvidence(source_type="message", source_id="message_1")],
    )
    changed = intake.model_copy(deep=True)
    changed.revision = 1
    changed.memory = IntakeWorkingMemory(
        revision=1,
        provisional_summary="The startup coordinates shared university lab equipment.",
        claims={claim.claim_id: claim},
    )

    assert repo.save_intake_session(changed, expected_revision=0) is True
    restored = repo.get_intake_session(intake.session_id)
    snapshots = repo.list_intake_memory_revisions(intake.session_id)

    assert restored is not None
    assert restored.memory.claims[claim.claim_id].text == claim.text
    assert [snapshot.revision for snapshot in snapshots] == [0, 1]
    assert snapshots[0].memory.claims == {}
    assert snapshots[1].memory.provisional_summary == (
        "The startup coordinates shared university lab equipment."
    )


def _chat_result(
    *proposals,
    message="Thanks — what stage is the startup at?",
    claims=(),
    summary="",
):
    return IntakeInterviewResult(
        assistant_message=message,
        proposals=list(proposals),
        claim_proposals=list(claims),
        working_summary=summary,
        missing_fields=["startup_description"],
        next_topic="stage",
    )


def test_interviewer_uses_existing_reasoning_model_configuration(monkeypatch):
    from agent.subagents import intake_interviewer

    captured = {}

    def fake_build_subagent(**kwargs):
        captured.update(kwargs)
        return object(), object()

    monkeypatch.setattr(intake_interviewer, "build_subagent", fake_build_subagent)
    intake_interviewer.build()

    assert captured["tier"] is config.settings().reasoning
    assert captured["tier"].model_id == "[DEMO]reasoning-model"
    assert captured["temperature"] == 0.0


def test_model_candidates_are_validated_and_never_overwrite_confirmed_facts():
    intake = new_session("founder_demo", profile())
    changed = apply_model_proposals(
        intake,
        [
            IntakeProposal(
                field="startup_description",
                value="A scheduling product for university laboratories.",
                confidence=0.94,
                evidence_source_ids=["message_1"],
            ),
            IntakeProposal(
                field="team_size",
                value=True,
                confidence=1,
                evidence_source_ids=["message_1"],
            ),
            IntakeProposal(
                field="institution",
                value="Attacker University",
                confidence=1,
                evidence_source_ids=["message_1"],
            ),
            IntakeProposal(
                field="not_a_profile_field",
                value="ignored",
                confidence=1,
                evidence_source_ids=["message_1"],
            ),
        ],
        source_id="message_1",
    )

    assert changed.fields["startup_description"].status == "proposed"
    assert changed.fields["startup_description"].confirmed_at is None
    assert changed.fields["team_size"].value == intake.fields["team_size"].value
    assert changed.fields["institution"].value == intake.fields["institution"].value
    assert "not_a_profile_field" not in changed.fields


def test_interview_memory_accepts_document_evidence_but_keeps_it_provisional():
    intake = new_session("founder_demo", profile())
    evidence = IntakeEvidence(
        source_type="document",
        source_id="chunk_slide_4",
        location="slide 4",
        excerpt="Labs lose hours coordinating shared equipment.",
    )
    changed = apply_interview_memory(
        intake,
        field_proposals=[
            IntakeProposal(
                field="startup_description",
                value="A platform for coordinating shared laboratory equipment.",
                confidence=0.95,
                evidence_source_ids=["chunk_slide_4"],
            )
        ],
        claim_proposals=[
            IntakeClaimProposal(
                category="problem",
                text="University labs lose hours coordinating shared equipment.",
                confidence=0.93,
                evidence_source_ids=["chunk_slide_4"],
            )
        ],
        provisional_summary="The startup coordinates university lab equipment.",
        source_message_id="message_1",
        valid_evidence={evidence.source_id: evidence},
    )

    batch = changed.pending_confirmation_batch
    assert batch is not None
    assert batch.field_names == ["startup_description"]
    assert len(batch.claim_ids) == 1
    assert changed.fields["startup_description"].status == "proposed"
    claim = changed.memory.claims[batch.claim_ids[0]]
    assert claim.status == "proposed"
    assert claim.evidence[0].location == "slide 4"
    assert changed.memory.confirmed_summary == ""


def test_confirming_a_batch_promotes_only_its_exact_candidates():
    intake = new_session("founder_demo", profile())
    evidence = IntakeEvidence(source_type="message", source_id="message_1")
    proposed = apply_interview_memory(
        intake,
        field_proposals=[
            IntakeProposal(
                field="startup_description",
                value="A platform for coordinating shared laboratory equipment.",
                confidence=0.95,
                evidence_source_ids=["message_1"],
            )
        ],
        claim_proposals=[
            IntakeClaimProposal(
                category="solution",
                text="The product schedules access to shared laboratory equipment.",
                confidence=0.9,
                evidence_source_ids=["message_1"],
            )
        ],
        provisional_summary="This provisional wording must not become ground truth.",
        source_message_id="message_1",
        valid_evidence={"message_1": evidence},
    )
    batch = proposed.pending_confirmation_batch
    assert batch is not None

    confirmed = confirm_proposal_batch(
        proposed, batch_id=batch.batch_id, actor="founder-user"
    )

    assert confirmed.pending_confirmation_batch is None
    assert confirmed.fields["startup_description"].status == "confirmed"
    assert confirmed.memory.claims[batch.claim_ids[0]].status == "confirmed"
    assert "This provisional wording" not in confirmed.memory.confirmed_summary
    assert "Solution: The product schedules access" in confirmed.memory.confirmed_summary
    with pytest.raises(IntakeConflict):
        confirm_proposal_batch(
            confirmed, batch_id=batch.batch_id, actor="founder-user"
        )


def test_correcting_a_claim_preserves_a_supersession_receipt():
    intake = new_session("founder_demo", profile())
    claim = IntakeKnowledgeClaim(
        claim_id="claim_old",
        category="customers",
        text="The product serves universities.",
        confidence=0.8,
        evidence=[IntakeEvidence(source_type="message", source_id="message_1")],
    )
    intake.memory.claims[claim.claim_id] = claim

    corrected = update_claim(
        intake,
        claim_id=claim.claim_id,
        action="correct",
        actor="founder-user",
        source_id="web-action-1",
        text="The initial customers are university laboratory managers.",
    )

    old = corrected.memory.claims[claim.claim_id]
    replacement = corrected.memory.claims[old.superseded_by_claim_id]
    assert old.status == "superseded"
    assert replacement.status == "confirmed"
    assert replacement.supersedes_claim_id == old.claim_id
    assert replacement.evidence[0].source_type == "founder_edit"


def test_profile_promotes_confirmed_claims_and_excludes_provisional_claims():
    intake = new_session("founder_demo", profile())
    intake = update_field(
        intake,
        field="startup_description",
        action="correct",
        actor="founder-user",
        value="A scheduling platform for shared university laboratories.",
    )
    now = datetime.now(timezone.utc)
    confirmed = IntakeKnowledgeClaim(
        claim_id="claim_confirmed",
        category="customers",
        text="The initial users are university laboratory managers.",
        status="confirmed",
        confidence=1.0,
        evidence=[IntakeEvidence(source_type="message", source_id="message_1")],
        confirmed_at=now,
        confirmed_by="founder-user",
    )
    provisional = IntakeKnowledgeClaim(
        claim_id="claim_provisional",
        category="traction",
        text="The startup has one million customers.",
        confidence=0.2,
        evidence=[IntakeEvidence(source_type="message", source_id="message_2")],
    )
    intake.memory.claims = {
        confirmed.claim_id: confirmed,
        provisional.claim_id: provisional,
    }

    stored = profile_from_session(intake, profile())
    texts = [chunk.text for chunk in stored.knowledge_base]

    assert confirmed.text in texts
    assert provisional.text not in texts
    assert confirmed.text in stored.memory_summary
    assert provisional.text not in stored.memory_summary


def test_chat_turn_is_persistent_idempotent_and_requires_confirmation(client):
    calls = 0

    async def fake_interviewer(session, messages, documents):
        nonlocal calls
        calls += 1
        founder_message = messages[-1]
        return _chat_result(
            IntakeProposal(
                field="startup_description",
                value="A concise platform for managing shared research equipment.",
                confidence=0.97,
                evidence_source_ids=[founder_message.message_id],
            )
        )

    app.state.intake_interviewer = fake_interviewer
    created = client.post("/founders/founder_demo/intake/sessions").json()
    session_id = created["session"]["session_id"]
    body = {
        "text": "We help university labs coordinate shared research equipment.",
        "client_message_id": "browser-message-1",
        "expected_revision": created["session"]["revision"],
    }

    first = client.post(
        f"/founders/founder_demo/intake/sessions/{session_id}/messages", json=body
    )
    duplicate = client.post(
        f"/founders/founder_demo/intake/sessions/{session_id}/messages", json=body
    )

    assert first.status_code == duplicate.status_code == 200
    assert calls == 1
    payload = first.json()
    assert payload["turn_pending"] is False
    assert [message["role"] for message in payload["messages"]] == [
        "founder",
        "assistant",
    ]
    fact = payload["session"]["fields"]["startup_description"]
    assert fact["status"] == "proposed"
    assert fact["confirmed_at"] is None
    assert payload["ready_to_complete"] is False


def test_unambiguous_chat_confirmation_updates_memory_without_a_second_model_call(
    client,
):
    calls = 0

    async def fake_interviewer(session, messages, documents):
        nonlocal calls
        calls += 1
        founder_message = messages[-1]
        source_id = founder_message.message_id
        return _chat_result(
            IntakeProposal(
                field="startup_description",
                value="A platform for coordinating shared laboratory equipment.",
                confidence=0.95,
                evidence_source_ids=[source_id],
            ),
            claims=[
                IntakeClaimProposal(
                    category="customers",
                    text="The initial users are university laboratory managers.",
                    confidence=0.9,
                    evidence_source_ids=[source_id],
                )
            ],
            summary="The startup coordinates shared university lab equipment.",
        )

    app.state.intake_interviewer = fake_interviewer
    created = client.post("/founders/founder_demo/intake/sessions").json()
    session_id = created["session"]["session_id"]
    proposed = client.post(
        f"/founders/founder_demo/intake/sessions/{session_id}/messages",
        json={
            "text": "We coordinate shared university laboratory equipment.",
            "client_message_id": "proposal-turn",
            "expected_revision": created["session"]["revision"],
        },
    ).json()

    confirmed = client.post(
        f"/founders/founder_demo/intake/sessions/{session_id}/messages",
        json={
            "text": "Yes.",
            "client_message_id": "confirmation-turn",
            "expected_revision": proposed["session"]["revision"],
        },
    )

    assert confirmed.status_code == 200
    assert calls == 1
    payload = confirmed.json()
    assert payload["session"]["pending_confirmation_batch"] is None
    assert payload["session"]["fields"]["startup_description"]["status"] == "confirmed"
    claims = payload["session"]["memory"]["claims"].values()
    assert {claim["status"] for claim in claims} == {"confirmed"}
    assert "Customers:" in payload["session"]["memory"]["confirmed_summary"]

    duplicate = client.post(
        f"/founders/founder_demo/intake/sessions/{session_id}/messages",
        json={
            "text": "Yes.",
            "client_message_id": "confirmation-turn",
            "expected_revision": proposed["session"]["revision"],
        },
    )
    assert duplicate.status_code == 200
    assert calls == 1


def test_ambiguous_confirmation_is_sent_to_the_interviewer(client):
    calls = 0

    async def fake_interviewer(session, messages, documents):
        nonlocal calls
        calls += 1
        source_id = messages[-1].message_id
        return _chat_result(
            IntakeProposal(
                field="startup_description",
                value="A laboratory scheduling platform.",
                confidence=0.9,
                evidence_source_ids=[source_id],
            )
        )

    app.state.intake_interviewer = fake_interviewer
    created = client.post("/founders/founder_demo/intake/sessions").json()
    session_id = created["session"]["session_id"]
    proposed = client.post(
        f"/founders/founder_demo/intake/sessions/{session_id}/messages",
        json={
            "text": "We make laboratory scheduling software.",
            "client_message_id": "ambiguous-proposal",
            "expected_revision": 0,
        },
    ).json()

    response = client.post(
        f"/founders/founder_demo/intake/sessions/{session_id}/messages",
        json={
            "text": "Yes, except we focus on teaching laboratories.",
            "client_message_id": "ambiguous-correction",
            "expected_revision": proposed["session"]["revision"],
        },
    )

    assert response.status_code == 200
    assert calls == 2


def test_batch_and_claim_endpoints_enforce_revision_and_explicit_actions(client):
    async def fake_interviewer(session, messages, documents):
        source_id = messages[-1].message_id
        return _chat_result(
            claims=[
                IntakeClaimProposal(
                    category="problem",
                    text="Laboratory managers coordinate equipment manually.",
                    confidence=0.9,
                    evidence_source_ids=[source_id],
                )
            ]
        )

    app.state.intake_interviewer = fake_interviewer
    created = client.post("/founders/founder_demo/intake/sessions").json()
    session_id = created["session"]["session_id"]
    proposed = client.post(
        f"/founders/founder_demo/intake/sessions/{session_id}/messages",
        json={
            "text": "Lab managers still coordinate shared equipment manually.",
            "client_message_id": "endpoint-proposal",
            "expected_revision": 0,
        },
    ).json()
    batch = proposed["session"]["pending_confirmation_batch"]
    claim_id = batch["claim_ids"][0]

    confirmed = client.post(
        f"/founders/founder_demo/intake/sessions/{session_id}"
        f"/proposal-batches/{batch['batch_id']}/confirm",
        json={"expected_revision": proposed["session"]["revision"]},
    )

    assert confirmed.status_code == 200
    confirmed_payload = confirmed.json()
    assert confirmed_payload["session"]["memory"]["claims"][claim_id]["status"] == (
        "confirmed"
    )
    stale = client.post(
        f"/founders/founder_demo/intake/sessions/{session_id}"
        f"/proposal-batches/{batch['batch_id']}/confirm",
        json={"expected_revision": proposed["session"]["revision"]},
    )
    assert stale.status_code == 409

    corrected = client.patch(
        f"/founders/founder_demo/intake/sessions/{session_id}/claims/{claim_id}",
        json={
            "action": "correct",
            "text": "University lab managers coordinate equipment manually.",
            "expected_revision": confirmed_payload["session"]["revision"],
            "client_action_id": "claim-edit-1",
        },
    )
    assert corrected.status_code == 200
    claims = corrected.json()["session"]["memory"]["claims"]
    assert claims[claim_id]["status"] == "superseded"
    assert any(
        claim["text"] == "University lab managers coordinate equipment manually."
        and claim["status"] == "confirmed"
        for claim in claims.values()
    )


def test_claim_endpoint_rejects_cross_founder_and_mass_assignment(client):
    other = new_session("founder_other", None)
    other.memory.claims["claim_other"] = IntakeKnowledgeClaim(
        claim_id="claim_other",
        category="team",
        text="The team has two founders.",
        confidence=0.9,
        evidence=[IntakeEvidence(source_type="message", source_id="message_other")],
    )
    app.state.repo.create_intake_session(other)

    cross_founder = client.patch(
        f"/founders/founder_demo/intake/sessions/{other.session_id}/claims/claim_other",
        json={
            "action": "confirm",
            "expected_revision": 0,
            "client_action_id": "cross-founder",
        },
    )
    assert cross_founder.status_code == 404

    created = client.post("/founders/founder_demo/intake/sessions").json()
    session_id = created["session"]["session_id"]
    extra = client.patch(
        f"/founders/founder_demo/intake/sessions/{session_id}/claims/claim_missing",
        json={
            "action": "confirm",
            "expected_revision": 0,
            "client_action_id": "mass-assignment",
            "role": "admin",
        },
    )
    assert extra.status_code == 422


def test_chat_provider_failure_is_sanitized_and_releases_session(client):
    async def failing_interviewer(session, messages, documents):
        raise RuntimeError("aws_secret_access_key=do-not-leak-founder@example.com")

    app.state.intake_interviewer = failing_interviewer
    created = client.post("/founders/founder_demo/intake/sessions").json()
    session_id = created["session"]["session_id"]
    response = client.post(
        f"/founders/founder_demo/intake/sessions/{session_id}/messages",
        json={
            "text": "Here is my company.",
            "client_message_id": "failure-1",
            "expected_revision": 0,
        },
    )

    assert response.status_code == 503
    assert "do-not-leak" not in response.text
    loaded = client.get(
        f"/founders/founder_demo/intake/sessions/{session_id}"
    ).json()
    assert loaded["turn_pending"] is False
    assert loaded["messages"][0]["role"] == "founder"
    assert "example.com" not in loaded["messages"][0]["text"]


def test_chat_rate_limit_is_persistent_and_returns_retry_after(client):
    async def fake_interviewer(session, messages, documents):
        return _chat_result()

    app.state.intake_interviewer = fake_interviewer
    app.state.config = replace(app.state.config, intake_turns_per_hour=2)
    created = client.post("/founders/founder_demo/intake/sessions").json()
    session_id = created["session"]["session_id"]
    revision = 0
    for number in range(2):
        response = client.post(
            f"/founders/founder_demo/intake/sessions/{session_id}/messages",
            json={
                "text": f"Founder turn {number}",
                "client_message_id": f"rate-{number}",
                "expected_revision": revision,
            },
        )
        assert response.status_code == 200
        revision = response.json()["session"]["revision"]

    rejected = client.post(
        f"/founders/founder_demo/intake/sessions/{session_id}/messages",
        json={
            "text": "One turn too many",
            "client_message_id": "rate-2",
            "expected_revision": revision,
        },
    )
    assert rejected.status_code == 429
    assert rejected.headers["retry-after"] == "3600"


def test_chat_rejects_cross_founder_and_mass_assignment(client):
    other = new_session("founder_other", None)
    app.state.repo.create_intake_session(other)
    cross_founder = client.post(
        f"/founders/founder_demo/intake/sessions/{other.session_id}/messages",
        json={"text": "Probe", "client_message_id": "probe-1", "expected_revision": 0},
    )
    assert cross_founder.status_code == 404

    created = client.post("/founders/founder_demo/intake/sessions").json()
    session_id = created["session"]["session_id"]
    extra = client.post(
        f"/founders/founder_demo/intake/sessions/{session_id}/messages",
        json={
            "text": "Probe",
            "client_message_id": "probe-2",
            "expected_revision": 0,
            "role": "assistant",
        },
    )
    assert extra.status_code == 422
