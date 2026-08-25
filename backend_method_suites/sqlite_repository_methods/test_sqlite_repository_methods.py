from __future__ import annotations

from agent.models import InboxItem
from tests.factories import draft, generated, opportunity, profile


def test_profile_and_opportunity_round_trip_as_structured_models(memory_repo):
    memory_repo.save_profile(profile(institution="Rutgers University"))
    memory_repo.save_opportunity(opportunity(id="opp_trace", award_max=25_000))

    stored_profile = memory_repo.get_profile("founder_demo")
    stored_opportunity = memory_repo.get_opportunity("opp_trace")

    assert stored_profile.institution == "Rutgers University"
    assert stored_opportunity.award_max == 25_000
    assert stored_opportunity.best_award == 25_000


def test_inbox_idempotency_and_state_update_preserve_run_decision(memory_repo):
    item = InboxItem(
        item_id="run_1:opp_1",
        founder_id="founder_demo",
        opportunity_id="opp_1",
        kind="APPLY",
        headline="[DEMO] Fit",
        summary="Worth applying.",
    )

    assert memory_repo.save_inbox_item(item) is True
    assert memory_repo.save_inbox_item(item) is False

    updated = memory_repo.set_inbox_state("run_1:opp_1", "dismissed")

    assert updated.state == "dismissed"
    assert updated.kind == "APPLY"
    assert updated.headline == "[DEMO] Fit"


def test_drafts_can_be_listed_by_founder_and_filtered_by_opportunity(memory_repo):
    memory_repo.save_draft(
        draft(generated("a", "Answer A"), draft_id="draft_a", opportunity_id="opp_a")
    )
    memory_repo.save_draft(
        draft(generated("b", "Answer B"), draft_id="draft_b", opportunity_id="opp_b")
    )

    all_drafts = memory_repo.list_drafts("founder_demo")
    opp_b = memory_repo.list_drafts("founder_demo", opportunity_id="opp_b")

    assert [d.draft_id for d in all_drafts] == ["draft_a", "draft_b"]
    assert [d.draft_id for d in opp_b] == ["draft_b"]


def test_recall_reuses_exact_normalized_questions_without_overclaiming_semantics(memory_repo):
    field = generated(
        "traction",
        "We have 40 active users.",
        question="Describe your traction to date!",
    )

    memory_repo.remember_answer("founder_demo", field)
    reused = memory_repo.recall("founder_demo", "Describe your traction to date")
    semantic_near_miss = memory_repo.recall("founder_demo", "How many users do you have?")

    assert reused is not None
    assert reused.status == "REUSED"
    assert semantic_near_miss is None
