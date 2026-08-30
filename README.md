# Kairos

**Tell us about your startup once. We watch for the money and handle the paperwork.**

> *καιρός* — the opportune moment. Greek separated **chronos**, clock time
> that just elapses, from **kairos**, the window that opens and shuts. Lysippos
> sculpted him with winged feet, a forelock in front and bald behind — you can
> catch him coming toward you, and there is nothing to grab once he has passed.
> A funding deadline behaves exactly that way.

An agent that watches for non-dilutive funding a student founder is actually
eligible for, decides which ones are worth their time, drafts most of the
application from what it already knows, and interrupts them only for the
handful of things it genuinely can't answer.

It is not a grant search engine, an AI grant writer, or a chat interface over
Grants.gov. Those exist. The thing that doesn't is **the loop running while
the founder is asleep**.

> **Status: working locally, not production-ready.** The agent loop,
> deterministic safety layer, SQLite persistence, FastAPI surface and Next.js
> dashboard are implemented. The catalog holds 59 curated rows, 52 verified
> quote-by-quote against their live pages; three real application forms are
> modelled, two of them partial; discovery scores 85.7% recall against a
> hand-authored 20-program benchmark. The AWS deployment is defined in
> Terraform but has not been applied, and no model path has been validated
> against live Bedrock. See
> [Current implementation](#current-implementation) and
> [Honest limitations](#honest-limitations).

---

## Who it's for

Undergraduate and first-time student founders, student research and
commercialization teams, and student-led social ventures looking for
$2K–$50K without giving up equity.

The money exists. Finding it is the barrier: campus competitions, student
innovation funds, fellowships and cash prizes have no API, no aggregator, and
no deadline reminders. The 45-minute search and the 3-hour application happen
in a week that already has a problem set due.

---

## Current implementation

| Area | What exists today |
|---|---|
| Discovery | Three sources. A curated seed catalog of **59 rows, 52 of them verified quote-by-quote against their live pages**; live Grants.gov `search2`/`fetchOpportunity` with pagination, profile-aware keywords, client-side `since` filtering and per-page failure reporting; and an optional campus source behind `KAIROS_ENABLE_BROWSER` that loads only human-`ACCEPTED` scraped rows. |
| Campus research | An operator-run, robots-aware Rutgers scraper with rate limiting, raw-page archives, deterministic extraction, exact evidence spans, explicit `UNKNOWN` fields, deduplication and stale-deadline warnings. Its seven rows have now been audited against their live pages: 1 accepted and promoted, 3 rejected with reasons, 3 still `NEEDS_HUMAN_REVIEW`. |
| Measurement | A 20-program discovery-recall benchmark with hand-authored ground truth and 6 deliberate negatives: **85.7% retrieval recall, 83.3% eligibility coverage at 100% precision, 0 wrong deadlines.** Separate from the drafting golden set. |
| Application forms | Three real forms transcribed with verbatim labels, source URLs and retrieval dates; two are marked `complete: false` because their pages publish only part of the application. Protected certification, disclosure and terms fields are proven unfillable by test. |
| Freshness | `scripts/reverify.py` refetches stale rows and writes a review diff. It never edits a curated fact — dead, redirected, expired and evidence-lost rows are reported for a person. |
| Decision loop | Deterministic eligibility filtering, Assessor/Drafter/Auditor sub-agents, value-per-hour escalation, top-three surfacing, idempotency, token/assessment/daily-spend caps and a fail-closed ship gate. |
| Product surfaces | SQLite persistence behind versioned migrations, a FastAPI API with per-founder authorization, and a Next.js dashboard for briefings, inbox state, runs, drafts and profile editing. |
| Run execution | A run is a durable job, not a held-open connection: `POST /founders/{id}/runs` returns 202 with a job id and the dashboard polls. A run lease keyed by founder makes two overlapping runs impossible; a crash cannot leave one "running" forever. |
| Operations | A Docker image running as a non-root user, versioned Alembic migrations, a preflight check, GitHub Actions CI, and Terraform for ALB + one-task ECS Fargate + EFS + EventBridge Scheduler with alarms and a dead-letter queue. **The Terraform is unapplied and has never been planned.** |
| Verification | 843 Python tests pass with no expected-failures remaining; 55 frontend tests, TypeScript checking, ESLint and the production build pass locally as of 2026-08-27. The published golden-set result is fixture-based, not a live-model score, and nothing here has ever called Bedrock. |

The research scraper and the Scout runtime are deliberately separate. A
scraped row cannot become a recommendation merely because a parser found it:
a person must inspect its evidence, promote it into the curated candidate
catalog, and run the verifier first.

---

## What a run looks like

```
Scanned 214. Discarded 198. Judged 16. Surfaced 3.
```

Every one of those 198 discards has a reason you can read. From an actual
`--dry-run` against the synthetic catalog:

```
REJECTED by the deterministic filter (no model involved):
  [DEMO] Doctoral Commercialization Award
    DEGREE_LEVEL: open to phd, postdoc only — you: undergrad / needs: phd/postdoc
  [DEMO] Campus Accelerator Cohort
    EQUITY: this funder takes equity — you: non-dilutive only / needs: equity accepted

JUDGED then held back:
  [DEMO] Student Venture Prize — needs ~12h, founder's ceiling is 8h
  [DEMO] Undergraduate Research Grant — award $1,500 is below your floor $2,000

SURFACED:
  (APPLY) [DEMO] Campus Innovation Fund · up to $10,000 · 54 days left · ~5h of work
```

Those four counters are the product. **The agent's judgment is measured by
what it throws away silently**, so the number that matters most is the second
one. Every discard is recorded with the exact check that fired, and one
request shows you all of them:

```
GET /founders/{id}/runs/latest/skips
```

If a run clears nothing, it sends nothing. Silence is a valid output and the
counters still record the work.

---

## The architecture decision that matters

**Models perceive. Python decides.**

```
messy input → [ingestion boundary] → [LLM] → structured facts → [pure Python] → what you see
```

A fully successful prompt injection inside an opportunity description can
make the Assessor say whatever the attacker wants. It still cannot:

- change a Python comparison against a structured eligibility field,
- spend past the run's token ceiling,
- get an ungrounded number through the ship gate,
- or cause a second notification about the same opportunity.

Everything that must not vary run-to-run lives in code, outside the model,
where no phrasing can reach it. See [`docs/architecture.md`](docs/architecture.md)
for the diagrams, including the trust boundary.

### Three sub-agents, three failure modes

Each is a Strands `Agent` wrapped in an `@tool`, with its own system prompt
and its own model configuration — a real architectural split, not decoration.

| Sub-agent | Temperature | Sees | Fails by |
|---|---|---|---|
| **Assessor** | 0 | one opportunity, the profile, the filter's output | judging fit badly |
| **Drafter** | > 0 | the form, the knowledge base, the opportunity | inventing facts |
| **Auditor** | 0 | the finished draft and the knowledge base, **nothing else** | missing an invention |

The Auditor never sees the Drafter's prompt, reasoning, or provenance claims.
An auditor that inherits the drafter's context inherits its mistakes. When
they disagree, the Drafter loses and the field goes back to the founder.

---

## Not inventing things onto a real funding application

A student who submits an application containing numbers an agent made up has
misrepresented themselves to a funder. That is a liability class, not a bug
class, so it gets more than a careful prompt.

**Nothing ships without passing one gate**, in this order, failing closed:

| # | Check | Catches |
|---|---|---|
| 1 | Blocklist | certification, signature, disclosure, tax and payment fields — forced to *you answer this*, even when the answer is known |
| 2 | Provenance | a generated field with no source span |
| 3 | Numeric whitelist | "we have 400 users" when the deck says 40 |
| 4 | Entity check | a named advisor, partner or institution that isn't in the knowledge base |
| 5 | Closed world | naming a funding program that wasn't in this run's retrieved set |
| 6 | Forbidden claims | invented incorporation, prior funding, credentials, patents |
| 7 | Auditor verdict | anything the independent pass couldn't support |
| 8 | Completeness | a field that is silently blank rather than explicitly flagged |

If the gate itself throws, the draft is `BLOCKED`. An exception in the safety
layer is never read as a pass.

Three more things fall out of the same principle:

- **`UNKNOWN` is a real value.** An eligibility rule the source page doesn't
  state is not permission. It becomes a question — *"this program's page
  doesn't say whether undergrads qualify; worth a two-minute email before you
  spend four hours"* — rather than a confident guess in either direction.
- **Abstention is a correct answer.** Every sub-agent has a legitimate way
  out, and the orchestrator handles each one as an outcome rather than an
  error.
- **Cold start disables drafting.** Below the knowledge-base floor the agent
  still finds and judges opportunities, but writes no prose it can't ground.
  A sparse profile produces *more* "needs you" fields, never more invention —
  and watching that count fall as the profile grows is the product.

---

## The agent does not submit anything

Grants.gov organizational submissions legally require an Authorized
Organization Representative. The agent prepares, validates, and stops.

That is a real legal constraint, not a confirmation dialog added for safety
theater. The agent may search, filter, assess, draft, audit, store to its own
memory, and notify the founder. It may never submit an application, email a
third party, register an account, upload a document, or accept terms.

---

## Setup

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/).

```bash
git clone <this repo> && cd kairos
uv sync

cp .env.example .env
```

Bedrock model IDs are region-specific and versioned, so `.env` ships empty
and the app **raises on startup until you fill it in**. Discover them:

```bash
aws bedrock list-foundation-models --region us-east-1 \
  --query 'modelSummaries[?contains(modelId, `anthropic`)].modelId' --output table
```

Paste the Sonnet-class ID into `BEDROCK_MODEL_REASONING` and the Haiku-class
one into `BEDROCK_MODEL_CLASSIFY`. If an invoke returns a
`ValidationException` about on-demand throughput, the model needs an
inference profile — run `aws bedrock list-inference-profiles` and use that ID
instead.

### Run it

```bash
# the full test suite — no AWS account, no network, ~1 second
uv run pytest

# the whole pipeline with no AWS account at all: discovery, the deterministic
# filter, ranking, the escalation policy, idempotency and the ship gate all
# run for real; only the judgment is stubbed, and every line it prints says so
uv run python scripts/run_scout.py --dry-run --demo --no-grants-gov

# one real Bedrock call per tier — run this before anything else that
# costs money. It proves the model IDs resolve in your region and that token
# accounting is actually wired to the wallet.
uv run python scripts/smoke_bedrock.py --tier classify

# one run against the synthetic catalog (needs Bedrock)
uv run python scripts/run_scout.py --demo --no-grants-gov

# a real run, live Grants.gov included
uv run python scripts/run_scout.py

# a local daily schedule, standing in for EventBridge
uv run python scripts/run_scout.py --schedule --hour 6

# the Section 11.11 golden set — 15 drafts, 8 with traps, prints the number
uv run python scripts/run_eval.py

# search-backed scraper lanes. Both use the same BRAVE_SEARCH_API_KEY or
# KAIROS_SEARCH_API_KEY. They write review candidates, not seed rows.
uv run python scripts/run_web_scraper.py --lane university
uv run python scripts/run_web_scraper.py --lane general
uv run python scripts/run_web_scraper.py --lane both --out-dir data

# opt-in paid fallback: local robots-aware HTTP first, then Firecrawl only for
# JavaScript shells or unusably thin HTTP 200 pages. Five paid pages maximum by
# default across both lanes; override the cap deliberately when needed.
uv run python scripts/run_web_scraper.py --lane both --out-dir data --firecrawl
uv run python scripts/run_web_scraper.py --lane general --firecrawl --max-firecrawl-pages 2

# the API
uv run fastapi dev api/main.py

# the dashboard (see frontend/README.md; needs the API running).
# `npm ci`, not `npm install` — it installs exactly the lockfile. Re-run it
# after any pull that touches frontend/package-lock.json; the Next 15 -> 16
# move on 2026-08-27 rewrote most of it. Node 20.9+ (CI builds on 22).
#
# If you change package.json, do NOT commit the lockfile `npm install` leaves
# behind: on macOS it prunes the linux-only optional dependencies that
# `npm ci` then demands on the runner. Regenerate it for CI instead —
#   uv run python scripts/check_lockfiles.py --fix
# See docs/runbooks.md §13.
cd frontend && npm ci && npm run dev
```

Firecrawl is backend-only and opt-in. Put `FIRECRAWL_API_KEY` in the root
`.env`; the CLI exits before Brave search or output writes when the key is
missing. `--firecrawl` cannot be combined with `--allow-js`. The fallback is
shared and capped across both lanes, never bypasses robots or network-safety
failures, and archives the exact extraction markdown, raw HTML, local fetch
decision, and provider metadata under the configured raw-data directory.

`KAIROS_API_TOKEN` in `.env` is empty by default and the API runs open on
localhost, logging that fact at startup. Set it (both sides — backend `.env`
and `frontend/.env.local`) before exposing the API to anything.

### Curating the catalog

Seed rows are **generated, not written**. Add a candidate to
`data/opportunities.candidates.json` after opening the page, then:

```bash
uv run python scripts/verify_seed.py
```

It fetches every `source_url`, checks the page exists and actually mentions
the program, **and re-finds every quote the row carries on the page that
quote cites** — programs state eligibility on FAQ and rules sub-pages, so
each quote is checked where it claims to come from. A quote that cannot be
found fails the row, whether it was fabricated, paraphrased, or simply
outlived the page. Evidence citing a page outside the funder's own site is
refused rather than blessed. It writes `data/opportunities.seed.json` with an
honest `verified` flag; rows that fail are excluded from runs. See
[`data/README.md`](data/README.md) for why, including a worked example of a
URL that looks exactly right and 404s.

Batches of researched rows are folded in with:

```bash
uv run python scripts/merge_candidates.py <batch.json> --dry-run
```

which validates each row against the real `Opportunity` model, deduplicates
on id and URL, refuses rows a researcher marked unreachable, and flags stale
deadlines. It writes candidates only — verification is still a separate step.

Three more operator commands matter:

```bash
# which rows have a usable application form, and which cannot be drafted
uv run python scripts/form_coverage.py

# refetch stale rows and write a review diff. Changes nothing.
uv run python scripts/reverify.py --max-age-days 30

# score the catalog against the version-controlled reference set. Offline.
uv run python scripts/run_discovery_benchmark.py
```

`reverify.py` is the answer to "a reachable page is not proof the stored
deadline is still right." It classifies each stale row as DEAD, REDIRECTED,
TITLE_GONE, EVIDENCE_LOST, DEADLINE_PASSED or UNCHANGED and writes
`data/reverification.report.json`. It never edits a curated fact, never
flips `verified`, and says so in its own output — a script that silently
rewrites curation has replaced the human with a parser.

For the Rutgers target set, the repository also has a research stage that
creates review material without touching the production seed:

```bash
# fixed targets, static HTML, plus a human-readable review document
uv run python scripts/scrape_rutgers.py --doc

# optional, bounded expansion: Rutgers-domain funding links, one level deep
uv run python scripts/scrape_rutgers.py --discover --doc

# opt into Playwright only for a target already known to return a JS shell
uv sync --extra js
uv run playwright install chromium
uv run python scripts/scrape_rutgers.py --allow-js --doc
```

The output is `data/opportunities.rutgers.candidates.json`; raw fetch metadata
is archived under `data/raw/`, and the readable artifact is
[`docs/rutgers-funding-review.md`](docs/rutgers-funding-review.md). Every row
starts as `NEEDS_HUMAN_REVIEW`. Promotion into
`data/opportunities.candidates.json` is a manual decision, followed by
`verify_seed.py`.

Those seven rows have since been audited row by row against their live pages;
the pass/fail/needs-follow-up artifact is
[`docs/rutgers-candidates-audit.md`](docs/rutgers-candidates-audit.md). The
audit corrected two extraction defects with primary evidence — an award
minimum read off a winners table that also listed smaller prizes, and a prize
*pool* recorded as one team's award — and rejected three programs a founder
cannot actually apply to. Setting `KAIROS_ENABLE_BROWSER=true` makes the
`ACCEPTED` rows visible to a run:

```bash
KAIROS_ENABLE_BROWSER=true uv run python scripts/run_scout.py --dry-run --no-grants-gov

# and, only with the flag already on, a live sweep during the run. What it
# collects lands as NEEDS_HUMAN_REVIEW and cannot affect this run.
KAIROS_ENABLE_BROWSER=true uv run python scripts/run_scout.py --campus-scrape
```

---

## Testing

```bash
uv run pytest -q

cd frontend
npm test
npm run typecheck
npm run lint
```

Everything runs offline. Live API responses are recorded as fixtures in
`tests/fixtures/`, so the suite never depends on Grants.gov being up and
never spends a token.

Current local result (2026-08-27): **843 Python tests passed, no xfail
remaining; 55 frontend tests passed; typecheck, lint and the production build
passed.** The three previously-planned behaviours — semantic recall and the
two scheduler/overlap ones — are implemented, and their tests converted from
`xfail` rather than being rewritten to match whatever got built.

Migrations have their own 20 tests, run against a fresh database *and* a
representative existing one built the way every deployed database was built,
with live rows that have to survive adoption.

Everything above is offline. It says nothing about AWS, which nothing here
has ever touched.

The tests that matter most are the adversarial ones. `tests/test_grounding.py`
holds all six cases the spec requires, including an injected instruction inside
an opportunity description asserting that the deterministic filter's result is
unchanged. Three suites extend that:

- `tests/test_adversarial.py` — 35 cases covering what happens when the
  *model* misbehaves rather than the input: fabricated citations, a real
  citation that supports nothing, malformed structured output, abstention,
  safety-layer exceptions failing closed, and partial usage at budget
  crossings. Verified by mutation — disabling a check fails specific tests —
  because a suite that passes against broken code is not testing anything.
- `tests/test_negation_grounding.py` — the 27-case polarity matrix, written
  before the fix it validates.
- `tests/test_spelled_numbers.py` — 32 cases pinning that "four hundred users"
  is checked exactly as "400 users" is.

---

---

## The number

Section 11.11 asks for a golden set and says to publish the result, whatever it
is. This is the result.

```
15 cases, 8 with traps · 20 fields scored · 8 shipped · 5 drafts blocked

groundedness             100.0%  (8/8 shipped claims supported)
abstention accuracy      100.0%  (11/11 unsupported claims withheld)
unnecessary questions    11.1%   (1/9 supported claims withheld anyway)
  of those, collateral   100%    (blocked by another field in the same draft)
```

**This measures the deterministic defense layer, not the model.** The offline
run replays a fixture Drafter proposal per case through the real
`draft_application`, `audit_draft` and `ship_gate`, so it answers "given a model
that says X, what reaches the application?" `run_eval.py --live` puts a real
Bedrock Drafter and Auditor in front of the same cases and the same scorer, and
that is the number for the whole system — it is not in this README yet because
[nothing here has run against live Bedrock](#honest-limitations).

Ground truth is declared by hand per field in
[`tests/golden_set/cases/`](tests/golden_set/cases), and the scorer imports
nothing from `guardrails`. A scorer that asks the gate whether the gate was
right is marking its own homework.

### What it found on its first run, and what happened next

The first run scored **80% groundedness** and leaked two claims, both the same
cause: **the forbidden-claims evidence check could not tell a statement from
its negation.** `agent/guardrails.py` paired a trigger regex with an evidence
regex and treated a match anywhere in the knowledge base as support.

- A draft claiming *"we work closely with a faculty advisor"* was supported by
  the deck line *"there is no faculty advisor"* — the evidence pattern matched
  the negation.
- A draft claiming *"incorporated as a Delaware C-Corporation"* was supported
  by *"No legal entity has been formed"* — same shape.

Both are claims Section 10.2 names as never-invent. Both shipped.

They were left unfixed at first, deliberately. The obvious patch — reject an
evidence match sitting near a negation marker — would have made exactly these
two cases pass, and tuning a check until it satisfies the eval that measures it
is how an eval stops meaning anything.

So the order was: adversarial cases first, written without this scoreboard in
view ([`tests/test_negation_grounding.py`](tests/test_negation_grounding.py),
27 cases across every forbidden-claim category, both polarities, mixed
evidence, punctuation and contractions), then the fix against them.

`evidence_supports_claim` is polarity-aware rather than proximity-based:
evidence splits into clauses at sentence punctuation, commas and contrast
conjunctions, each clause carries a negation polarity, and a match supports a
claim only at the same polarity. The comma boundary is what keeps *"no revenue
yet, but 40 users"* from blocking the supported half — the false positive
[`DECISIONS.md`](DECISIONS.md) predicted, now a test of its own.

Both traps block at `FORBIDDEN_CLAIMS`, no clean case regressed, and the
numbers above are the post-fix figures. **The 80% is kept in this README on
purpose**: an eval whose bad result quietly disappears once it is fixed is an
eval nobody can audit.

---

## Honest limitations

Written before the deadline pressure, so it stays honest.

- **The catalog is 59 rows, one short of the promised 60–100.** 52 are verified: the
  page was fetched, it still mentions the program, and every quote the row
  carries was re-found on the page that quote cites. The other 7 are
  deliberately retained failures — one URL that 404s, three hosts that refuse
  automated clients, two JavaScript-rendered sites a static verifier cannot
  confirm, and one row whose rules live on a different domain. They carry
  `verified: false` and are excluded from runs by `SeedCatalog`, which counts
  what it skipped in a `seed_catalog_excluded_unverified` log line. Three
  research sweeps died on an API rate limit; two were re-run and one is still
  outstanding, which is the whole reason the count is 59 rather than higher:
  the gap is unfinished research, not a rejected shortcut. Demo runs still
  use the obviously-synthetic catalog: `[DEMO]` in every title, every URL on
  `.invalid`.
- **Campus discovery is wired in, behind a flag, and still gated on a human.**
  `KAIROS_ENABLE_BROWSER` now adds a real source to a Scout run — verified
  end to end, a dry run scans 6 instead of 5 with it on. What it adds is only
  rows a person marked `ACCEPTED`; `NEEDS_HUMAN_REVIEW` and `REJECTED` rows
  stay invisible to the runtime. A live sweep during a run needs a second
  opt-in (`--campus-scrape`) and cannot feed the run that triggered it,
  because everything it writes is unreviewed by construction.
- **Only three real application forms exist, and two are partial.** NJIT's
  four questions are the whole initial submission. MIT CEP's seven sections
  and TCNJ's fourteen components come from pages that publish the shape of
  the application but not the questions inside it; both are marked
  `complete: false` with a note saying what is missing. No login was used and
  no portal was opened. 2 of 59 catalog rows can therefore be drafted;
  `scripts/form_coverage.py` prints the gap.
- **Structured eligibility is still mostly UNKNOWN on live sources.**
  Grants.gov states eligibility in prose, and prose is not a structured
  fact. `agent/tools/extraction.py` is the boundary that lets prose become
  structure safely — every field needs a verbatim span re-found in the
  source — but nothing model-driven runs through it yet. The benchmark's
  83.3% eligibility coverage is curation, not extraction.
- **The AWS deployment is written, not applied.** `infra/` holds Terraform
  for ECS Fargate, EFS-backed SQLite, an EventBridge schedule, five alarms
  and a dead-letter queue. It has never met a live account — never applied,
  never planned, and `fmt`/`validate` have not been run because there is no
  Terraform binary in the authoring environment. There is no demo link and no
  claim that any of it works first try. `infra/README.md` and
  `docs/runbooks.md` both mark every procedure LOCAL or WRITTEN, and most are
  WRITTEN.
- **Identity has a seam, not a provider.** `api/auth.py` supports two modes:
  one shared bearer token (the documented single-founder demo, honest that a
  shared secret proves only that somebody holds it) and a hashed credential
  file mapping distinct tokens to distinct founders, with revocation, expiry
  and rotation that takes effect without a restart. Every founder-scoped
  endpoint authorizes, the three resource-id-only routes look up their
  owner, and a refusal is a 404 with the same wording a missing resource
  gets — a 403 confirms the id exists, which is an enumeration primitive.
  What is *not* here is a real identity provider: OIDC/JWT is a product
  decision, so the `Authenticator` protocol is built and the adapter is
  documented rather than faked.
- **The dollar cap is only real once prices are.** At the default price of
  zero every call costs $0.00, so `KAIROS_DAILY_USD_CAP` can never trip and
  only the per-run token ceiling does anything. `/ready` reports that as
  `spend_cap: unenforceable`, production Terraform refuses to plan around it,
  and `RunBudget` refuses to start rather than pretend — but none of those
  can supply the number. There is deliberately no default price table
  anywhere in this repository, because a stale guess under-counts spend
  against a real cap.
- **Cross-run memory is the database, not AgentCore Memory.** The session
  manager the spec calls for does not exist in the installed SDK — see
  [`DECISIONS.md`](DECISIONS.md) D1.
- **`recall` now matches meaning as well as text**, through a tested
  similarity threshold in `agent/semantic.py`. Exact-after-normalisation is
  still tried first — it is the highest-confidence path — and the semantic
  tier only runs when that finds nothing.
- **The grounding checks are regex and set membership, not semantics.** They
  produce false positives. Every false positive pushes a field to *you answer
  this*, which is the safe direction — one extra question beats one invented
  fact — but it is not free.
- **The groundedness number covers the defense layer, not the model.** See
  [The number](#the-number). The live figure needs Bedrock and does not exist
  yet.
- **The two known leaks are fixed; the check that fixed them is still
  lexical.** `evidence_supports_claim` reads clause polarity, not meaning, so
  it will misjudge sentences whose negation is carried by structure rather
  than by a marker word ("far from settled", "we would have an advisor if").
  Every such misjudgment pushes a field to *you answer this*, which is the
  safe direction. Reproduced by `uv run python scripts/run_eval.py`, written
  up in [`tests/golden_set/README.md`](tests/golden_set/README.md), and pinned
  by `tests/test_golden_set.py` so a new leak fails the build.
- **Nothing here has run against live Bedrock yet.** Every model path is
  exercised by fakes. The suite passing tells you the orchestration is
  correct, not that the model IDs in your `.env` resolve or that the models
  are enabled on your account. `scripts/smoke_bedrock.py` is the first thing
  to run once credentials exist, and it is deliberately loud about the two
  failures that look like a broken build: model access not enabled, and a
  model that needs an inference profile.
- **Bedrock prices default to zero** in `.env`, so cost estimates read
  `$0.0000` until someone fills in live pricing. Visibly wrong beats quietly
  wrong, and the token ceiling enforces on raw counts regardless of price.

Every deviation from the spec, every API fact confirmed by running code
rather than recalling it, and every open TODO is dated in
[`DECISIONS.md`](DECISIONS.md).

---

## Repository layout

```
agent/
  models.py        Pydantic contracts. Three-valued eligibility throughout.
  guardrails.py    Escalation thresholds, the field blocklist, ship_gate().
  budget.py        Token ceiling, assessment cap, persisted daily USD cap.
  sanitize.py      The ingestion boundary for untrusted text.
  prompting.py     Prompt loading, git-blob prompt versions, structured-or-abstain.
  scout.py         The orchestrator. Deterministic run + Strands agent.
  toolset.py       The six tools, bound to one run.
  runtime.py       Per-run state.
  subagents/       Assessor, Drafter, Auditor.
  tools/           discovery.py, eligibility.py (pure Python).
  scraping/        Evidence-first campus research; never writes runtime seed.
  prompts/         System prompts as version-controlled .md.
api/
  main.py          FastAPI read surface.
  repository.py    Protocol + SQLite. DynamoDB is a port, not a rewrite.
data/              Candidates, verified seed, Rutgers review rows, demo data, forms.
scripts/           Scout runner, seed verifier, Bedrock smoke check, eval, scraper.
frontend/          Next.js dashboard. Reads the API; owns no business logic.
infra/             Terraform: ECS Fargate + EFS + EventBridge schedule. Unapplied.
tests/             Offline. Fixtures recorded from real API calls.
backend_method_suites/  Per-method pytest suites, including the auth gate.
docs/              Architecture diagrams (Mermaid source).
```

---

## License

MIT. See [LICENSE](LICENSE).
