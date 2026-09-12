# Results: refactor, evidence packs, concurrent assessments, config contract

Branch `refactor`, measured 2026-09-12 against baseline `157a7a5`
(`docs/perf/baseline.md`). Offline only; no Bedrock or paid service was called.

## Validation

| Check | Baseline | Now |
|---|---|---|
| Backend `uv run pytest -q` | 1171 passed | 1277 passed |
| Frontend `npm test` | 209 passed | 208 passed (see note) |
| `npm run typecheck`, `npm run lint`, `npm run build` | pass | pass |

The frontend count differs only because the baseline ran in a checkout with an
uncommitted login-form test; no frontend file is changed on this branch.

New tests: HTTP contract and loader characterization (60), evidence packs (14),
concurrent assessments (18), deployment contract drift (14). No test was deleted.
Two test edits retarget monkeypatches to code that moved: the scraper-lane patch
in `tests/test_api.py`, and the characterization harness's `REPO_ROOT` patch.

## 1. Refactor

| File | Before | After |
|---|---|---|
| `api/main.py` | 2,064 | 268 |
| `api/deps.py` | | 170 |
| `api/schemas.py` | | 180 |
| `api/routes/*.py` (7 routers) | | 1,600 |
| `api/jobs.py` | 345 | 304 |
| `api/repository.py` | 1,634 | 1,639 |
| `scripts/run_scout.py` | 220 | 180 |
| `scripts/form_coverage.py` | 135 | 123 |
| `agent/run_inputs.py` | | 79 |
| **Total in scope** | **4,398** | **4,543** |

Total lines grew by about 3%, from per-module imports and docstrings (and 6 lines of the later topology warning in `api/main.py`). What
shrank is duplication and size per unit:

- One source factory and one form loader instead of two and three copies.
- 13 hand-written authenticated-write rate-limit blocks became one helper call.
- `Session(self.engine)` in the repository went from 51 to 36; all 25 redaction
  calls go through one `_payload` helper; 18 read methods use typed
  `_get` / `_first` / `_all` helpers.
- The largest module is now `api/repository.py`; the largest route module is
  intake at 674 lines.

The OpenAPI document and the route table are byte-for-byte identical to the
pinned fixture. Middleware order, auth, 404-not-403, status codes, idempotency
and lease handling were moved verbatim.

Intentionally deferred duplication:

- Intake compare-and-swap repository methods keep their own SQL. Their guards
  differ in ways a shared helper would hide.
- The intake routes still repeat "check pending turn, check revision, save,
  audit" five times. Collapsing it changes error ordering and deserves its own
  review.
- Batching run persistence and moving sync work off the event loop change
  transaction and threading behavior. They are ranked in the baseline and not
  done here.

## 2. Evidence packs

Medians of 7 runs with stub sub-agents (`scripts/bench/profile_dry_run.py`).

| Scenario | Agent | Prompt bytes before | After |
|---|---|---|---|
| demo catalog, 6-chunk KB | Drafter | 2,778 | 2,778 (identical) |
| seed catalog, 6-chunk KB | Assessor, 25 calls | 39,982 | 39,982 (unchanged by design) |
| demo catalog, 80-chunk KB | Drafter | 43,027 | **9,350 (−78%)** |
| demo catalog, 80-chunk KB | Auditor | 42,680 | **6,863 (−84%)** |

At about 4 bytes per token, that is roughly 10.8k to 2.3k Drafter tokens and
10.7k to 1.7k Auditor tokens per drafted application on a large knowledge base.

| Scenario | `run_once` before | After | Peak RSS before | After | Report identical |
|---|---|---|---|---|---|
| demo | 42 ms | 41 ms | 116 MB | 117 MB | yes |
| seed | 193 ms | 176 ms | 120 MB | 117 MB | yes |
| demo_bigkb | 97 ms | 100 ms | 120 MB | 119 MB | yes |

Selection costs about 7 ms of CPU per drafted application on the 80-chunk case,
which is noise next to a model call. Peak RSS is import-dominated and unchanged.
Model latency savings cannot be measured offline; they scale with input tokens.

Quality trade-offs:

- Selection is lexical. A field whose wording shares no content word with the
  chunk that answers it (a "Project name" field versus a chunk that only says
  "LabQueue") gets no evidence on a large knowledge base and goes to
  NEEDS_FOUNDER. That fails toward the founder, never toward invention.
- The Drafter may now cite only chunks it was shown. A citation to an existing
  but unshown chunk is demoted, which is stricter than before.
- Knowledge bases within 24 chunks and 16 KB are passed whole, so every current
  profile is unaffected. Embeddings were not added; there is no measured recall
  problem to justify them yet.
- The ship gate still reads the full knowledge base; a test proves a number
  supported only by an unselected chunk still passes the numeric whitelist, and
  an invented number is still blocked.

## 3. Bounded concurrent assessments

`scripts/bench/bench_assessments.py`: seed catalog, 25 assessments, stub Assessor
sleeping 200 ms per call and reporting 1,200 tokens.

| `KAIROS_ASSESSMENT_CONCURRENCY` | Wall time | Speedup |
|---|---|---|
| 1 (default, sequential path) | 5.20 s | 1.00x |
| 2 | 2.78 s | 1.87x |
| 4 | 1.57 s | 3.31x |

Reports, assessments, notes, skips, token totals and inbox order were identical
at every level. Design and residual risks: `docs/perf/assessment-concurrency.md`.
Drafting and auditing remain sequential.

## 4. Configuration contract

See `docs/ops/production-scaling-plan.md` and `docs/ops/configuration.md`.
Summary: one typed contract, 14 drift tests, the dead Terraform
`allowed_frontend_origin` removed, and a single-task topology warning in
preflight and at production startup. The database, founder-scheduling and
worker decisions are documented and left open.

## Not verified

- No live Bedrock call. Token savings are prompt-size estimates.
- Terraform was not run (`terraform` is not installed here); the variable removal
  is checked by the drift tests' parser, not by `terraform validate`.
