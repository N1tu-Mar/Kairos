# Production configuration and scaling plan

Status: decisions open. Nothing in this document has been implemented except
the configuration-contract items in "Done in this change". The migration path
is a proposal.

## Decision points (need an owner's answer)

These three choices determine the architecture. None of them is made here.

### 1. Single founder, or many founders on a schedule?

| | Single founder | Multi-founder scheduled |
|---|---|---|
| Today | Yes. Terraform's `founder_id` and `KAIROS_SCHEDULER_FOUNDER_ID` name one founder; EventBridge calls one URL. | Not supported by the scheduler. The API already authorizes many founders. |
| Needs | Nothing new. | A scheduler that enumerates founders, a per-founder schedule or a fan-out job, and per-founder spend accounting (the daily cap is global today). |
| Risk if assumed wrong | Nightly runs silently cover one founder. | Building a fan-out nobody needs. |

### 2. Which production store?

| | SQLite on EFS (today) | Postgres (RDS/Aurora) | DynamoDB |
|---|---|---|---|
| Writers | One | Many | Many |
| Fit with `Repository` | Current | Close: the tables are key + indexed columns + JSON payload; Alembic migrations carry over | Close by design (the payload shape was chosen for it), but every compare-and-swap and unique index becomes a conditional write |
| Spend ledger, leases | SQLite files in `KAIROS_STATE_DIR` | Tables with row locks | Conditional writes with TTL |
| Operational cost | Lowest | Moderate | Low at small scale |
| Migration effort | None | Medium | Medium to high (rewrite transactional methods) |

### 3. Jobs in the API process, or dedicated workers?

| | In-process (today) | Queue + workers |
|---|---|---|
| Execution | `LocalJobExecutor` runs `execute_job` as an asyncio task in the API | API enqueues a job id; a worker process claims it and calls the same `execute_job` |
| Blast radius | A run's sync work stalls API requests (measured 81 to 229 ms offline, see `docs/perf/baseline.md`) | API latency independent of runs |
| Cancellation | In-process `task.cancel()` | Needs a cancel flag the worker polls, or a message |
| Scaling | One task, fixed | API and workers scale separately |
| Prerequisite | None | A multi-writer store (decision 2) |

Decision 3 depends on decision 2: workers need a store more than one process
can write.

## Done in this change (safe without choosing a database)

1. **One typed configuration contract.** `agent/deploy_contract.py` lists every
   variable with its surface, default, secrecy, production requirement and the
   exact Terraform expression. `docs/ops/configuration.md` is generated from it.
2. **Dead configuration removed.** Terraform's `allowed_frontend_origin` was
   read by nothing. It was removed rather than wired to CORS, because
   `infra/README.md` records that CORS origins are code on purpose: an
   env-driven origin list is how a wildcard reaches production.
3. **Drift tests.** `tests/test_deploy_contract.py` parses `agent/config.py`,
   the task definition in `infra/main.tf`, `infra/variables.tf`, the dashboard
   sources, `.env.example` and the generated doc. It fails on an unlisted
   variable, a Python default that differs from Terraform's, a secret in plain
   task environment, a public secret, or an unused Terraform variable.
4. **Single-task limitation made explicit.** `scripts/preflight.py` reports a
   `topology` check (a warning in production) whenever the database is SQLite,
   and the API logs the same warning at startup in production.

Unchanged: production still refuses `local_shared` auth, Playwright, and open
APIs; the scheduler credential still triggers runs for exactly one founder;
secrets still arrive only through Secrets Manager.

Not changed, on purpose: `/ready` keeps its response body. Adding a topology
field there would describe the deployment to unauthenticated callers, which
the endpoint is designed never to do.

## Proposed migration path (not implemented)

Each step ships separately and keeps `desired_count = 1` until step 4.

1. **Decide 1 and 2.** Everything below assumes Postgres; DynamoDB follows the
   same order with conditional writes instead of transactions.
2. **Second `Repository` implementation.** A `PostgresRepository` behind the
   existing protocol, with the Alembic migrations run against Postgres in CI.
   Characterization tests from `tests/test_refactor_contracts.py` and the API
   suite run against both implementations.
3. **Move process-local state into the store.** The spend ledger
   (`DailyLedger`), run leases (`RunLock`) and the scheduler failure log move
   from SQLite files under `KAIROS_STATE_DIR` into tables, keeping their
   atomicity: ledger increment in one transaction, lease take-over with a
   conditional update. `KAIROS_STATE_DIR` then holds nothing shared.
4. **Cut over storage.** Point `KAIROS_DB_URL` at Postgres, keep one task, run
   for a release. Only then allow `desired_count > 1` for the API.
5. **Queue-backed executor.** Implement the existing `JobExecutor` protocol with
   SQS: `submit` enqueues the job id, a worker service (same image, different
   command) claims it, takes the same lease and calls `execute_job`. `cancel`
   writes a cancellation flag the run checks at its await points. Orphan
   recovery moves from API startup to a visibility-timeout sweep.
6. **Scheduler fan-out** (only if decision 1 is multi-founder). EventBridge calls
   one internal endpoint that enqueues one job per eligible founder, each with
   its own idempotency key and per-founder spend accounting.
