# Architecture

Diagram source is committed, not just a PNG. Render with any Mermaid tool.

## The run

```mermaid
flowchart TD
    SCHED["EventBridge Scheduler<br/>cron: daily 06:00<br/><i>(APScheduler locally)</i>"]
    SCOUT["<b>Scout</b> — orchestrator<br/>runs headless, no user in the loop"]

    SCHED --> SCOUT

    SCOUT --> DISC["discover_opportunities"]
    SCOUT --> PROF["load founder profile<br/>+ knowledge base"]

    DISC --> T1["Tier 1 — seeded catalog<br/><i>verified rows only</i>"]
    DISC --> T2["Tier 2 — Grants.gov<br/><i>search2 + fetchOpportunity</i>"]
    DISC --> T3["Tier 3 — AgentCore Browser<br/><i>feature-flagged, degrades cleanly</i>"]

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
        BUDGET["budget caps"]
    end

    UNTRUSTED -->|"sanitised, capped,<br/>wrapped in a labelled block"| MODEL
    UNTRUSTED -.->|"never reaches"| HARD
    MODEL -->|"proposes"| PYTHON
    PYTHON -->|"disposes"| OUT["what the founder sees"]
```

A fully successful prompt injection inside an opportunity description can
make the Assessor say anything it likes. It still cannot change a Python
comparison against a structured field, it cannot spend past the token
ceiling, it cannot get an ungrounded number through the gate, and it cannot
cause a second notification about the same opportunity. That is the design.

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
