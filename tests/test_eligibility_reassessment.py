"""Founder clarification generation and conservative answer reuse."""

from __future__ import annotations

import pytest

from agent.eligibility_clarifications import (
    MAX_SEMANTIC_COMPARISONS,
    build_questions,
    constraints_compatible,
    persist_plausible_questions,
    resolve_founder_answers,
)
from agent.models import Assessment, EligibilityQuestion, EligibilityRules
from agent.runtime import SubAgents
from agent.scheduler import RunLock, ScheduledRunFailureLog
from agent.scout import new_run_context, run_once
from agent.subagents import eligibility_reuse
from agent.subagents.eligibility_reuse import EquivalenceDecision
from agent.tools.eligibility import check_opportunity
from api.jobs import execute_job, new_job
from api.repository import SqliteRepository
from tests.conftest import FakeAgent
from tests.factories import TODAY, budget, opportunity, profile


def _compound_rules(requirement: str) -> EligibilityRules:
    return EligibilityRules(
        degree_levels=["undergrad"],
        citizenships=[requirement],
        entity_types=["none"],
    )


def _question(requirement: str, *, question_id: str, answer: str = "yes") -> EligibilityQuestion:
    return EligibilityQuestion(
        question_id=question_id,
        founder_id="founder_demo",
        opportunity_id=f"old_{question_id}",
        opportunity_title="[DEMO] Earlier Program",
        source_url="https://example.invalid/earlier",
        check="CITIZENSHIP",
        question="Does your company meet this requirement?",
        requirement=requirement,
        source_doc="https://example.invalid/earlier#eligibility",
        answer=answer,
    )


def _context(tmp_path, requirement: str, *, reuse: bool = False):
    repo = SqliteRepository(f"sqlite:///{tmp_path}/clarifications.db")
    founder = profile(reuse_eligibility_answers=reuse)
    opp = opportunity(
        id="current",
        eligibility=_compound_rules(requirement),
    )
    ctx = new_run_context(
        profile=founder,
        repo=repo,
        budget=budget(),
        today=TODAY,
    )
    ctx.retrieved = {opp.id: opp}
    ctx.eligibility = {opp.id: check_opportunity(opp, founder, TODAY)}
    return ctx, repo, opp


def test_source_unknown_checks_do_not_become_founder_questions():
    opp = opportunity(eligibility=EligibilityRules())
    result = check_opportunity(opp, profile(), TODAY)

    assert set(result.unknown_checks) >= {"DEGREE_LEVEL", "CITIZENSHIP", "ENTITY_TYPE"}
    assert build_questions("founder_demo", opp, result) == []


def test_compound_ownership_rule_becomes_a_specific_founder_question():
    requirement = (
        "Company must be at least 51% owned and controlled by U.S. citizens "
        "or permanent residents"
    )
    opp = opportunity(eligibility=_compound_rules(requirement))
    result = check_opportunity(opp, profile(), TODAY)

    questions = build_questions("founder_demo", opp, result)

    assert len(questions) == 1
    assert questions[0].check == "CITIZENSHIP"
    assert questions[0].requirement == requirement
    assert "ownership" in questions[0].question


@pytest.mark.asyncio
async def test_exact_requirement_reuses_a_definite_answer_without_semantics(tmp_path):
    requirement = "Company must be owned and controlled by eligible U.S. residents"
    ctx, repo, _ = _context(tmp_path, requirement)
    repo.save_eligibility_question(_question(requirement, question_id="eq_prior"))

    async def should_not_run(left, right, run_budget):
        raise AssertionError("semantic classifier ran for an exact match")

    stats = await resolve_founder_answers(ctx, semantic_classifier=should_not_run)

    assert stats.exact == 1
    assert ctx.eligibility["current"].verdict == "ELIGIBLE"
    reused = repo.list_eligibility_questions("founder_demo", "answered")[0]
    assert reused.opportunity_id == "current"
    assert reused.reused_from_question_id == "eq_prior"


@pytest.mark.asyncio
async def test_a_reused_no_becomes_a_deterministic_rejection(tmp_path):
    requirement = "All founders and cofounders must be eligible U.S. residents"
    ctx, repo, _ = _context(tmp_path, requirement)
    repo.save_eligibility_question(
        _question(requirement, question_id="eq_prior", answer="no")
    )

    await resolve_founder_answers(ctx)

    result = ctx.eligibility["current"]
    assert result.verdict == "INELIGIBLE"
    assert result.rejection is not None
    assert result.rejection.founder_value == "no"
    assert ctx.report.filtered_out == 1


@pytest.mark.asyncio
async def test_semantic_reuse_is_opt_in_and_records_its_source(tmp_path):
    previous = "At least 51 percent of the company must be owned by U.S. residents"
    current = "U.S. residents must own a minimum of 51 percent of the company"
    ctx, repo, _ = _context(tmp_path, current, reuse=True)
    repo.save_eligibility_question(_question(previous, question_id="eq_prior"))
    calls = []

    async def equivalent(left, right, run_budget):
        calls.append((left, right, run_budget))
        return True

    stats = await resolve_founder_answers(ctx, semantic_classifier=equivalent)

    assert stats.semantic_attempts == 1
    assert stats.semantic_successes == 1
    assert len(calls) == 1
    reused = next(
        question
        for question in repo.list_eligibility_questions("founder_demo", "answered")
        if question.opportunity_id == "current"
    )
    assert reused.reused_from_question_id == "eq_prior"


@pytest.mark.asyncio
async def test_not_sure_on_this_requirement_is_not_overridden_by_reuse(tmp_path):
    requirement = "The company ownership and control requirement applies"
    ctx, repo, opp = _context(tmp_path, requirement)
    current = build_questions(
        "founder_demo",
        opp,
        ctx.eligibility[opp.id],
    )[0]
    current.answer = "not_sure"
    current.align_status_with_answer()
    repo.save_eligibility_question(current)
    repo.save_eligibility_question(_question(requirement, question_id="eq_prior"))

    stats = await resolve_founder_answers(ctx)

    assert stats.exact == 0
    assert ctx.eligibility["current"].verdict == "UNKNOWN"
    assert ctx.eligibility_questions["current"][0].answer == "not_sure"


def test_numeric_and_polarity_guards_fail_closed():
    assert constraints_compatible(
        "At least 51 percent must be owned by residents",
        "A minimum of 51 percent must be owned by residents",
    )
    assert not constraints_compatible(
        "At least 51 percent must be owned by residents",
        "At least 60 percent must be owned by residents",
    )
    assert not constraints_compatible(
        "At least 51 percent must be owned by residents",
        "At most 51 percent must be owned by residents",
    )
    assert not constraints_compatible(
        "Founders must be U.S. residents",
        "Founders must not be U.S. residents",
    )


@pytest.mark.asyncio
async def test_incompatible_constraints_never_reach_the_classifier(tmp_path):
    ctx, repo, _ = _context(
        tmp_path,
        "At least 60 percent company ownership by U.S. residents",
        reuse=True,
    )
    repo.save_eligibility_question(
        _question(
            "At least 51 percent company ownership by U.S. residents",
            question_id="eq_prior",
        )
    )

    async def should_not_run(left, right, run_budget):
        raise AssertionError("incompatible numeric constraints reached the model")

    stats = await resolve_founder_answers(ctx, semantic_classifier=should_not_run)

    assert stats.semantic_attempts == 0
    assert ctx.eligibility["current"].verdict == "UNKNOWN"


@pytest.mark.asyncio
async def test_semantic_comparisons_are_capped_per_run(tmp_path):
    ctx, repo, _ = _context(
        tmp_path,
        "Company ownership eligibility applies to every founder",
        reuse=True,
    )
    labels = [f"{chr(97 + first)}{chr(97 + second)}" for first in range(5) for second in range(5)]
    for index, label in enumerate(labels):
        repo.save_eligibility_question(
            _question(
                f"Company leadership eligibility category {label}",
                question_id=f"eq_{index}",
            )
        )
    calls = 0

    async def unrelated(left, right, run_budget):
        nonlocal calls
        calls += 1
        return False

    stats = await resolve_founder_answers(ctx, semantic_classifier=unrelated)

    assert calls == MAX_SEMANTIC_COMPARISONS
    assert stats.semantic_attempts == MAX_SEMANTIC_COMPARISONS
    assert ctx.eligibility["current"].verdict == "UNKNOWN"


@pytest.mark.asyncio
async def test_only_plausible_assessments_persist_unresolved_questions(tmp_path):
    ctx, repo, _ = _context(
        tmp_path,
        "Company ownership eligibility applies to every founder",
    )
    await resolve_founder_answers(ctx)
    ctx.assessments["current"] = Assessment(
        verdict="APPLY",
        reason="[DEMO] plausible",
        effort_hours=2,
    )

    assert persist_plausible_questions(ctx) == 1
    assert len(repo.list_eligibility_questions("founder_demo", "pending")) == 1


@pytest.mark.asyncio
async def test_skip_assessments_do_not_create_needs_you_rows(tmp_path):
    ctx, repo, _ = _context(
        tmp_path,
        "Company ownership eligibility applies to every founder",
    )
    await resolve_founder_answers(ctx)
    ctx.assessments["current"] = Assessment(
        verdict="SKIP",
        reason="[DEMO] irrelevant",
        effort_hours=2,
    )

    assert persist_plausible_questions(ctx) == 0
    assert repo.list_eligibility_questions("founder_demo", "all") == []


@pytest.mark.asyncio
async def test_a_run_consumes_the_answer_and_clears_pending_reassessment(tmp_path):
    requirement = "Company ownership and control must remain with eligible U.S. residents"
    ctx, repo, opp = _context(tmp_path, requirement)
    pending = build_questions(
        "founder_demo",
        opp,
        ctx.eligibility[opp.id],
    )[0]
    repo.save_eligibility_question(pending)
    answered = repo.answer_eligibility_question(pending.question_id, "yes")
    assert answered is not None and answered.reassessment_pending

    ctx = new_run_context(
        profile=ctx.profile,
        repo=repo,
        budget=budget(),
        agents=SubAgents(
            assessor=FakeAgent(
                Assessment(verdict="APPLY", reason="[DEMO] fit", effort_hours=2)
            ),
            assessor_version="v1",
            drafter=FakeAgent(),
            drafter_version="v1",
            auditor=FakeAgent(),
            auditor_version="v1",
        ),
        today=TODAY,
    )

    class OneSource:
        name = "seed"

        def fetch(self, since):
            return [opp]

    report = await run_once(ctx, [OneSource()])

    stored = repo.get_eligibility_question(pending.question_id)
    assert report.judged == 1
    assert stored is not None
    assert stored.reassessment_pending is False


@pytest.mark.asyncio
async def test_targeted_job_loads_and_reassesses_only_its_persisted_row(
    tmp_path,
    monkeypatch,
):
    requirement = "Company ownership and control must remain with eligible U.S. residents"
    founder = profile()
    opp = opportunity(id="target", eligibility=_compound_rules(requirement))
    repo = SqliteRepository(f"sqlite:///{tmp_path}/targeted.db")
    repo.save_profile(founder)
    repo.save_opportunity(opp)
    pending = build_questions(
        founder.founder_id,
        opp,
        check_opportunity(opp, founder, TODAY),
    )[0]
    repo.save_eligibility_question(pending)
    repo.answer_eligibility_question(pending.question_id, "yes")

    fake_agents = SubAgents(
        assessor=FakeAgent(
            Assessment(verdict="APPLY", reason="[DEMO] fit", effort_hours=2)
        ),
        assessor_version="v1",
        drafter=FakeAgent(),
        drafter_version="v1",
        auditor=FakeAgent(),
        auditor_version="v1",
    )
    monkeypatch.setattr(SubAgents, "build", classmethod(lambda cls: fake_agents))
    lock = RunLock(tmp_path / "locks")
    lease = lock.acquire(founder_id=founder.founder_id, run_kind="pipeline")
    job = new_job(
        founder_id=founder.founder_id,
        idempotency_key=None,
        source="eligibility_answer",
        use_demo_catalog=False,
        include_grants_gov=False,
        target_opportunity_id=opp.id,
    )

    await execute_job(
        job,
        repo,
        lease,
        ScheduledRunFailureLog(tmp_path / "failures.jsonl"),
    )

    stored_job = repo.get_job(job.job_id)
    assert stored_job is not None
    assert stored_job.status == "succeeded"
    report = repo.get_run(stored_job.run_id)
    assert report is not None
    assert report.scanned == 1
    assert report.judged == 1
    assert repo.get_eligibility_question(pending.question_id).reassessment_pending is False


@pytest.mark.asyncio
async def test_bedrock_classifier_requires_all_three_safety_flags(monkeypatch):
    run_budget = budget()
    agent = FakeAgent(
        EquivalenceDecision(
            equivalent=True,
            same_polarity=True,
            compatible_constraints=False,
        )
    )
    monkeypatch.setattr(eligibility_reuse, "build", lambda: (agent, object()))

    matched = await eligibility_reuse.equivalent(
        "At least 51 percent resident owned",
        "At least 60 percent resident owned",
        budget=run_budget,
    )

    assert matched is False
    assert run_budget.usage.total_tokens == 150
