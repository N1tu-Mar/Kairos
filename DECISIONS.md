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

### D11 — The daily spend ledger is a JSON file

Correct for a single-process scheduled run, and inspectable. **Not safe
across concurrent processes.** A corrupt ledger refuses to spend rather than
resetting to zero, because a ledger you cannot read is not proof you are
under the cap.

**TODO:** on DynamoDB, replace with an atomic counter (`UpdateItem` with
`ADD`) before more than one runner exists.

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
