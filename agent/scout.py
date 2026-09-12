"""Scout — the orchestrator.

Two entry points, deliberately:

*   **`run_once`** — the deterministic pipeline. This is what EventBridge
    invokes at 06:00 and what the demo runs. Discovery, filtering, ranking,
    caps, idempotency and the escalation policy are Python. A model decides
    *fit* and *prose*; it does not decide control flow, because control flow
    is where the cost caps and the never-notify-twice guarantee live.

*   **`build_scout_agent`** — the same six tools registered on a real Strands
    `Agent` with `agent/prompts/scout.md`. This is the interactive path, used
    from AgentCore Runtime. It can call the tools in whatever order it likes;
    it still cannot route around the policy, because the policy is enforced
    inside each tool rather than in the prompt.

Both share one `RunContext`, so both produce the same `RunReport` and the
same audit trail. See DECISIONS.md for why the scheduled path is not
model-driven.

The primary entry point is a scheduled run, not an HTTP request from a user
(Section 2). Nothing here waits for a click.
"""

from __future__ import annotations

import logging
import time
import uuid
from datetime import date, datetime, timezone

from agent import guardrails
from agent.budget import BudgetExceeded, RunBudget
from agent.models import (
    FounderProfile,
    InboxItem,
    KnowledgeBase,
    Opportunity,
    RunReport,
    SkipRecord,
)
from agent.prompting import Abstention, Throttled, load_prompt
from agent.runtime import RunContext, SubAgents
from agent.sanitize import safe_detail
from agent.toolset import build_toolset
from agent.tools.discovery import Source

log = logging.getLogger("kairos.scout")

_SOURCE_PRIORITY = {"seed": 3, "browser": 2, "grants_gov": 2}


class _StageClock:
    """Logs how long each pipeline stage took, measured between marks.

    Durations only. Stage names are fixed strings, so nothing a source or a
    model wrote can reach this log line.
    """

    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        self.last = time.perf_counter()

    def __call__(self, stage: str) -> None:
        now = time.perf_counter()
        log.info(
            "stage_complete",
            extra={
                "run_id": self.run_id,
                "stage": stage,
                "duration_ms": round((now - self.last) * 1000, 1),
            },
        )
        self.last = now


def assessment_priority(opportunity: Opportunity, today: date) -> tuple:
    """Rank decision quality and urgency before possible award size."""
    known_eligibility = sum(
        value is not None for value in opportunity.eligibility.model_dump().values()
    )
    has_current_deadline = (
        opportunity.deadline is not None and opportunity.deadline >= today
    )
    days_until_deadline = (
        (opportunity.deadline - today).days if has_current_deadline else 10_000
    )
    return (
        known_eligibility > 0,
        known_eligibility,
        _SOURCE_PRIORITY.get(opportunity.source, 0),
        bool(opportunity.criteria),
        min(len(opportunity.criteria), 5),
        has_current_deadline or opportunity.rolling,
        -days_until_deadline,
        opportunity.best_award or 0,
    )


async def assess_concurrently(ctx: RunContext, ranked: list[Opportunity], limit: int) -> None:
    """Judge `ranked` with at most `limit` Assessor calls in flight.

    *   **Admission in rank order.** A call starts only after reserving token
        and dollar budget. When a reservation does not fit, admission waits
        for an in-flight call to reconcile; with nothing in flight it halts
        with the cap that refused it.
    *   **Deterministic output.** Results are written to `ctx` in rank order,
        never completion order.
    *   **Failure halts.** After the first failure nothing new is admitted.
        Calls already in flight finish, so their spend is charged rather than
        billed and forgotten. Results ranked above the highest-ranked failure
        are recorded — exactly what a sequential run would have recorded —
        and that failure is raised for `run_once` to halt on.
    *   **External cancellation** (job cancel, run timeout) cancels every
        in-flight call immediately and releases their reservations.
    """
    import asyncio

    from agent.toolset import judge, record_assessment

    budget = ctx.budget
    results: dict[int, tuple] = {}
    failures: dict[int, BaseException] = {}
    running: dict[asyncio.Task, tuple[int, object]] = {}
    queue = list(enumerate(ranked))

    try:
        while queue or running:
            while queue and not failures and len(running) < limit:
                reservation = budget.reserve(tier="reasoning")
                if reservation is None:
                    if not running:
                        failures[queue[0][0]] = budget.refusal()
                    break
                index, opportunity = queue.pop(0)
                budget.take_assessment_slot()
                task = asyncio.ensure_future(
                    judge(ctx, opportunity, ctx.eligibility[opportunity.id])
                )
                running[task] = (index, reservation)
            if not running:
                break
            done, _ = await asyncio.wait(running, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                index, reservation = running.pop(task)
                budget.release(reservation)
                if task.cancelled():
                    failures[index] = asyncio.CancelledError()
                elif task.exception() is not None:
                    failures[index] = task.exception()
                else:
                    results[index] = task.result()
    finally:
        if running:
            for task in running:
                task.cancel()
            await asyncio.gather(*running, return_exceptions=True)
            for _, reservation in running.values():
                budget.release(reservation)

    first_failure = min(failures) if failures else len(ranked)
    for index in range(first_failure):
        if index in results:
            record_assessment(ctx, *results[index])
    if failures:
        raise failures[first_failure]


def new_run_context(
    *,
    profile: FounderProfile,
    repo,
    budget: RunBudget,
    agents: SubAgents | None = None,
    today: date | None = None,
) -> RunContext:
    """Build the mutable state one pipeline run threads through.

    The run id is generated here, which makes this the moment a run starts
    existing — before any source is queried. `today` is injectable so
    deadline arithmetic is testable without freezing the clock; it defaults
    to the UTC date, so a run late in a local evening may already be
    "tomorrow" for deadline-urgency purposes.

    `agents=None` is legitimate: the deterministic half of the pipeline runs
    without any model, which is what the dry run and most tests exercise.
    """
    run_id = f"run_{uuid.uuid4().hex[:12]}"
    return RunContext(
        profile=profile,
        kb=KnowledgeBase.from_profile(profile),
        budget=budget,
        report=RunReport(run_id=run_id, founder_id=profile.founder_id),
        repo=repo,
        agents=agents,
        today=today or datetime.now(timezone.utc).date(),
    )


def build_scout_agent(ctx: RunContext, sources: list[Source]):
    """A real Strands orchestrator over the same tools.

    Kept separate from `run_once` so the scheduled path stays deterministic.
    """
    from strands import Agent

    from agent.config import settings
    from agent.subagents.base import build_model

    prompt = load_prompt("scout")
    return Agent(
        model=build_model(settings().reasoning),
        system_prompt=prompt.text,
        tools=build_toolset(ctx, sources),
        name="scout",
        description="Decides what a student founder should hear about when they wake up.",
    )


async def run_once(ctx: RunContext, sources: list[Source]) -> RunReport:
    """One overnight run, start to finish.

    The four counters are the output that matters. A run that scans 214 and
    surfaces 3 is the entire pitch in one line.
    """
    started = time.perf_counter()
    report = ctx.report
    # A Strands @tool stays directly callable as a plain function — verified
    # against strands-agents 1.53.0. That is what lets the scheduled path call
    # the same tool objects the Scout agent is given, so both paths share one
    # implementation and one audit trail.
    tools = {t.tool_name: t for t in build_toolset(ctx, sources)}
    stage = _StageClock(report.run_id)

    try:
        # 1. Discover. A dead source is recorded; the run continues.
        tools["discover"]()
        stage("discover")

        # 2. Deterministic gate. Cheap, and it decides most of the outcome.
        tools["filter_eligibility"]()
        stage("filter_eligibility")

        # Source-stated rules the profile cannot answer stay three-valued.
        # Definite founder answers may resolve them before model judgment.
        from agent.eligibility_clarifications import resolve_founder_answers

        await resolve_founder_answers(ctx)
        stage("resolve_founder_answers")

        # 3. Judge the survivors, most valuable first, until the cap.
        survivors = sorted(
            (ctx.retrieved[oid] for oid in ctx.eligibility if ctx.eligibility[oid].verdict != "INELIGIBLE"),
            key=lambda o: assessment_priority(o, ctx.today),
            reverse=True,
        )
        assessed = 0
        if ctx.budget.assessment_concurrency > 1:
            admitted = survivors[: ctx.budget.max_assessments]
            await assess_concurrently(ctx, admitted, ctx.budget.assessment_concurrency)
            assessed = len(admitted)
        for opportunity in survivors[assessed:]:
            if assessed >= ctx.budget.max_assessments:
                # Reported, never silently dropped (Section 9, rule 12).
                skipped = len(survivors) - assessed
                report.notes.append(
                    f"assessment cap reached: {skipped} eligible opportunities were "
                    f"not judged this run"
                )
                for remaining in survivors[assessed:]:
                    report.skips.append(
                        SkipRecord(
                            opportunity_id=remaining.id,
                            opportunity_title=remaining.title,
                            stage="assessor",
                            reason="assessment cap reached for this run",
                        )
                    )
                break
            await tools["assess_fit"](opportunity.id)
            assessed += 1

        from agent.eligibility_clarifications import persist_plausible_questions

        stage("assess")
        persist_plausible_questions(ctx)
        stage("persist_questions")

        # 4. Apply the escalation policy, then rank what survives it.
        candidates = []
        for opportunity_id, assessment in ctx.assessments.items():
            opportunity = ctx.retrieved[opportunity_id]
            decision = guardrails.escalation_decision(
                assessment=assessment,
                opportunity=opportunity,
                eligibility=ctx.eligibility[opportunity_id].verdict,
                max_application_hours=ctx.profile.max_application_hours,
                min_award=ctx.profile.min_award,
                today=ctx.today,
                already_surfaced=ctx.repo.has_surfaced(
                    ctx.profile.founder_id, opportunity_id
                ),
            )
            if decision.surface:
                candidates.append((opportunity_id, assessment, decision))
            else:
                report.skips.append(
                    SkipRecord(
                        opportunity_id=opportunity_id,
                        opportunity_title=opportunity.title,
                        stage="escalation_policy",
                        reason=decision.reason,
                    )
                )

        candidates.sort(
            key=lambda c: guardrails.rank_key(c[1], ctx.retrieved[c[0]]), reverse=True
        )

        stage("escalation_policy")

        # 5. Draft only what will actually be shown, and only above the
        #    cold-start floor. Drafting something nobody sees is pure spend.
        notifying = candidates[: guardrails.MAX_SURFACED_PER_RUN]
        for opportunity_id, _, _ in notifying:
            if opportunity_id in ctx.forms and not ctx.kb.is_cold(guardrails.MIN_KB_CHUNKS):
                await tools["draft_and_audit"](opportunity_id)

        stage("draft_and_audit")

        # 6. Queue. Overflow past the cap goes to a passive list with no ping.
        for index, (opportunity_id, assessment, decision) in enumerate(candidates):
            opportunity = ctx.retrieved[opportunity_id]
            draft = ctx.drafts.get(opportunity_id)
            summary = decision.reason
            if ctx.kb.is_cold(guardrails.MIN_KB_CHUNKS) and index == 0:
                from agent.subagents.drafter import COLD_START_MESSAGE

                summary = f"{summary}\n\n{COLD_START_MESSAGE}"
            ctx.pending_inbox.append(
                InboxItem(
                    item_id=f"{report.run_id}:{opportunity_id}",
                    founder_id=ctx.profile.founder_id,
                    opportunity_id=opportunity_id,
                    kind=decision.kind,
                    headline=_headline(opportunity, assessment, ctx.today),
                    summary=summary,
                    assessment=assessment,
                    draft_id=draft.draft_id if draft else None,
                    passive=index >= guardrails.MAX_SURFACED_PER_RUN,
                )
            )

    except BudgetExceeded as exc:
        # Halt and report. No partial digest, no degraded run.
        report.halted_reason = safe_detail(f"{exc.cap}: {exc.detail}")
        ctx.pending_inbox.clear()
        log.warning("run_halted", extra={"run_id": report.run_id, "cap": exc.cap})
    except Throttled as exc:
        # Section 11.12: backoff is exhausted, so abort and report. A busy
        # region is not a judgment about any opportunity, and surfacing a
        # partial digest would make it look like one.
        report.halted_reason = safe_detail(f"THROTTLED: {exc.detail}")
        ctx.pending_inbox.clear()
        log.warning("run_throttled", extra={"run_id": report.run_id})
    except Abstention as exc:
        report.halted_reason = safe_detail(
            f"sub-agent abstained and could not continue: {exc.detail}"
        )
        ctx.pending_inbox.clear()
    except Exception as exc:  # noqa: BLE001
        # A crash mid-run must not look like a quiet night.
        report.halted_reason = safe_detail(f"{type(exc).__name__}: {exc}")
        ctx.pending_inbox.clear()
        log.exception("run_failed", extra={"run_id": report.run_id})

    # 7. Persist. Only now — a halted run surfaces nothing.
    #
    # Opportunities are written even on a halted run. They are not a digest,
    # they are the rows the rejections and skips were written *about*, and a
    # rejection you cannot resolve back to what was rejected is not much of an
    # audit trail. A failure here must not turn a completed run into a halted
    # one, so it is recorded as a note rather than raised.
    for opportunity in ctx.retrieved.values():
        try:
            ctx.repo.save_opportunity(opportunity)
        except Exception as exc:  # noqa: BLE001
            report.notes.append(
                f"could not persist opportunity {opportunity.id}: "
                f"{type(exc).__name__}: {exc}"
            )
            log.warning(
                "opportunity_persist_failed",
                extra={"run_id": report.run_id, "opportunity_id": opportunity.id},
            )

    for item in ctx.pending_inbox:
        if ctx.repo.save_inbox_item(item) and not item.passive:
            report.surfaced += 1
    for draft in ctx.drafts.values():
        ctx.repo.save_draft(draft)

    if not report.halted_reason:
        for opportunity_id in ctx.applied_eligibility_answers:
            mark_reassessed = getattr(ctx.repo, "mark_eligibility_reassessed", None)
            if mark_reassessed is not None:
                mark_reassessed(
                    ctx.profile.founder_id,
                    opportunity_id,
                    before=report.started_at,
                )

    stage("persist")
    report.usage = ctx.budget.usage
    report.finished_at = datetime.now(timezone.utc)
    report.duration_s = round(time.perf_counter() - started, 3)
    ctx.repo.save_run(report)

    log.info(
        "run_complete",
        extra={
            "run_id": report.run_id,
            "scanned": report.scanned,
            "filtered_out": report.filtered_out,
            "judged": report.judged,
            "surfaced": report.surfaced,
            "duration_s": report.duration_s,
            "halted_reason": report.halted_reason,
            "tokens": report.usage.total_tokens,
        },
    )
    return report


def _headline(opportunity, assessment, today: date) -> str:
    """Name what the founder controls, and compute the countdown in Python."""
    remaining = guardrails.days_until(opportunity.deadline, today)
    award = opportunity.best_award
    parts = [opportunity.title]
    if award:
        parts.append(f"up to ${award:,}")
    if remaining is not None:
        parts.append(f"{remaining} days left")
    parts.append(f"~{assessment.effort_hours:.0f}h of work")
    return " · ".join(parts)
