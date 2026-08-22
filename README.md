# Provision

**Tell us about your startup once. We watch for the money and handle the paperwork.**

An agent that watches for non-dilutive funding a student founder is actually
eligible for, decides which ones are worth their time, drafts most of the
application from what it already knows, and interrupts them only for the
handful of things it genuinely can't answer.

It is not a grant search engine, an AI grant writer, or a chat interface over
Grants.gov. Those exist. The thing that doesn't is **the loop running while
the founder is asleep**.

> **Status: in progress.** The agent loop, the deterministic safety layer and
> the API are built and tested. The curated catalog, the AWS deployment and
> the frontend are not. See [Honest limitations](#honest-limitations) — that
> section is accurate, not modest.

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
git clone <this repo> && cd provision
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

# one run against the synthetic catalog (needs Bedrock)
uv run python scripts/run_scout.py --demo --no-grants-gov

# a real run, live Grants.gov included
uv run python scripts/run_scout.py

# a local daily schedule, standing in for EventBridge
uv run python scripts/run_scout.py --schedule --hour 6

# the API
uv run fastapi dev api/main.py
```

### Curating the catalog

Seed rows are **generated, not written**. Add a candidate to
`data/opportunities.candidates.json` after opening the page, then:

```bash
uv run python scripts/verify_seed.py
```

It fetches every `source_url`, checks the page exists and actually mentions
the program, and writes `data/opportunities.seed.json` with an honest
`verified` flag. Rows that fail are excluded from runs. See
[`data/README.md`](data/README.md) for why, including a worked example of a
URL that looks exactly right and 404s.

---

## Testing

```bash
uv run pytest -q
```

Everything runs offline. Live API responses are recorded as fixtures in
`tests/fixtures/`, so the suite never depends on Grants.gov being up and
never spends a token.

The tests that matter most are the adversarial ones in
`tests/test_grounding.py` — all six cases the spec requires, including an
injected instruction inside an opportunity description asserting that the
deterministic filter's result is unchanged.

---

## Honest limitations

Written before the deadline pressure, so it stays honest.

- **The curated catalog is a stub.** The schema, the verifier and the
  exclusion behaviour work. The 60–100 real rows are not collected yet, so
  demo runs use an obviously-synthetic catalog: `[DEMO]` in every title, and
  every URL on `.invalid`, a TLD reserved so it can never resolve.
- **No AWS deployment yet.** No AgentCore Runtime, no EventBridge schedule,
  no live demo link. Scheduling runs locally on APScheduler.
- **No frontend yet.** The API is the interface.
- **Cross-run memory is the database, not AgentCore Memory.** The session
  manager the spec calls for does not exist in the installed SDK — see
  [`DECISIONS.md`](DECISIONS.md) D1.
- **`recall` matches normalised text, not meaning.** It undercounts reuse
  rather than overcounting it, which is the safe direction, but "this
  application needs 3 answers instead of 15" is conservative until it is
  backed by embeddings.
- **The grounding checks are regex and set membership, not semantics.** They
  produce false positives. Every false positive pushes a field to *you answer
  this*, which is the safe direction — one extra question beats one invented
  fact — but it is not free.
- **No groundedness number yet.** The golden-set eval is not built, so this
  README does not claim an accuracy figure. When it exists, the real number
  goes here whatever it is.
- **The daily spend ledger is a JSON file**, correct for one process and not
  safe across several.
- **Bedrock prices default to zero** in `.env`, so cost estimates read
  `$0.0000` until someone fills in live pricing. Visibly wrong beats quietly
  wrong; the token ceiling enforces regardless.

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
  prompts/         System prompts as version-controlled .md.
api/
  main.py          FastAPI read surface.
  repository.py    Protocol + SQLite. DynamoDB is a port, not a rewrite.
data/              Candidates, verified seed, synthetic demo catalog, forms.
scripts/           run_scout.py, verify_seed.py
tests/             Offline. Fixtures recorded from real API calls.
docs/              Architecture diagrams (Mermaid source).
```

---

## License

MIT. See [LICENSE](LICENSE).
