# DECISIONS

Every deviation from `prompt.md`, every API fact confirmed by running code
rather than recalling it, and every open question. Dated. Newest section
last within each heading.

The rule this file exists to serve: *when you don't know, stop and write a
TODO with the specific question. A confident guess that's wrong is not
useful.*

---

## Confirmed by running code

### 2026-08-22 — Strands API surface

Read from the installed package (`strands-agents 1.53.0`), not from memory.

```
strands.Agent(model, messages, tools, system_prompt, structured_output_model,
              callback_handler, ..., name=, description=, session_manager=, ...)
Agent.structured_output_async(output_model: type[T], prompt) -> T
Agent.invoke_async(prompt, *, limits: Limits | None, ...) -> AgentResult
Agent.as_tool(*, name, description, preserve_context, delegate) -> AgentTool
strands.tool(func=None, description=None, inputSchema=None, name=None, context=False)
strands.models.BedrockModel(*, region_name=None, **BedrockConfig)
  BedrockConfig includes: model_id, temperature, max_tokens, top_p,
  streaming, cache_config, guardrail_id, service_tier, strict_tools
AgentResult.metrics.accumulated_usage -> Usage TypedDict
  {inputTokens, outputTokens, totalTokens}
strands.types.agent.Limits (TypedDict): turns, output_tokens, total_tokens
```

Two behaviours confirmed by spike rather than by reading signatures:

- A `@tool`-decorated function exposes `.tool_name` and **has no
  `.original_function`**. It stays directly callable as a plain function,
  and returns an awaitable when the underlying function is `async`. This is
  what lets `run_once` and the Scout agent share one set of tool objects.
- `Agent.structured_output_async` takes **no `limits` argument**, and — the
  part that mattered and was missed — returns the parsed model rather than an
  `AgentResult`, so it exposes no metrics either. Superseded on 2026-08-26;
  see below.

### 2026-08-22 — Grants.gov schema

Answers the open TODO in `prompt.md` ("confirm whether search2 returns
closeDate as ISO or MM/DD/YYYY"). Live calls, responses saved to
`tests/fixtures/grants_gov_search2.json` and
`tests/fixtures/grants_gov_fetchOpportunity.json`.

- `POST /v1/api/search2` and `POST /v1/api/fetchOpportunity` need **no
  authentication**.
- Both return **HTTP 200 with an in-band `errorcode`**. Checking the status
  code alone reads a failure as a success, so the client checks `errorcode`.
- `search2` → `data.oppHits[]` with `id, number, title, agency, agencyCode,
  openDate, closeDate, oppStatus, docType, cfdaList`.
- **`closeDate` is `MM/DD/YYYY`**, and `""` when there is no stated deadline.
- `fetchOpportunity` → `data.synopsis` with `awardFloor` / `awardCeiling` as
  digit strings, `responseDateStr` as `YYYY-MM-DD-HH-MM-SS`,
  `applicantEligibilityDesc` and `synopsisDesc` as **HTML-escaped HTML**, and
  `applicantTypes` as `[{id, description}]`.
- Detail pages resolve at
  `https://www.grants.gov/search-results-detail/{id}` — confirmed with a real
  fetch returning 200, not assumed.

Because the payload is HTML-escaped HTML, `agent/sanitize.py` unescapes
entities to a bounded fixpoint **before** stripping tags. One pass leaves
`&amp;lt;script&amp;gt;` looking like text; decoding first and stripping
second means anything that could ever decode into markup is markup by the
time the stripper runs.

---

### 2026-08-26 — Structured output, metrics, and throttling

Read from the same installed package, after a bug made it worth re-reading.

```
Agent.structured_output_async(output_model, prompt) -> T
  DEPRECATED in 1.53.0. Warns: "You should pass in `structured_output_model`
  directly into the agent invocation." Returns T. No AgentResult, therefore
  no metrics, therefore nothing to charge a budget from.

Agent.invoke_async(prompt, *, invocation_state=None,
                   structured_output_model: type[BaseModel] | None = None,
                   structured_output_prompt: str | None = None,
                   idempotency_token=None, limits: Limits | None = None,
                   **kwargs) -> AgentResult

AgentResult(stop_reason, message, metrics, state, interrupts=None,
            structured_output: BaseModel | None = None, checkpoint=None)

EventLoopMetrics.accumulated_usage -> Usage TypedDict
  {inputTokens, outputTokens, totalTokens}
  Agent.invoke_async calls event_loop_metrics.reset_usage_metrics() at the
  top of each invocation (agent/agent.py:1233), so accumulated_usage is
  per-invocation. That is what makes it safe to charge per call.

strands.types.agent.Limits (TypedDict, total=False): turns, output_tokens,
  total_tokens. On a trip, stop_reason is "limit_turns",
  "limit_total_tokens" or "limit_output_tokens".

strands.types.exceptions.ModelThrottledException
  BedrockModel raises this, wrapping a botocore ClientError whose
  Error.Code is "ThrottlingException" (models/bedrock.py:1355-1362).
```

Consequences, all now in `agent/prompting.py`:

- `structured_call` uses `invoke_async`, so every model call is charged and
  Strands' own per-call `limits` finally has somewhere to go.
- `structured_output` coming back `None` is treated as a schema failure and
  feeds the existing retry loop. Absence of an answer is not an answer.
- A `stop_reason` beginning `limit_` abstains without retrying. Retrying
  spends more against a cap that has already tripped.
- Throttling gets a **separate** retry budget from schema validation, because
  a busy region says nothing about whether a prompt is followable, and
  spending a schema attempt on it would make a service incident look like a
  broken prompt.

---

### 2026-08-27 — Grants.gov pagination and date filtering

Confirmed from the official parameter documentation
(`grants.gov/api/common/search2`) **and** two live calls before any request
field was changed, because a plausible-looking invented parameter is exactly
what Section 0.5 rule 1 forbids.

```
search2 request:  rows, startRecordNum, keyword, oppNum, oppStatuses,
                  aln, fundingCategories, agencies, eligibilities,
                  fundingInstruments, sortBy
search2 response: data.hitCount, data.startRecord, data.oppHits[]
```

Live probe, keyword="student", rows=3:

```
startRecordNum=0 -> startRecord 0, hitCount 228, ids 348985/334326/363101
startRecordNum=3 -> startRecord 3, hitCount 228, ids 329436/363559/357996
```

Two facts that shaped the implementation:

- `openDate` is `MM/DD/YYYY`, like `closeDate`.
- **There is no documented server-side posted-date filter.** So the `since`
  argument — which the tool had accepted and ignored since it was written —
  is applied client-side against `openDate`. A row with no `openDate` is
  kept: a missing date is not evidence that a row is old.

Fixtures: `tests/fixtures/grants_gov_search2_page{1,2}.json`, sanitized to
drop facet blocks the client never reads.

---

### 2026-08-27 — The seed verifier checks evidence, not just reachability

`verify_seed.py` previously proved a page existed. It now re-finds every
`criteria[].text` quote on the page that quote cites in its `source_doc`.

The sub-page part was not a refinement, it was the difference between a
usable check and a useless one. The first run of the quote check failed 20 of
40 rows; inspection showed 16 of those failures were quotes that were real
but lived on an FAQ, a rules page, or an NSF solicitation rather than the
landing page the row pointed at. Checking every quote against one URL would
have trained everyone to ignore the verifier.

Evidence citing a page outside the funder's own site is refused rather than
blessed — `nasaorbit.org` is not `nasa.gov`, and deciding whether that is
legitimate is a human's call.

What it still cannot check is interpretation. `award_max: 10000` being the
right reading of the sentence quoted beside it is a judgment, not a match.

---

### 2026-08-27 — Two holes found by transcribing a real form

Both were found by doing the work, not by reading the code.

1.  `blocklisted()` did not catch **"MIT CEP 2026 IP, Capital, and Revenue
    Disclosure Forms"**. The disclosure pattern covered debarment, conflict
    of interest and lobbying — the three Section 10.1 names — and the bare
    word *disclosure* matched nothing. A disclosure form would have been
    handed to the Drafter.
2.  `ApplicationField.protected`, added so a curator could mark a field the
    agent must never fill, was consulted by nobody. The Drafter checked only
    label patterns. A flag that only advises is a flag that gets ignored, so
    the Drafter now treats it as an independent reason to force
    `NEEDS_FOUNDER`.

Pinned by `tests/test_real_forms.py`, which runs the real drafting path
against every real form with a stub that tries to answer every field.

---

### 2026-08-27 — Extraction is separated from decision, with re-found spans

`agent/tools/extraction.py` splits eligibility prose into three stages:
an untrusted `EligibilityExtraction` (perception, may be a model), a pure
Python `verify()` (no model, no network), and a total projection into
`EligibilityRules`.

The rule that makes it safe: **a structured field exists only when its
verbatim span is re-found in the source text.** A model that hallucinates a
rule must also hallucinate a quote that appears verbatim on the page — and if
it manages that, the quote is on the page. A paraphrase fails identically to
a fabrication, because both are text the source does not contain.

Five more failure modes collapse to UNKNOWN rather than to a guess: a negated
span offered as permission, an exception clause, an illustrative list
("including but not limited to") treated as a closed set, a value outside the
controlled vocabulary, and two page sections that disagree. Each is recorded
with its reason, so a reviewer can tell "the page was silent" from "the
extractor lied" — identical outputs, very different problems.

Writing the adversarial cases found an ordering bug immediately: "not limited
to" contains "not", so the negation matcher fired before the non-exhaustive
check and reported the wrong reason. Non-exhaustive markers are stripped
before the polarity scan.

---

### 2026-08-27 — What the discovery benchmark may not do

The reference set is version-controlled at
`tests/discovery_benchmark/reference_set.json` and its ground truth comes
from each program's own page. It is **not** derived from
`opportunities.seed.json`, from the scraper, or from any code it scores — a
test asserts the reference keys are not a subset of our catalog ids, so it
cannot quietly become self-scoring.

Six of the twenty entries are deliberate negatives: equity-taking (Hult
Prize, Z Fellows), non-cash (the MTC hackathon), closed to outsiders (Stevens
Ansary), defunct (EPA P3) and indirect-access-only (UPitchNJ). A reference
set of only positives cannot detect a catalog that recommends equity deals
and dead programs.

Carrying a negative is not automatically a defect. An equity-taking program
in the catalog is *correct* as long as the row records `takes_equity`,
because that is what lets the deterministic filter drop it with a readable
reason. The benchmark distinguishes the two cases.

Scoring the scorer found a real defect: title matching counted "Alpha Dining
Services Survey" as the "Alpha Innovation Fund" because both live on the same
host and share the organisation's name. Host-derived and generic tokens
("competition", "fund", "prize") are now dropped before comparing.

The benchmark cannot claim to measure the funding universe, cannot find a
program nobody thought of, and says nothing about drafting groundedness —
that is the Section 11.11 golden set, kept separate on purpose.

---

### 2026-08-27 — Reverification reports; it does not curate

`scripts/reverify.py` refetches stale rows and writes
`data/reverification.report.json`. It never edits a curated fact, never flips
`verified`, never updates a deadline.

The temptation is obvious: a 404 is unambiguous, so why not set
`verified: false` automatically? Because the next step after that is
"the deadline moved, so update it", and at that point human curation has been
replaced by a parser with write access. Three tests pin the property
directly, including one asserting a dead row keeps its `verified` flag.

---

## Deviations from the spec

### D1 — `AgentCoreMemorySessionManager` does not exist

**Spec (Section 3):** cross-run memory via Strands'
`AgentCoreMemorySessionManager`, "first-party integration, a few lines".

**Reality:** no such symbol in `strands-agents 1.53.0`. `strands.session`
exposes `FileSessionManager`, `S3SessionManager`, `RepositorySessionManager`
and `SnapshotSessionManager`. AgentCore Memory appears instead as
`strands_tools.agent_core_memory.AgentCoreMemoryToolProvider` — a *tool* the
agent calls, not a session manager that persists conversation state.

**Decision:** cross-run memory is the `Repository` (`recall`,
`remember_answer`, `has_surfaced`), which is where it belongs anyway —
"never re-ask a known question" is a data question, not a conversation-state
question, and it has to survive a process restart.

**TODO:** if AgentCore Memory is wanted for the submission's AWS story, wire
`AgentCoreMemoryToolProvider` as an additional tool and confirm what it
actually persists. Do not describe it as a session manager in the README.

### D2 — `ship_gate` takes more than two arguments

**Spec (Section 11.9):** `ship_gate(draft: Draft, kb: KnowledgeBase) -> GateResult`.

**Reality:** checks 5 and 7 of the spec's own run order cannot be computed
from those two arguments. The closed-world check needs this run's retrieved
set; the auditor check needs the `AuditReport`.

**Decision:** `ship_gate(draft, kb, *, retrieved, opportunity, audit,
required_field_ids)`. The extra arguments are keyword-only and all optional,
so the two-argument call in the spec still works — it just runs fewer checks,
and `GateResult.checks_run` records exactly which.

### D3 — `DraftField` and `FieldRecord` are one model

Section 8 defines `DraftField`; Section 11.8 defines `FieldRecord` with
overlapping fields. They describe the same object at two fidelities. Kept as
one `DraftField` carrying the receipt fields — two models over one row
guarantees they drift, and the receipt is only useful if it is impossible to
have a field without one.

### D4 — Resolvable blockers are not rejections

Section 6 lists `entity type` and `team size` among the hard filter's
deterministic checks. Section 10.7 names "form an LLC" and "get a faculty PI"
as blockers worth **surfacing** as a MAYBE.

Encoding them as `INELIGIBLE` would silently discard exactly the
opportunities the escalation policy says to show. So `EligibilityResult`
carries `resolvable_blockers: list[Blocker]` alongside a still-three-valued
`verdict`. A missing entity type or an unmet team minimum attaches a
`Blocker` and passes through; a team **over** a stated maximum is still a
rejection, because you cannot un-hire a co-founder.

### D5 — An equity check was added to the hard filter

Not in the Section 6 list, but `FounderProfile` carries `equity_ok` and the
product is defined as *non-dilutive* funding. An accelerator taking 6% is a
deterministic dealbreaker for a founder who said no equity, and it is
checkable without a model.

### D6 — An unmatched institution is UNKNOWN, not INELIGIBLE

"Georgia Tech" is not a substring of "Georgia Institute of Technology".
Institution names are matched by prefix-token comparison in both directions,
and a failure to match produces `UNKNOWN` rather than a rejection — a name
formatting difference must not cost the founder a real opportunity.

Known gap: acronyms. "MIT" will not match "Massachusetts Institute of
Technology". That lands on UNKNOWN, which becomes a founder-facing question
rather than a wrong answer, so it is survivable.
**TODO:** add an acronym table once the curated catalog shows which
institutions actually appear as initialisms.

### D7 — The Auditor runs on the reasoning tier, not the cheap one

Section 3 assigns Claude Haiku to "cheap, high-volume eligibility parsing"
and Sonnet to "drafting, fit judgment". The Auditor is neither, exactly.

It runs on the reasoning tier. It is the last check standing between an
invented number and a real funding application, and saving tokens there is
the wrong trade. The classification tier is reserved for its named job:
parsing eligibility text at volume.

### D8 — The scheduled run is deterministic Python

Section 4 draws Scout as a Strands orchestrator agent. Both exist:

- `run_once(ctx, sources)` — the deterministic pipeline. What EventBridge
  invokes and what the demo runs.
- `build_scout_agent(ctx, sources)` — the same six tools on a real Strands
  `Agent` with `agent/prompts/scout.md`. The interactive path.

They share one `RunContext` and one set of tool objects, so both produce the
same `RunReport` and the same audit trail.

The scheduled path is not model-driven because the things that must not vary
run-to-run all live in control flow: the token ceiling, the assessment cap,
never-notifying-twice, and the escalation policy. A model choosing the loop
order can spend the budget twice on a bad day. The model still makes every
judgment call — fit, reasoning and prose — which is the part that needs one.

The policy is enforced *inside* each tool rather than in the prompt, so the
Scout agent cannot route around it either.

### D9 — SQLModel tables are key columns plus a JSON payload

Section 3 specifies SQLite + SQLModel locally and DynamoDB deployed, "behind
the same repository interface". Rather than a normalised relational schema,
every table is a primary key, the columns worth querying on, and the full
Pydantic model serialised into a `Text` payload.

That is the shape DynamoDB wants (partition key, sort key, document), so the
second implementation is a port rather than a rewrite. The cost is that you
cannot query inside a payload from SQL, which nothing needs to do.

Idempotency is a **unique index** on `founder_id::opportunity_id`, not a
check-then-act in Python. Double-notifying should be impossible rather than
unlikely.

### D10 — `recall` matches normalised text, not semantics

Section 6 asks whether the founder answered a *semantically equivalent*
question before. Implemented as exact match after lowercasing and stripping
punctuation, which needs no model call and no vector store.

**TODO:** back it with Bedrock Titan embeddings and a cosine threshold once
the golden set shows how often near-duplicate phrasings actually appear.
Until then the "Application 1 needed 15 answers, this one needs 3" number is
real but conservative — it undercounts reuse rather than overcounting it.

### D11 — The daily spend ledger is SQLite, not a JSON file

**Superseded.** It was a JSON file: read the dict, add, write it back.
Correct for exactly one process, and the async job boundary (D18) means
there will not be exactly one process. Two concurrent charges could both read
the same stale total, both conclude they were under the cap, and both spend.

Now SQLite in the same state directory. `add()` is one `BEGIN IMMEDIATE`
transaction — increment and read-back inside the writer lock — so concurrent
calls serialise and each sees a total including every earlier call. The call
that crosses the cap is still recorded and *then* halted, which is the
semantics `charge()` always had: the report shows what was actually spent,
including the call that crossed the line.

The failure posture is unchanged. A corrupt database or an unreadable legacy
file raises `BudgetExceeded` — we refuse to spend money we cannot account
for, and we never reset the ledger to zero. An existing `daily_spend.json` is
imported once (`INSERT OR IGNORE`, idempotent per day-key) and left in place
as its own backup, never rewritten and never deleted.

**Still true:** DynamoDB with an atomic counter (`UpdateItem` with `ADD`) is
the answer if runners ever span machines. SQLite's writer lock is a
single-filesystem guarantee.

### D12 — Bedrock prices default to zero

`.env.example` ships `KAIROS_PRICE_*_PER_MTOK=0`, so `usd_estimate` reads
`0.0` until someone confirms live pricing for the region. Visibly wrong beats
quietly wrong, and the per-run token ceiling still enforces regardless.

**TODO:** fill these from the Bedrock pricing page before the first
scheduled run.

### D13 — The seed catalog is a stub

Section 5 requires 60–100 curated rows. The schema, the verifier
(`scripts/verify_seed.py`), the `verified: false` exclusion behaviour and the
demo catalog all work. The curation itself is outstanding.

Demo runs use `data/opportunities.demo.json`, which is synthetic by
construction: `[DEMO]` in every title and funder, every `source_url` on
`.invalid` — a TLD RFC 2606 reserves so it can never resolve.

**TODO:** curate the real rows. Budget one sitting. Section 13 is right that
this is grinding work.

### D14 — A scraping pipeline, and the four rules that shape it

**Spec (Section 5, Tier 3):** AgentCore Browser navigates 3-5 known
university pages, behind a feature flag, week 3 only, "do not architect
around this".

**What was built instead:** `agent/scraping/`, a deterministic collection
pipeline over a curated target registry. Static `httpx` + BeautifulSoup by
default; a headless browser only where a static fetch has already proved the
page returns a JavaScript shell, and only when explicitly asked for. Of eight
targets, exactly one qualifies.

Four rules, each enforced in code rather than in a docstring:

1.  **Never infer a missing field.** `ScrapedOpportunity.set_field` refuses to
    populate a field without an `Evidence`, so an unstated rule lands in
    `unknown_fields` and nowhere else. This is the same three-valued logic as
    `agent/tools/eligibility.py`, one layer earlier: silence about equity
    stays UNKNOWN even where "no equity" would almost certainly be right.
2.  **Every populated field carries the verbatim sentence and its URL.**
3.  **robots.txt fails closed.** Fetched once per host, cached to
    `data/raw/robots/` so the decision is auditable later, and an unreachable
    robots.txt is a refusal rather than permission.
4.  **Nothing is promoted.** Output is
    `data/opportunities.rutgers.candidates.json`, every row
    `NEEDS_HUMAN_REVIEW`. No code path writes `opportunities.seed.json`.

There is no login, form-submission, or CAPTCHA code path.

### D15 — Two domain tiers, so "Rutgers-owned only" stays true

The brief says Rutgers-owned domains only. The supplied target list also
contains four off-domain pages, each for a reason: NJIT's competition is open
to Northern NJ students generally, Stevens' is the instructive negative,
Devpost hosts a Rutgers event, and Rutgers' own student organisation
directory is hosted on campuslabs.com.

Rather than quietly widening the rule, `registry.Target.tier` splits them.
`RUTGERS` rows are the only ones link discovery may expand into, and
`is_rutgers_domain` is checked inside `discover_links` rather than trusted to
a caller. `PROVIDED_EXTERNAL` rows are fetched once at exactly the URL the
operator supplied, never crawled, and flagged `[off-domain]` in the output so
a reviewer sees it without reading the registry.

### D16 — Founder reviews are never written by the scraper

The requested output schema includes reviews from past student founders. No
page in the target set publishes any, and the field is the single most
damaging one to fill speculatively: it is what someone reads when deciding
whether a program is worth two weeks.

`FounderReview` therefore requires an `added_by`, the scraper has no code path
that appends to the list, and every record carries a caveat saying the
emptiness is by construction rather than by omission. `write_candidates`
preserves hand-typed reviews and `review_status` across re-scrapes, so a
human's work is never overwritten by the next run.

### D17 — beautifulsoup4 as a direct dependency

Section 3 does not list an HTML parser; Section 0.5 rule 5 requires a dated
line for anything outside it. `beautifulsoup4` was already present
transitively via `strands-agents-tools`; it is now declared directly because
`agent/scraping/` imports it. `playwright` is an **optional** extra
(`uv sync --extra js`) and is never installed or invoked by default.

### D18 — A run is a durable job, not a held-open connection

`POST /founders/{id}/runs` used to do discovery, every model call and
drafting before returning. Minutes of work living or dying with one TCP
socket: an ALB idle timeout, a closed laptop lid, or a scheduler retry
landing mid-run each produced a different flavour of nothing.

The endpoint now persists a `RunJob` and answers **202** with a job id. The
job row is the source of truth from that moment — a poller reads it, a retry
with the same idempotency key resolves to it, and a crash is repaired at
startup rather than leaving a row that claims to be running forever.

Three consequences worth naming:

- **Halted is not failed.** A run that hit a budget cap or a throttle
  *finished* and has a report. The job says `halted` and points at it, and
  nothing goes to the failure log. Only a run that could not report at all is
  a failure, classed `startup`, `timeout`, `crash` or `orphaned`.
- **`run_once` is untouched.** The job records the lifecycle; the
  `RunReport` remains immutable and append-only, exactly as before.
- **`LocalJobExecutor` runs jobs in the API process.** Correct while the run
  lease and `desired_count = 1` both guarantee one. `JobExecutor` is the seam
  a queue-backed worker slots into, calling the same `execute_job`.

**Rejected:** SQS plus a separate worker service from the start. It is the
right end state and the wrong first step — it doubles the deployment surface
to solve a concurrency problem that the single-writer storage decision (D19)
already forbids.

### D19 — The run lease is SQLite, and `desired_count = 1` is load-bearing

Two invocations of the same run — a scheduler retry landing while the first
attempt is still working, or a double-clicked button — must not both execute.
`RunLock` is a lease keyed by `(founder_id, run_kind)`, and acquisition is a
single `BEGIN IMMEDIATE` transaction: SQLite serialises writers at the file
level, so two processes cannot both see "free" and both insert.

Every lease carries a random ownership token and release requires it. A slow
first run finishing after its lease expired cannot yank the lease out from
under the second run that legitimately took over. Expired leases are adopted
in the same atomic step, so crash recovery needs no janitor process.

The lease is the *second* line of defence. The first is that one Fargate task
writes one SQLite file. The day two writers are genuinely needed, the answer
is RDS Postgres behind the same `Repository` protocol — not a second SQLite
reader, and not a cleverer lock.

**Rejected:** an advisory lock in the database the application already uses.
It would work, and it couples "can a run start" to "is the application
database healthy", which is exactly the coupling you do not want when
diagnosing a stuck run.

### D20 — Ownership refusals are 404, never 403

A shared bearer token is not an identity: it proves somebody holds the
secret, never *which* founder they are. Adding per-founder authorization
raises a question the old design never had to answer — what does the API say
when a valid credential asks for somebody else's resource?

**404, with the same wording a genuinely missing resource gets.** A 403
confirms the id exists, which turns id-guessing into founder enumeration and
gives away the thing the authorization was added to protect. Not-found and
not-yours must be indistinguishable from outside, which means the *message*
has to match too, not just the status code.

The three resource-id-only routes (`/inbox/{item_id}`, `/drafts/{draft_id}`,
`/opportunities/{id}`) were the concrete hole. The first two now look up the
owner before acting. The third deliberately does not, and the reason is in
the docstring: an opportunity is a public funding programme, the same row for
everyone. *Which* opportunities a founder was shown is founder data, and that
lives in the scoped inbox.

**Rejected:** an identity provider. OIDC/JWT is a product decision with an
operational tail, so `api/auth.py` builds the `Authenticator` seam and ships
two honest implementations — a shared token, and a hashed credential file
with revocation and restart-free rotation — rather than faking an integration
nobody has chosen.

### D21 — Alembic, and an initial revision that adopts rather than demands

`SQLModel.metadata.create_all()` cannot evolve a schema. It creates what is
missing and says nothing at all about a table whose shape has changed, so the
first time a column moved, a deployed database would keep serving until a
query hit the difference — a 500 at 3am rather than a red probe at deploy.

The wrinkle is that every database that already exists was built by
`create_all()` and has no `alembic_version` table. A plain autogenerated
revision dies on its first `CREATE TABLE` against one, which is the exact
moment somebody reaches for `--force` or deletes the volume. So the initial
revision creates only tables that are *absent*: `upgrade head` handles a
fresh database, a pre-`jobs` database with live rows, and a database already
at head, and nothing it does drops or rewrites a table that exists.

In production the application no longer creates its own schema
(`create_schema=False`) and `/ready` reports `schema: unmigrated` when the
revision table is missing, so a deploy that skipped its migration fails its
probe instead of booting on a half-invented schema.

**Rejected:** `alembic stamp head` on existing databases as the adoption
path. It works and it requires a human to run exactly one command exactly
once on every environment, which is a procedure that gets skipped.

### D22 — Liveness and readiness answer different questions

`/health` returning `{"status": "ok"}` for everything meant a load balancer
could not tell a container that was serving from one whose EFS mount had gone
read-only.

`/health` is now deliberately dependency-free. A liveness probe that checks
the database is a liveness probe that restarts a healthy container because
storage hiccuped, and restarting rarely fixes storage.

`/ready` checks what a request actually needs — a trivial query, a write
probe against the state directory, that configuration resolves — and in
production also that a credential exists, that the schema is migrated, and
that the dollar cap can actually fire. It invokes no model: a readiness check
that costs a Bedrock call is a readiness check that bills you per probe
interval.

Both stay unauthenticated so a load balancer can reach them, which means
neither may describe the deployment. `/ready` names *which* check failed and
never what it was configured with — no model IDs, no paths, no token — and a
test asserts exactly that.

---

## Bugs found by tests, worth remembering

### 2026-08-22 — The retry loop was amplifying the budget guard

`structured_call` caught every `Exception` and retried twice with the error
appended. `BudgetExceeded` is an `Exception`. So a token ceiling firing
inside a model call was caught, retried twice, and ended up spending **three
times** the ceiling it existed to enforce — then surfaced as an abstention,
so the run continued as if nothing had happened.

Found by `test_a_blown_token_ceiling_halts_and_surfaces_nothing`, which
asserted `halted_reason is not None` and got `None` alongside a note reading
"30,000 tokens used, ceiling is 5,000".

Fixed by excluding control-flow signals (`BudgetExceeded`, `Abstention`,
`CancelledError`, `KeyboardInterrupt`) from the retry loop. The general
lesson: a retry loop is not an error handler, and a catch-all inside one
turns every guard it wraps into its opposite.

---

### 2026-08-26 — The token ceiling and the daily cap were never enforced

`agent/budget.py` had a working `charge()`, a working ledger, a tested
`BudgetExceeded`, and a tested halt path in `run_once`. It also had
`charge_agent_result`, which **nothing in production ever called**. The only
budget call on the run path was `take_assessment_slot()`. So the assessment
cap enforced and the other two — `KAIROS_MAX_RUN_TOKENS` and
`KAIROS_DAILY_USD_CAP` — were decoration.

The cause is the deprecation above: `structured_output_async` returns the
parsed model, so there was no `AgentResult` to charge from, and rather than
noticing that, the docstring wrote the gap down as a design note.

What let it survive a green suite is the more useful lesson. The test named
`test_a_blown_token_ceiling_halts_and_surfaces_nothing` used a fake assessor
that called `budget.charge(...)` by hand. It proved the halt path worked; it
could not prove anyone reached it. **A test that simulates the thing it is
meant to observe is not observing anything.** The fake now reports usage the
way a real agent does and lets the orchestrator do the charging, so the same
test would fail against the old code.

Section 11.12's throttling row was missing for a related reason: it was
covered by a generic `except Exception` that retried instantly, appended the
throttle text to the prompt, and abstained — three wrong behaviours reading
as one handled case.

---

### 2026-08-26 — The evidence check cannot tell a claim from its negation

Found by the Section 11.11 golden set on the first run it ever did, which is
the best argument for building one.

`FORBIDDEN_CLAIMS` (`agent/guardrails.py:238`) pairs a trigger regex with an
evidence regex, and treats an evidence match anywhere in `kb.text` as support
for the claim. Keyword matching has no notion of polarity, so the knowledge
base sentence that most clearly *refutes* a claim is usually the one that
contains its keywords:

| Draft claims | Knowledge base says | Evidence pattern that "supported" it |
|---|---|---|
| "We work closely with a faculty advisor." | "there is no faculty advisor" | `faculty advisor` |
| "Incorporated as a Delaware C-Corporation." | "No legal entity has been formed" | `formed` |

Both are Section 10.2 never-invent categories. Both shipped. Neither the
numeric whitelist, the entity check nor the closed-world check applies, because
neither claim contains a number or a name.

**Not fixed, deliberately.** The obvious patch is a negation window — refuse an
evidence match within N tokens of "no", "not", "without", "none". It would make
exactly these two cases pass, and a check tuned until it satisfies the eval
that measures it has stopped measuring anything. The real fix needs its own
adversarial cases, written without this scoreboard in view, and it has to be
checked against the clean cases too: "no revenue yet, but 40 users" is a
sentence where a naive negation window would start blocking supported claims.

Recorded rather than patched so the number in the README stays true.

**TODO:** write the negation cases first, then the fix, then re-run the eval
and update `tests/test_golden_set.py` in the same commit.

---

## Naming

### 2026-08-22 — Provision -> Kairos

`prompt.md` shipped "Provision" as an explicit working name ("swap it
everywhere if you pick something better; nothing depends on it"). Renamed to
**Kairos**.

*Kairos* is the Greek word for the opportune moment, as opposed to *chronos*,
clock time. The classical figure — winged feet, a forelock in front and bald
behind, scales balanced on a razor's edge — is a deadline and a judgment
call in one image, which is what this system is.

Mechanical scope: package name, `KAIROS_*` environment prefix, logger
namespace, SQLite filename, state directory, CORS origins and the Vercel
preview regex, plus doc references. 16 files.

One rename hazard worth recording: the string `provisional` appears in the
Section 10.2 forbidden-claims regex and in the Drafter prompt, where it means
a provisional patent. A naive find-and-replace would have quietly turned that
into `kairosal` and broken an IP-invention check without failing a test —
none of the grounding tests happen to exercise the word. The rename used a
negative lookahead, and the three occurrences were verified intact
afterwards.

---

## Environment notes

- `uv` resolved the virtualenv to **Python 3.13.13**, above the `>=3.11`
  floor in `pyproject.toml`. Nothing depends on 3.13; the floor is the
  contract.
- The **AWS CLI is not installed** in this environment, so the Bedrock model
  IDs in `.env` have not been discovered yet. `agent/config.py` raises on
  startup while they are empty, which is deliberate — see `.env.example` for
  the exact `aws bedrock list-foundation-models` command.
  **TODO:** run it, paste the IDs, and confirm whether the models need an
  inference-profile prefix (`us.` / `global.`) in the target region.
