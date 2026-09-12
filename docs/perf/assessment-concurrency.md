# Design note: bounded parallel assessments

Status: implemented behind `KAIROS_ASSESSMENT_CONCURRENCY`, default `1`
(sequential, identical to before). Drafting and auditing stay sequential.

## Why naive `asyncio.gather` overspends

Today `run_once` judges survivors one at a time. Each call:

1. takes an assessment slot (`RunBudget.take_assessment_slot`),
2. sets the Strands per-call limit to 25% of the tokens *remaining* in the run,
3. calls the model, then
4. charges actual usage and raises `BudgetExceeded` if the run ceiling or the
   daily dollar cap is now crossed.

Every check that protects money happens either before the call against a
total that ignores calls already in flight, or after the money is spent.
Sequentially that is safe: at most one call is ever unaccounted for.

With `gather` over N survivors:

- **Token ceiling.** All N calls read the same "remaining" before any charges
  land, so each is allowed 25% of it. Four calls may legitimately consume the
  entire remaining budget, and every call beyond four is headroom the run does
  not have. The ceiling check fires only when results come back, after the
  spend.
- **Daily dollar cap.** The ledger is read after each call. N calls started
  under a nearly exhausted cap all proceed, and the cap is exceeded by up to N
  calls instead of one.
- **Failure semantics.** When one call raises `BudgetExceeded` or `Throttled`,
  `gather` leaves the other calls running. They keep spending after the run has
  already decided to halt.
- **Determinism.** Results land in completion order. `ctx.assessments` insertion
  order, abstention notes and ties in the stable escalation sort would all
  depend on network timing.

## Design

**Concurrency limit.** `KAIROS_ASSESSMENT_CONCURRENCY` in `[1, 4]`; invalid values
refuse to start. `1` takes the existing sequential code path unchanged.

**Admission in ranked order.** Survivors are ranked exactly as today. The
assessment cap is applied to that ranking before any call, so the same set of
opportunities is judged and the same cap notes and skips are recorded
regardless of concurrency.

**Reserve, then reconcile.** Before a call starts, it reserves:

- tokens: its per-call Strands limit, computed from `remaining - reserved`
  rather than `remaining`;
- dollars: that reservation priced at the most expensive configured rate,
  added to today's ledger total when checking the daily cap.

A call is admitted only if the reservation fits. If it does not fit and other
calls are in flight, it waits for one to reconcile. If nothing is in flight,
it raises `BudgetExceeded` exactly as a sequential run would. After the call,
the reservation is released and actual usage is charged through the existing
`RunBudget.charge`, which still raises on a crossed ceiling. All reservation
bookkeeping runs on the event loop thread between awaits, so it needs no lock.

Residual risk: Strands limits are soft. A call can overshoot its reservation,
as it can today; the bound on overshoot becomes K calls instead of 1. With
K at most 4 and each call limited to a share of what is left, this stays
within one call's worth of the ceiling per in-flight slot. Recorded, not
hidden.

**Deterministic output.** Each task returns its assessment and any abstention
note instead of writing `ctx`. Results are written to `ctx.assessments` and
`report.notes` in ranked order after all tasks finish.

**Failure: halt and surface nothing partial.** On the first `BudgetExceeded`,
`Throttled`, cancellation or unexpected exception, every other in-flight task is
cancelled and awaited, nothing from the batch is written to `ctx`, and the
exception propagates to `run_once`'s existing handlers, which clear the pending
inbox. If several tasks fail, the failure of the highest-ranked task is raised,
so the reported halt reason does not depend on timing. An `Abstention` stays an
outcome (`INSUFFICIENT_INFO`), not a failure, as today.

## Not in scope

Parallel drafting and auditing, until assessment parallelism has run in
production. Drafts share the recall table and the Auditor depends on the
Drafter's output, so the win is smaller and the ordering hazards larger.
