"""Persistence and API contract for founder eligibility clarifications."""

from __future__ import annotations

from fastapi.testclient import TestClient

from agent.models import EligibilityQuestion, FounderProfile
from api.main import app
from api.repository import SqliteRepository
from tests.factories import profile


def question(**overrides) -> EligibilityQuestion:
    base = {
        "question_id": "eq_demo_1",
        "founder_id": "founder_demo",
        "opportunity_id": "opp_demo_1",
        "opportunity_title": "[DEMO] Founder Grant",
        "source_url": "https://example.invalid/grant",
        "check": "CITIZENSHIP",
        "question": "Is at least 51% of the company owned by eligible residents?",
        "requirement": "The company must be at least 51% owned by eligible residents.",
        "source_doc": "https://example.invalid/grant#eligibility",
    }
    base.update(overrides)
    return EligibilityQuestion(**base)


def test_legacy_profiles_default_answer_reuse_off():
    legacy = profile().model_dump(exclude={"reuse_eligibility_answers"})

    loaded = FounderProfile.model_validate(legacy)

    assert loaded.reuse_eligibility_answers is False


def test_repository_filters_and_edits_questions(tmp_path):
    repo = SqliteRepository(f"sqlite:///{tmp_path}/questions.db")
    repo.save_eligibility_question(question(question_id="pending"))
    repo.save_eligibility_question(
        question(question_id="answered", answer="yes")
    )

    assert [item.question_id for item in repo.list_eligibility_questions(
        "founder_demo", "pending"
    )] == ["pending"]
    assert [item.question_id for item in repo.list_eligibility_questions(
        "founder_demo", "answered"
    )] == ["answered"]

    unsure = repo.answer_eligibility_question("pending", "not_sure")
    assert unsure is not None
    assert unsure.status == "pending"
    assert unsure.answer_updated_at is not None

    resolved = repo.answer_eligibility_question("pending", "no")
    assert resolved is not None
    assert resolved.status == "answered"
    assert resolved.answer == "no"


def test_repository_scopes_question_lists_by_founder(tmp_path):
    repo = SqliteRepository(f"sqlite:///{tmp_path}/questions.db")
    repo.save_eligibility_question(question())
    repo.save_eligibility_question(
        question(question_id="eq_other", founder_id="founder_other")
    )

    assert [item.question_id for item in repo.list_eligibility_questions(
        "founder_demo", "all"
    )] == ["eq_demo_1"]


def test_question_api_lists_and_edits_answers(monkeypatch, tmp_path):
    monkeypatch.setenv("KAIROS_DB_URL", f"sqlite:///{tmp_path}/api.db")
    from agent import config

    config.settings.cache_clear()
    with TestClient(app) as client:
        app.state.repo.save_eligibility_question(question())

        pending = client.get(
            "/founders/founder_demo/eligibility-questions?status=pending"
        )
        answered = client.put(
            "/founders/founder_demo/eligibility-questions/eq_demo_1/answer",
            json={"answer": "yes"},
        )
        after = client.get(
            "/founders/founder_demo/eligibility-questions?status=answered"
        )

    config.settings.cache_clear()
    assert pending.status_code == 200
    assert pending.json()[0]["question_id"] == "eq_demo_1"
    assert answered.status_code == 200
    assert answered.json()["status"] == "answered"
    assert after.json()[0]["answer"] == "yes"


def test_question_api_rejects_invalid_answers_and_wrong_founders(monkeypatch, tmp_path):
    monkeypatch.setenv("KAIROS_DB_URL", f"sqlite:///{tmp_path}/api.db")
    from agent import config

    config.settings.cache_clear()
    with TestClient(app) as client:
        app.state.repo.save_eligibility_question(question())
        invalid = client.put(
            "/founders/founder_demo/eligibility-questions/eq_demo_1/answer",
            json={"answer": "probably"},
        )
        wrong_founder = client.put(
            "/founders/founder_other/eligibility-questions/eq_demo_1/answer",
            json={"answer": "yes"},
        )

    config.settings.cache_clear()
    assert invalid.status_code == 422
    assert wrong_founder.status_code == 404
