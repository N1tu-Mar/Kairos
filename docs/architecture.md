# Architecture

Diagram source is committed, not just a PNG. Render with any Mermaid tool.

## Getting into a run

A run takes minutes, so no HTTP request waits for one. Both entry points —
the scheduler and the dashboard button — hit the same endpoint, which
persists a job and returns immediately. The lease is what makes "the same
endpoint" safe.

```mermaid
flowchart TD
    SCHED["EventBridge Scheduler<br/>daily, Bearer + execution-id<br/>as the idempotency key"]
    BUTTON["Dashboard button<br/>fresh idempotency key per click"]

    SCHED --> POST
    BUTTON --> POST

    POST["<b>POST /founders/{id}/runs</b>"]
    POST --> AUTHZ["<b>authenticate + authorize</b><br/>which founder is asking?<br/><i>not-yours is 404, never 403</i>"]
    AUTHZ --> IDEM{"idempotency key<br/>already seen?"}

    IDEM -->|yes| SAME["<b>200</b> — the original job<br/><i>a retry is not a second run</i>"]
    IDEM -->|no| LEASE{"acquire run lease<br/>(founder, run_kind)<br/><i>BEGIN IMMEDIATE</i>"}

    LEASE -->|refused| CONFLICT["<b>409</b> — a run is already in progress"]
    LEASE -->|acquired| JOB["persist RunJob · <b>202</b> + job id"]

    JOB --> EXEC["executor runs it in the background"]
    EXEC --> SCOUT

    POLL["GET /founders/{id}/jobs/{job_id}"] -.->|"dashboard polls"| JOB

    SCOUT --> DONE{"outcome"}
    DONE -->|"report written"| OK["job: succeeded / <b>halted</b><br/><i>halted is finished, and has a report</i>"]
    DONE -->|"could not report"| BAD["job: failed<br/>startup / timeout / crash / orphaned<br/>→ scheduler failure log → dashboard"]

    OK --> REL
    BAD --> REL
    REL["<b>release lease in finally</b><br/><i>success, halt, timeout, crash, cancel</i>"]

    CRASH["process dies mid-run"] -.->|"startup recovery"| BAD
```

Two properties this diagram exists to make obvious:

- **Nothing reaches Scout without holding the lease**, so two runs for one
  founder cannot overlap however they were triggered.
- **The lease is released in a `finally`**, and a crash that skips even that
  is repaired at startup — no job stays `running` with no process behind it,
  and the lease expires on its own TTL.

## The run

```mermaid
flowchart TD
    SCOUT["<b>Scout</b> — orchestrator<br/>runs headless, no user in the loop"]

    SCOUT --> DISC["discover_opportunities"]
    SCOUT --> PROF["load founder profile<br/>+ knowledge base"]

    DISC --> T1["Tier 1 — seeded catalog<br/><i>verified rows only — 34 of 40</i><br/><i>every quote re-found on the page it cites</i>"]
    DISC --> T2["Tier 2 — Grants.gov<br/><i>search2 paginated by startRecordNum</i><br/><i>+ fetchOpportunity, bounded concurrency</i><br/><i>since filtered client-side on openDate</i>"]
    DISC --> T3["Tier 3 — campus source<br/><i>KAIROS_ENABLE_BROWSER</i><br/><i>ACCEPTED rows only</i>"]

    SCRAPE["<b>campus scraper</b> — operator-run<br/>robots-aware, rate-limited, archived"]
    SCRAPE --> REVIEWFILE["candidates file<br/><i>every row NEEDS_HUMAN_REVIEW</i>"]
    REVIEWFILE -.->|"a person reads the evidence<br/>and sets ACCEPTED"| T3
    REVIEWFILE -.->|"never automatically"| T1

    T1 --> INGEST
    T2 --> INGEST
    T3 --> INGEST
    INGEST["<b>ingestion boundary</b><br/>unescape, strip markup,<br/>cap size, redact PII"]

    INGEST --> FILTER
    PROF --> FILTER
    FILTER["<b>hard_eligibility_filter</b><br/>PURE PYTHON — no LLM<br/>reads structured fields only<br/>drops ~90% deterministically"]

    FILTER -->|INELIGIBLE| REJLOG["rejection log<br/><i>with the exact check that fired</i>"]
    FILTER -->|ELIGIBLE / UNKNOWN| ASSESS

    ASSESS["<b>Assessor</b> — sub-agent<br/>APPLY / MAYBE / SKIP / INSUFFICIENT_INFO<br/><i>reasoning tier, temperature 0</i>"]

    ASSESS -->|SKIP| SKIPLOG["skip log<br/><i>never shown, always recorded</i>"]
    ASSESS -->|APPLY / MAYBE| POLICY

    POLICY["<b>escalation policy</b><br/>PURE PYTHON — guardrails.py<br/>effort, reachability, award floor,<br/>already-surfaced, rank by value/hour"]

    POLICY -->|below the bar| SKIPLOG
    POLICY -->|top 3| DRAFT
    POLICY -->|overflow| PASSIVE["passive 'also found' list<br/><i>no notification</i>"]

    DRAFT["<b>Drafter</b> — sub-agent<br/>KNOWN / REUSED / GENERATED / NEEDS_FOUNDER<br/><i>the only call above temperature 0</i>"]
    DRAFT --> AUDIT
    AUDIT["<b>Auditor</b> — sub-agent<br/>fresh context: draft + KB only<br/>SUPPORTED / UNSUPPORTED / UNVERIFIABLE"]

    AUDIT --> GATE
    GATE["<b>ship_gate</b> — 8 ordered checks<br/>PURE PYTHON, fail-closed"]

    GATE -->|passed| READY["draft READY"]
    GATE -->|failed| BLOCKED["draft BLOCKED<br/><i>founder sees the reason</i>"]

    READY --> INBOX
    BLOCKED --> INBOX
    PASSIVE --> INBOX
    INBOX["<b>RunReport + Inbox</b><br/>scanned / filtered_out / judged / surfaced<br/><i>the only thing the founder sees</i>"]

    INBOX --> HUMAN["founder answers the gaps"]
    HUMAN --> APPROVE["human approves"]
    APPROVE --> STOP["<b>the agent stops here</b><br/>submission requires an<br/>Authorized Organization Representative"]
```

## The ship gate

Ordered. First failure stops the chain. An exception anywhere inside is
reported as `GATE_EXCEPTION` and the draft is `BLOCKED` — a throw in the
safety layer is never read as a pass.

```mermaid
flowchart LR
    A["1 · BLOCKLIST<br/><i>rewrites, does not fail</i>"] --> B["2 · PROVENANCE"]
    B --> C["3 · NUMERIC<br/>WHITELIST"]
    C --> D["4 · ENTITY<br/>CHECK"]
    D --> E["5 · CLOSED<br/>WORLD"]
    E --> F["6 · FORBIDDEN<br/>CLAIMS"]
    F --> G["7 · AUDITOR<br/>VERDICT"]
    G --> H["8 · COMPLETENESS"]
    H --> R["READY"]
```

## Trust boundaries

The thing worth understanding about this system is which layers can be
talked out of their answer and which cannot.

```mermaid
flowchart TB
    subgraph UNTRUSTED["untrusted — data from the open web"]
        DESC["opportunity descriptions"]
        SCRAPE["scraped pages, PDF text"]
        UPLOAD["founder uploads<br/><i>a deck can carry an injection too</i>"]
    end

    subgraph MODEL["model layer — influenceable"]
        ASSESSOR["Assessor"]
        DRAFTER["Drafter"]
        AUDITOR["Auditor"]
    end

    subgraph PYTHON["deterministic — cannot be influenced"]
        HARD["hard_eligibility_filter<br/><i>structured fields only</i>"]
        GATE2["ship_gate"]
        POL["escalation policy"]
        BUDGET["budget caps<br/><i>atomic daily ledger, token ceiling</i>"]
        AUTHZ2["authorization<br/><i>founder ownership</i>"]
        LEASE2["run lease<br/><i>one run per founder</i>"]
    end

    UNTRUSTED -->|"sanitised, capped,<br/>wrapped in a labelled block"| MODEL
    UNTRUSTED -.->|"never reaches"| HARD
    MODEL -->|"proposes"| PYTHON
    PYTHON -->|"disposes"| OUT["what the founder sees"]
```

A fully successful prompt injection inside an opportunity description can
make the Assessor say anything it likes. It still cannot change a Python
comparison against a structured field, it cannot spend past the token
ceiling, it cannot get an ungrounded number through the gate, it cannot
cause a second notification about the same opportunity, it cannot start a
second concurrent run, and it cannot reach another founder's data. That is
the design.

Every one of those is a database constraint or a Python comparison, not a
prompt: the unique index on the inbox idempotency key, the `BEGIN IMMEDIATE`
on the lease and the spend ledger, the ownership check on every
founder-scoped route. A model cannot argue with an index.

## Sub-agents

Three agents, three different failure modes. This is why they are separate
rather than one agent with a longer prompt.

| Sub-agent | Model tier | Temperature | Sees | Fails by |
|---|---|---|---|---|
| Assessor | reasoning | 0 | one opportunity, the profile, the filter's structured output | judging fit badly |
| Drafter | reasoning | > 0 | the form, the knowledge base, the opportunity | inventing facts |
| Auditor | reasoning | 0 | the finished draft and the knowledge base — **nothing else** | missing an invention |

The Auditor's isolation is the point. It never sees the Drafter's prompt, its
reasoning, or its provenance claims, because an auditor that inherits the
drafter's context inherits its mistakes. When they disagree, the Drafter
loses and the field goes back to the founder.

## The curation boundary

Discovery has two halves that must not touch, and the diagram above draws the
gap deliberately. Everything below the dotted line is research; everything
above it is what a founder can be told about.

```
      research                        │            runtime
                                      │
  campus scraper ──► candidates file  │
  (robots, rate limit, archive)       │
                          │           │
              a person reads evidence │
              and sets ACCEPTED ──────┼──► CampusDiscoverySource
                                      │    (KAIROS_ENABLE_BROWSER)
  research sweeps ──► batch files     │
                          │           │
       merge_candidates.py (validate, │
       dedupe, refuse unreachable)    │
                          │           │
                 candidates.json      │
                          │           │
        verify_seed.py (refetch page, │
        re-find every quote where it  │
        claims to come from) ─────────┼──► SeedCatalog
                                      │    (verified rows only)
```

Three properties hold across that line:

1.  **Nothing crosses automatically.** No code path writes
    `opportunities.seed.json` from a scrape, and no scraped row becomes an
    opportunity without a human setting `ACCEPTED`.
2.  **A quote is checked where it claims to come from.** Programs state
    eligibility on FAQ and rules sub-pages, so the verifier follows
    `source_doc` rather than assuming the landing page, and refuses evidence
    citing another organisation's site.
3.  **Re-checking reports; it does not curate.** `reverify.py` writes a diff
    of what moved — dead pages, redirects, expired deadlines, lost evidence —
    and edits nothing. Automatic correction of a curated fact is the same
    failure as automatic promotion, one step later.

## Measurement

Two evals, deliberately unrelated, because they answer different questions
and a single number would hide both.

| Eval | Question | Ground truth | Current result |
|---|---|---|---|
| Discovery benchmark | Did we find the money? | 20 hand-authored programs, read off their own pages, 6 deliberate negatives | 85.7% retrieval recall; eligibility 72.2% coverage at 100% precision; 0 wrong deadlines |
| Section 11.11 golden set | Did we lie on the application? | Per-field truth declared by hand | published in the README, fixture-based |

Neither derives its answers from the code it scores. The discovery
benchmark's reference keys are asserted not to be a subset of the catalog's
own ids; the golden-set scorer imports nothing from `guardrails`. A scorer
that asks the system whether the system was right is marking its own
homework.
