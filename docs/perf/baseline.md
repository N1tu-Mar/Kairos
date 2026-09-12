# Kairos performance and complexity baseline

Measured 2026-09-12 on commit `157a7a5` (macOS, Python 3.13, Apple silicon).
Everything here is offline. No Bedrock call, no Grants.gov call, no paid service.

Reproduce:

```bash
uv run python scripts/bench/profile_dry_run.py --scenario demo --repeat 7
uv run python scripts/bench/profile_dry_run.py --scenario seed --repeat 7
uv run python scripts/bench/profile_dry_run.py --scenario demo_bigkb --repeat 7
uv run python scripts/bench/profile_api_path.py
```

## 1. Checks

| Check | Result |
|---|---|
| `uv run pytest -q` | 1171 passed, 2 warnings, 69.4 s wall, 350 MB max RSS |
| `npm test` (vitest) | 209 passed |
| `npm run typecheck` | pass |
| `npm run lint` | pass (0 warnings) |
| `npm run build` | pass |

CI has no Python linter or type checker; "lint" and "typecheck" are frontend-only.

## 2. Execution path: `POST /founders/{id}/runs` to dashboard

1. `api/main.py::trigger_run` (async route). Authorize `run:trigger`, load profile,
   idempotency lookup (`get_job_by_key`), `_require_paid_work_capacity`
   (opens the spend ledger SQLite), durable rate limit (2 SQL round trips),
   `RunLock.acquire` (lease SQLite, `BEGIN IMMEDIATE`), `save_job`, then
   `LocalJobExecutor.submit` creates an asyncio task. Returns 202.
2. `api/jobs.py::execute_job` on the same event loop. `save_job(running)`,
   `get_profile`, `RunBudget.from_settings`, `SubAgents.build()` (constructs three
   Strands agents), `load_forms()` (reads and validates every form JSON),
   `build_sources()`, then `asyncio.wait_for(run_once(...), run_timeout_s)`.
3. `agent/scout.py::run_once`
   1. `discover`: every source `fetch()` in sequence. `SeedCatalog` reads and validates
      JSON. `GrantsGovSource` uses synchronous `httpx.post`, with detail hydration in a
      thread pool that the calling thread still waits on.
   2. `filter_eligibility`: pure Python, three-valued (`ELIGIBLE`/`UNKNOWN`/`INELIGIBLE`).
   3. `resolve_founder_answers`: one SQL read; optional semantic reuse model calls, capped at 20.
   4. Assessment loop, sequential, ranked by `assessment_priority`, capped by
      `max_assessments`: `take_assessment_slot` then Assessor `structured_call`,
      which charges `RunBudget` (a spend-ledger SQLite read or write per call).
   5. `persist_plausible_questions`: one upsert per question.
   6. Escalation policy per assessment, which calls `has_surfaced` (one SQL read each).
   7. Drafting for the top three that have a form and a warm knowledge base: 1 `recall`
      per form field (each loads all answer rows), Drafter call, Auditor call, `ship_gate`.
   8. Queue inbox items. On `BudgetExceeded`, `Throttled`, `Abstention` or any crash the
      pending inbox is cleared.
   9. Persist: one session and commit per opportunity, inbox item, draft; then `save_run`.
4. `execute_job` writes the terminal job status and releases the lease in `finally`.
5. Dashboard: `frontend/src/components/manual-run.tsx` polls
   `GET /founders/{id}/jobs/{job_id}` through the Next proxy; the route is a sync
   handler (thread pool) doing `get_job` and `get_run`.

## 3. Synchronous work on the FastAPI event loop

Sync `def` routes run in Starlette's thread pool and are not a problem. These are:

| Where | Blocking work |
|---|---|
| `trigger_run` (async) | profile read, idempotency read, ledger SQLite, rate-limit SQL, lease SQLite, job insert |
| `answer_eligibility_question` (async) | same set plus question read/write |
| `send_intake_message` (async) | 6+ repository calls around the model call |
| `upload_intake_document` (async) | reserve/save/delete document SQL (extraction itself is `to_thread`) |
| `execute_job` task | every sync step of `run_once`: seed JSON parse, **synchronous Grants.gov HTTP**, eligibility filter, SQLite persistence, ship-gate regex, spend-ledger SQLite per model call |
| `lifespan` | `create_all`, demo seed, orphan recovery (startup only) |

Measured with a 1 ms heartbeat on the same loop while the stubbed run executes:

| Scenario | Longest loop stall |
|---|---|
| demo catalog, 6-chunk KB | 81 ms |
| seed catalog (52 rows) | 229 ms |
| demo catalog, 80-chunk KB | 145 ms |

With stubs the whole run is one stall, because a stub `invoke_async` never yields.
With live Bedrock the loop yields during model calls, but discovery (including live
Grants.gov HTTP with a 15 s timeout per request) and persistence still block every
other request on the process, including `/health`.

## 4. Offline dry-run profile

Medians of 7 runs; each scenario is a separate process. `run_once` excludes setup.

| Metric | demo | seed | demo_bigkb |
|---|---|---|---|
| Scanned / filtered / judged / surfaced | 5/2/3/1 | 52/16/25/3 | 5/2/3/1 |
| `run_once` wall time | 42 ms | 193 ms | 97 ms |
| setup (repo, profile, forms) | 40 ms | 39 ms | 49 ms |
| discover | 0.5 ms | 4.1 ms | 0.5 ms |
| filter_eligibility | 0.1 ms | 0.9 ms | 0.1 ms |
| resolve_founder_answers | 1.8 ms | 3.7 ms | 1.7 ms |
| assess_fit (all) | 2.0 ms | 7.6 ms | 2.8 ms |
| persist_questions | 0 | 20 ms | 0 |
| draft_and_audit | 8.3 ms | none drafted | 60 ms |
| persistence + orchestration | 30 ms | 156 ms | 32 ms |
| Peak RSS | 116 MB | 120 MB | 120 MB |
| tracemalloc peak | 4.8 MB | 6.0 MB | 6.5 MB |

Peak RSS is dominated by imports (Strands, SQLAlchemy, pydantic); the run's own
Python allocation peak is under 7 MB.

### Model prompt payloads (user message bytes; tokens estimated as bytes / 4)

| Agent | demo | seed | demo_bigkb |
|---|---|---|---|
| Assessor | 3 calls, 3,973 B total, 1,369 max | 25 calls, 39,982 B (~10.0k tok), 2,057 max | 3 calls, 3,973 B |
| Drafter | 1 call, 2,778 B | 0 calls | 1 call, **43,027 B (~10.8k tok)** |
| Auditor | 0 calls (nothing generated) | 0 calls | 1 call, **42,680 B (~10.7k tok)** |

Drafter and Auditor prompts grow linearly with the whole knowledge base: 80 chunks is
~40 KB each, of which a 14-field form needs a small fraction. Assessor prompts do not
contain the knowledge base.

### Pydantic serialization (last run)

| | demo | seed | demo_bigkb |
|---|---|---|---|
| `model_dump_json` calls / bytes | 9 / 14.6 KB | 82 / 122 KB | 9 / 69.5 KB |
| `model_validate_json` calls / bytes | 1 / 2.0 KB | 1 / 2.0 KB | 1 / 2.0 KB |
| Largest by bytes | Draft 5.7 KB | Opportunity 91 KB (52 rows) | FounderProfile 48.7 KB |

Every dump is followed by `redact_json` (a regex pass over the full string).

### SQLite (last run)

| | demo | seed | demo_bigkb |
|---|---|---|---|
| Queries | 36 | 190 | 36 |
| Total query time | 2.0 ms | 14.1 ms | 2.5 ms |

Seed breakdown: 52 `SELECT` + 52 `INSERT` on `opportunities` (one session and commit
each), 43 `SELECT` on `inbox` (`has_surfaced` per assessment plus the pre-insert check),
18 inbox inserts, 10 + 10 on `eligibility_questions`. Demo: 14 `SELECT` on `answers`,
one `recall` per form field, each loading every answer row for the founder.
Slowest single statements are inserts at 0.15 to 0.5 ms; the per-commit transaction
overhead, not query time, dominates the 156 ms persistence stage (fsync per commit).

## 5. API request path

`scripts/bench/profile_api_path.py`, TestClient, stubbed sub-agents, demo catalog.

| Request | Latency | SQL per request |
|---|---|---|
| `POST /runs` (202, includes the in-loop run under TestClient) | 38 ms | 45 |
| `GET /jobs/{id}` (poll) | 1.0 ms | 2 |
| `GET /inbox` | 1.1 ms | 2 (+1 per item for deadline filter) |
| `GET /runs?limit=50` | 0.9 ms | 1 |
| `GET /runs/latest/skips` | 0.9 ms | 1 |
| `GET /drafts` | 0.9 ms | 1 |
| `POST /runs` idempotent replay (200) | 1.1 ms | 2 |

Polling is cheap. `GET /inbox` issues one `get_opportunity` per item (N+1).

## 6. Code complexity

| File | Lines |
|---|---|
| `api/main.py` | 2,064 (34 routes, middleware, models, helpers in one module) |
| `api/repository.py` | 1,634 (`Session(self.engine)` opened 51 times) |
| `api/jobs.py` | 345 |
| `scripts/run_scout.py` | 220 |

Duplicated logic: `build_sources` exists in `api/jobs.py` and `scripts/run_scout.py`
(they differ in Grants.gov keywords and the live campus flag); `load_forms` exists in
both files and in `scripts/form_coverage.py`. The rate-limit call with
`scope="authenticated_write"` is repeated in 13 routes.

## 7. Ranked improvements

Ranked by measured impact first, then risk, then safety impact.

| # | Improvement | Measured impact | Risk | Safety impact |
|---|---|---|---|---|
| 1 | Evidence pack for Drafter and Auditor prompts; ship gate keeps the full KB | ~40 KB to a few KB per call at 80 chunks; linear in KB size, so the largest token and latency lever for drafting | Medium: recall of relevant chunks must be tested | Neutral if the ship gate still uses the full KB, which is the stated design |
| 2 | Bounded-concurrent assessments with budget reservation | Assessments are the bulk of model wall time (25 sequential calls per seed run); stubs cannot show model latency, so benchmark with a delayed stub | Medium to high: budget races | Must preserve halt-and-surface-nothing; naive `gather` overspends |
| 3 | Move sync discovery and persistence off the event loop (`to_thread`) | Loop stalls of 81 to 229 ms offline, up to network timeouts with live Grants.gov | Medium: SQLite thread use, cancellation | Neutral |
| 4 | Batch run persistence into one transaction | 156 ms of 193 ms `run_once` in the seed scenario | Low to medium: a failed row today becomes a note, not a halt | Must keep per-row failure-to-note semantics |
| 5 | Stage and prompt observability | Enables the above; no runtime cost | Low | Positive |
| 6 | Split `api/main.py`, dedupe source and form loaders, repository helpers | ~no runtime effect; large maintainability effect | Low with characterization tests | Neutral |
| 7 | N+1 in `GET /inbox`, per-field `recall` loads all answers | ~1 ms at current sizes | Low | Neutral |

Items 3 and 4 are recorded here and deferred in this change set: they alter
transaction and threading behavior and deserve their own review.
