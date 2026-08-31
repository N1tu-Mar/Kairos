"""Founder clarification generation, reuse, and deterministic application."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Awaitable, Callable

from agent.budget import BudgetExceeded
from agent.models import (
    EligibilityQuestion,
    EligibilityResult,
    Opportunity,
    Rejection,
)
from agent.sanitize import safe_detail

MAX_SEMANTIC_COMPARISONS = 20
_PLAUSIBLE_VERDICTS = frozenset({"APPLY", "MAYBE", "INSUFFICIENT_INFO"})
_FOUNDER_ANSWERABLE_CHECKS = frozenset({"CITIZENSHIP", "GEOGRAPHY", "INSTITUTION"})
_NEGATIVE = re.compile(
    r"\b(?:not|excluding|excluded|prohibited|ineligible|cannot|must not|may not)\b",
    re.IGNORECASE,
)
_NUMBER = re.compile(r"\b\d+(?:\.\d+)?\s*(?:%|percent(?:age)?|people|members?)?\b", re.I)
_DIRECTIONS = (
    (re.compile(r"\b(?:at least|minimum|min)\b", re.I), "min"),
    (re.compile(r"\b(?:at most|maximum|max|no more than)\b", re.I), "max"),
    (re.compile(r"\b(?:more than|greater than|over)\b", re.I), "gt"),
    (re.compile(r"\b(?:less than|under|fewer than)\b", re.I), "lt"),
)

SemanticClassifier = Callable[[str, str, object], Awaitable[bool]]


@dataclass
class ReuseStats:
    exact: int = 0
    semantic_attempts: int = 0
    semantic_successes: int = 0
    unresolved: int = 0


def normalize_requirement(value: str) -> str:
    """Stable equality key for exact reuse."""
    return re.sub(r"[^a-z0-9%]+", " ", value.lower()).strip()


def constraints_compatible(left: str, right: str) -> bool:
    """Fail closed when polarity or any stated number differs."""
    if bool(_NEGATIVE.search(left)) != bool(_NEGATIVE.search(right)):
        return False

    def signatures(value: str) -> tuple[tuple[str, str], ...]:
        found = []
        for match in _NUMBER.finditer(value):
            nearby = value[max(0, match.start() - 24) : match.start()]
            direction = next(
                (label for pattern, label in _DIRECTIONS if pattern.search(nearby)),
                "exact",
            )
            found.append((normalize_requirement(match.group()), direction))
        return tuple(found)

    left_numbers = signatures(left)
    right_numbers = signatures(right)
    return left_numbers == right_numbers


def _requirement(opportunity: Opportunity, check: str) -> str | None:
    rules = opportunity.eligibility
    values = {
        "CITIZENSHIP": rules.citizenships,
        "GEOGRAPHY": rules.geographies,
        "INSTITUTION": rules.institutions,
    }.get(check)
    if not values:
        return None
    return " / ".join(values)


def _question_text(check: str, requirement: str) -> str:
    if check == "CITIZENSHIP":
        ownership = bool(
            re.search(r"\b(?:own|owned|ownership|control|cofounder|team|percent)\b|%", requirement, re.I)
        )
        if ownership:
            return "Does your founding team or company meet this citizenship or ownership requirement?"
        return "Do you meet this citizenship or residency requirement?"
    if check == "GEOGRAPHY":
        return "Do you or your company meet this location requirement?"
    return "Are you currently affiliated with one of these institutions?"


def _source_doc(opportunity: Opportunity, requirement: str) -> str:
    normalized = normalize_requirement(requirement)
    for criterion in opportunity.criteria:
        criterion_text = normalize_requirement(criterion.text)
        if normalized in criterion_text or criterion_text in normalized:
            return criterion.source_doc
    return opportunity.criteria[0].source_doc if opportunity.criteria else opportunity.source_url


def build_questions(
    founder_id: str,
    opportunity: Opportunity,
    result: EligibilityResult,
) -> list[EligibilityQuestion]:
    """Build only questions backed by a source-stated structured rule."""
    questions: list[EligibilityQuestion] = []
    for check in result.unknown_checks:
        if check not in _FOUNDER_ANSWERABLE_CHECKS:
            continue
        requirement = _requirement(opportunity, check)
        if not requirement:
            continue
        digest = hashlib.sha256(
            f"{founder_id}\0{opportunity.id}\0{check}\0{normalize_requirement(requirement)}".encode()
        ).hexdigest()[:20]
        questions.append(
            EligibilityQuestion(
                question_id=f"eq_{digest}",
                founder_id=founder_id,
                opportunity_id=opportunity.id,
                opportunity_title=opportunity.title,
                source_url=opportunity.source_url,
                deadline=opportunity.deadline,
                check=check,
                question=_question_text(check, requirement),
                requirement=requirement,
                source_doc=_source_doc(opportunity, requirement),
            )
        )
    return questions


def _merge_existing(snapshot: EligibilityQuestion, existing: EligibilityQuestion | None) -> EligibilityQuestion:
    if existing is None:
        return snapshot
    snapshot.answer = existing.answer
    snapshot.answer_updated_at = existing.answer_updated_at
    snapshot.reused_from_question_id = existing.reused_from_question_id
    snapshot.reassessment_pending = existing.reassessment_pending
    snapshot.created_at = existing.created_at
    snapshot.updated_at = existing.updated_at
    snapshot.align_status_with_answer()
    return snapshot


def _apply_answer(
    result: EligibilityResult,
    question: EligibilityQuestion,
) -> EligibilityResult:
    remaining = [check for check in result.unknown_checks if check != question.check]
    if question.answer == "no":
        return EligibilityResult(
            opportunity_id=result.opportunity_id,
            verdict="INELIGIBLE",
            rejection=Rejection(
                opportunity_id=result.opportunity_id,
                opportunity_title=question.opportunity_title,
                check=question.check,
                detail="founder confirmed this requirement is not met",
                founder_value="no",
                required_value=question.requirement,
            ),
            unknown_checks=remaining,
            resolvable_blockers=result.resolvable_blockers,
        )
    return EligibilityResult(
        opportunity_id=result.opportunity_id,
        verdict="UNKNOWN" if remaining else "ELIGIBLE",
        unknown_checks=remaining,
        resolvable_blockers=result.resolvable_blockers,
    )


async def _default_classifier(left: str, right: str, budget: object) -> bool:
    from agent.subagents.eligibility_reuse import equivalent

    return await equivalent(left, right, budget=budget)


async def resolve_founder_answers(
    ctx,
    *,
    semantic_classifier: SemanticClassifier | None = None,
) -> ReuseStats:
    """Apply definite stored answers and retain unresolved questions for later."""
    classifier = semantic_classifier or _default_classifier
    all_stored = ctx.repo.list_eligibility_questions(ctx.profile.founder_id, "all")
    by_id = {question.question_id: question for question in all_stored}
    answered = [question for question in all_stored if question.answer in {"yes", "no"}]
    stats = ReuseStats()
    semantic_available = ctx.profile.reuse_eligibility_answers

    for opportunity_id, initial_result in list(ctx.eligibility.items()):
        if initial_result.verdict == "INELIGIBLE":
            continue
        opportunity = ctx.retrieved[opportunity_id]
        result = initial_result
        unresolved: list[EligibilityQuestion] = []
        for snapshot in build_questions(ctx.profile.founder_id, opportunity, result):
            current = _merge_existing(snapshot, by_id.get(snapshot.question_id))
            source: EligibilityQuestion | None = None

            if current.answer in {"yes", "no"}:
                source = current
            elif current.answer != "not_sure":
                exact = next(
                    (
                        prior
                        for prior in answered
                        if prior.check == current.check
                        and normalize_requirement(prior.requirement)
                        == normalize_requirement(current.requirement)
                    ),
                    None,
                )
                if exact is not None:
                    source = exact
                    stats.exact += 1

            if source is None and current.answer != "not_sure" and semantic_available:
                for prior in answered:
                    if prior.check != current.check or prior.question_id == current.question_id:
                        continue
                    if stats.semantic_attempts >= MAX_SEMANTIC_COMPARISONS:
                        semantic_available = False
                        break
                    if not constraints_compatible(prior.requirement, current.requirement):
                        continue
                    stats.semantic_attempts += 1
                    try:
                        matched = await classifier(
                            prior.requirement,
                            current.requirement,
                            ctx.budget,
                        )
                    except BudgetExceeded:
                        raise
                    except Exception as exc:  # noqa: BLE001
                        ctx.report.notes.append(
                            safe_detail(
                                f"eligibility semantic reuse stopped: {type(exc).__name__}: {exc}"
                            )
                        )
                        semantic_available = False
                        break
                    if matched:
                        source = prior
                        stats.semantic_successes += 1
                        break

            if source is None:
                unresolved.append(current)
                stats.unresolved += 1
                continue

            if source.question_id != current.question_id:
                current.answer = source.answer
                current.answer_updated_at = datetime.now(timezone.utc)
                current.reused_from_question_id = source.question_id
                current.updated_at = current.answer_updated_at
                current.reassessment_pending = False
                current.align_status_with_answer()
                ctx.repo.save_eligibility_question(current)
            result = _apply_answer(result, current)
            ctx.applied_eligibility_answers.add(opportunity_id)
            if result.rejection is not None:
                ctx.report.rejections.append(result.rejection)
                ctx.report.filtered_out += 1
                break

        ctx.eligibility[opportunity_id] = result
        if unresolved and result.verdict != "INELIGIBLE":
            ctx.eligibility_questions[opportunity_id] = unresolved

    if stats.exact or stats.semantic_attempts or stats.unresolved:
        ctx.report.notes.append(
            "eligibility answer reuse: "
            f"exact={stats.exact}, semantic_attempts={stats.semantic_attempts}, "
            f"semantic_successes={stats.semantic_successes}, unresolved={stats.unresolved}"
        )
    return stats


def persist_plausible_questions(ctx) -> int:
    """Write unresolved founder questions only for current, plausible rows."""
    saved = 0
    for opportunity_id, questions in ctx.eligibility_questions.items():
        assessment = ctx.assessments.get(opportunity_id)
        if assessment is None or assessment.verdict not in _PLAUSIBLE_VERDICTS:
            continue
        for question in questions:
            ctx.repo.save_eligibility_question(question)
            saved += 1
    if saved:
        ctx.report.notes.append(f"eligibility clarifications queued: {saved}")
    return saved
