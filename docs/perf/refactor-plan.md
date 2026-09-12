# Refactor plan: API, jobs, repository, CLI

Scope: `api/main.py`, `api/jobs.py`, `api/repository.py`, `scripts/run_scout.py`,
and the duplicated source and form loaders. Behavior-preserving only.

## Guardrails

- A characterization test pins the full OpenAPI document and route table
  (path, methods, status code, name) before anything moves. Any contract drift fails it.
- Existing monkeypatch targets keep working: `api.main.app`,
  `api.main.MAX_BODY_BYTES`, `api.main.MAX_UPLOAD_BODY_BYTES`,
  `api.jobs.settings`, `api.jobs.build_sources`, `api.jobs.load_forms`,
  `api.jobs.execute_job`, `api.jobs.new_job`, `api.jobs.LocalJobExecutor`,
  `scripts.run_scout.settings`, `scripts.run_scout.build_sources`.
- Middleware order, CORS settings, auth, 404-not-403 semantics, status codes,
  idempotency and lease handling are moved verbatim, not rewritten.
- No migrations, no queue, same single-process executor.

## Steps, one commit each

1. **Characterization tests.** OpenAPI and route snapshot; API-versus-CLI source
   factory parity per flag; form loader behavior (sorted, last duplicate wins,
   invalid JSON raises, missing directory is empty).
2. **Canonical loaders.** New `agent/catalog.py` with `load_forms()` and
   `build_sources(config, ...)`. `api/jobs.py` and `scripts/run_scout.py` become
   thin callers that pass their own `settings()` so existing patches still apply.
   `scripts/form_coverage.py` reuses `load_forms`.
3. **Split `api/main.py`.** `api/deps.py` for bounds types, `principal`, `owned`,
   rate limiting and the paid-work check; `api/schemas.py` for request and response
   models; `api/routes/` with one `APIRouter` per area (health, founders, intake,
   eligibility, runs and jobs, drafts and inbox, catalog). `api/main.py` keeps
   `app`, `lifespan`, middleware and router registration. Routes read state from
   `request.app.state` instead of the module-global `app`.
4. **Repository helpers.** Small typed helpers for "get one payload by key",
   "list payloads for a statement", and "redacted payload of a model". No generic
   ORM layer; transactional methods stay explicit.

## Deliberately deferred

- Intake compare-and-swap methods keep their bespoke SQL; they differ in guards and
  a shared helper would hide the differences that matter.
- Batching run persistence and moving sync work off the event loop change
  transaction and threading behavior; they get their own change.
