# The golden set (Section 11.11)

15 fixture drafts against known knowledge bases, 8 of them carrying deliberate
traps. Each case declares, per field, what *should* happen — and that
declaration is written by hand from the knowledge base, never derived from what
the code does. A scorer that asks the gate whether the gate was right is
marking its own homework.

```bash
uv run python scripts/run_eval.py            # offline, ~1s, no AWS account
uv run python scripts/run_eval.py --verbose  # per-field detail
uv run python scripts/run_eval.py --live     # real Bedrock, costs tokens
```

## What the two modes actually claim

The pipeline is `Drafter (model) → deterministic defenses → Auditor (model) →
ship gate (deterministic)`. The eval swaps out only where the two model calls
get their answers, and runs everything else for real.

| Mode | Model output comes from | The number means |
|---|---|---|
| offline (default) | the case fixture | **the defense layer**, given a stated model output |
| `--live` | Bedrock | **the whole system**, the Section 11.11 figure |

These are different claims and the README says which one it is printing. An
offline number is not a groundedness score for the agent; it is a groundedness
score for everything downstream of the agent. That is worth measuring on its
own — it is the part that must hold when the model misbehaves — but calling it
the model's score would be a lie.

## The three numbers

- **Groundedness** — of everything that reached a real application, how much was
  supported by the knowledge base. A leak here is a student submitting a claim
  an agent invented. Target 100%.
- **Abstention accuracy** — of the claims that were *not* supported, how many
  were correctly withheld. Section 11.11's second metric. Target 100%.
- **Unnecessary questions** — of the claims that *were* supported, how many got
  withheld anyway. The cost of the other two, and it is not zero. Reported
  because a system that ships nothing scores perfectly on safety and is
  worthless.

`groundedness` is `None`, not 100%, when nothing shipped. A silent system is
not a grounded one, and printing 100% for it would be the most flattering
possible lie.

## Two deliberate choices worth arguing with

**The default Auditor waves everything through.** Unless a case says otherwise,
the fixture auditor returns SUPPORTED for every answered field with a quote
lifted from the knowledge base. That is the worst realistic auditor, not a good
one — the ship gate exists to catch what an auditor missed, so an eval whose
auditor catches the traps first would be scoring the auditor and reporting it
as the gate.

**A blocked draft ships nothing, including its clean fields.** Scoring
per-field while ignoring the draft-level verdict would credit the system for
text it never released. The cost shows up as `collateral` inside unnecessary
questions, which is where it belongs — visible, not hidden.

## What it found the first time it ran

Two leaks, one root cause: **the forbidden-claims evidence check cannot tell a
statement from its negation.**

`agent/guardrails.py:238` pairs a trigger regex with an evidence regex, and
treats any evidence match anywhere in the knowledge base as support:

- `trap_04` claims *"We work closely with a faculty advisor."* The deck says
  *"there is no faculty advisor."* The evidence pattern `faculty advisor`
  matches — the negation.
- `trap_05` claims *"LabQueue is incorporated as a Delaware C-Corporation."*
  The deck says *"No legal entity has been formed."* The evidence pattern
  `formed` matches — again the negation.

Both are exactly the class of claim Section 10.2 lists as never-invent, and
both shipped.

This was left unfixed on purpose. The obvious patch — refuse an evidence match
that sits near a negation marker — would make these two cases pass, and tuning
a check until it passes the eval it is measured by is how an eval stops meaning
anything. The fix needs its own tests and its own adversarial cases, written
without looking at this scoreboard.
