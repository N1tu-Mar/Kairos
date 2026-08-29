# Incomplete work

Current as of 2026-08-27.

**Local health.** 843 Python tests pass with no `xfail` remaining; 55
frontend tests, TypeScript checking, ESLint and the production build all
pass. Migrations are covered by 20 tests against both a fresh database and a
representative existing one.

**Nothing in this repository has ever run against AWS.** No deployment, no
Bedrock call, no HTTPS path, no scheduled invocation, no alarm, no rollback
and no restore has been observed working. Everything below is honest about
which side of that line it sits on.

---

## Status at a glance

### Complete and locally verified

Built, tested, and exercised on a developer machine.

| | Where |
|---|---|
| Deterministic rule engine, eligibility filter, escalation policy, ship gate | `agent/` |
| Grounding leaks closed — negation no longer reads as support | `agent/guardrails.py` |
| Spelled-out quantities covered by the numeric whitelist | `agent/guardrails.py` |
| Semantic answer recall with a tested similarity threshold | `agent/semantic.py` |
| Run lease: atomic, cross-process, ownership-token, expiry recovery | `agent/scheduler.py` |
| Persistent scheduler failure log — sanitised, bounded, founder-scoped | `agent/scheduler.py` |
| Async job boundary: 202 + job id, status polling, idempotency, cancel, crash recovery | `api/jobs.py` |
| Atomic daily spend ledger — SQLite, one `BEGIN IMMEDIATE` per charge | `agent/budget.py` |
| A dollar cap that refuses to pretend when prices are zero | `agent/budget.py` |
| Per-founder identity and authorization; 404 never 403 | `api/auth.py` |
| Sanitised security audit events on every write | `api/auth.py` |
| Liveness and readiness as separate questions | `api/main.py` |
| Versioned migrations that adopt an existing database in place | `migrations/` |
| Preflight configuration check | `scripts/preflight.py` |
| CI: tests, migrations, frontend, hygiene, Terraform, image, scan | `.github/workflows/ci.yml` |
| Dashboard surfaces runs that never happened | `frontend/src/components/scheduler-failures.tsx` |
| Campus discovery in the runtime, human-review boundary intact | `KAIROS_ENABLE_BROWSER` |
| Grants.gov pagination, profile-aware keywords, date filtering | `agent/tools/discovery.py` |
| Discovery-recall benchmark with hand-authored ground truth | `tests/discovery_benchmark/` |
| Periodic reverification that reports rather than overwrites | `scripts/reverify.py` |
| Every API parameter bounded and validated at the edge | `api/main.py` |
| Redaction at **every** persistence boundary, not three of nine | `api/repository.py` |
| Credential and filesystem-path scrubbing on anything an API response carries | `agent/sanitize.py` |
| No secret, path or traceback in any unauthenticated response, log line or 500 | `tests/test_leak_surface.py` |

### Implemented but awaiting live validation

Written and reviewed. Never executed against the thing it targets. **Do not
describe any of these as working.**

| | Blocked on |
|---|---|
| Terraform: HTTPS-required production, private task networking, scoped Bedrock IAM, encrypted storage and logs, alarms, dead-letter queue | An AWS account. `fmt`, `validate` and `plan` have not been run — there is no Terraform binary in the authoring environment |
| Docker image: non-root uid 1000, migrations included | Docker. The image has never been built |
| EventBridge schedule, retry policy, DLQ delivery | A deployment |
| CloudWatch alarms and SNS delivery | A deployment, plus a confirmed email subscription |
| Deploy, rollback, backup restore, credential rotation | A deployment. Each is written in `docs/runbooks.md` and marked WRITTEN |
| `scripts/smoke_bedrock.py` against live Bedrock | AWS credentials and per-model access grants |
| Live golden-set result | The above. The current 100% groundedness figure is an offline defense-layer number, not a live-model one — it measures what the deterministic layer does with a stated model output, not what a real Drafter says |

### Blocked on credentials or approval

Cannot proceed without something only the repository owner can supply.

| | Needed |
|---|---|
| Any AWS work at all | An account, `aws configure`, and per-model Bedrock access granted in the console — Terraform cannot grant it |
| The two Bedrock model IDs | `aws bedrock list-foundation-models`; possibly `us.`-prefixed inference profiles |
| Real token prices | The live Bedrock pricing page for the target region. **Never invented** — a stale guess under-counts spend against a real cap |
| Model ARNs for the scoped IAM grant | The same CLI calls. Production Terraform refuses to plan without them |
| An ACM certificate | A domain. Production Terraform refuses to plan without one |
| Terraform remote state | An S3 bucket and DynamoDB lock table; names are account-specific (`infra/README.md`) |
| Branch protection | Repository settings. `.github/BRANCH_PROTECTION.md` records the recommendation and changes nothing |

### Still incomplete

Real work, not yet started or not yet finished.

Numbered below.

---

## Priority 0 — correctness and safety

1. **Validate every model path against live Bedrock.**
   Offline tests replace every model call with a fake that mirrors the real
   `AgentResult` shape. That catches an unbilled call; it cannot catch a
   wrong region, a revoked model grant, or a throttling behaviour that only
   appears under load. Run `scripts/smoke_bedrock.py` for both tiers, confirm
   the region-specific IDs, exercise a complete run, test throttling,
   confirm token accounting, and publish the live golden-set result
   **separately** from the fixture score.

2. **Confirm real token prices before any live run.**
   Prices default to zero, which makes every call cost $0.00 and the daily
   USD cap unenforceable. `/ready` reports this as
   `spend_cap: unenforceable` and production Terraform refuses to plan
   around it, but neither can supply the number. Only the per-run token
   ceiling is doing work until someone does.

## Priority 1 — make the core product real

3. **Finish the curated catalog.**
   59 rows, 52 verified quote by quote. The promise was 60–100 campus,
   nonprofit, fellowship and corporate programs. The shortfall is unfinished
   research rather than a rejected standard: three sweeps died on an API rate
   limit and one has yet to be re-run. The seven unverified rows are kept
   deliberately — one 404, three hosts that refuse automated clients, two
   JavaScript-rendered sites, one row whose rules live off-site — and all
   seven are excluded from runs.

4. **Model more real application forms.**
   Three real forms plus the synthetic demo one. Two of the three are marked
   `complete: false`, because their pages publish the shape of the
   application without the questions inside it. That leaves 2 of 59 catalog
   rows draftable (`scripts/form_coverage.py`). For many of the rest the
   questions appear only after registering, and registering is something the
   agent may never do — so those stay human work rather than a backlog item.

5. **Schedule the campus scraper.**
   `KAIROS_ENABLE_BROWSER` now adds a campus source to a Scout run, and it
   loads only human-`ACCEPTED` rows, so the review boundary survived
   integration. What is still missing is the other half: the scraper itself
   is operator-run, so nothing refreshes those rows on a schedule.

6. **Close the discovery-recall gap.**
   The benchmark exists and reports 85.7% retrieval recall and 83.3%
   eligibility coverage at 100% precision. The number to move is the second
   one — Grants.gov preserves eligibility prose but leaves most structured
   fields `UNKNOWN`, which limits the deterministic filter to fewer decisions
   than it could make. Precision is at 100%, so there is room to extract more
   without loosening anything.

7. **Extend the benchmark past its 20 programs.**
   It already reports duplicates, stale rows, deadline accuracy, eligibility
   coverage and precision, and form coverage alongside recall. What it cannot
   do is measure the funding universe: 20 hand-picked programs are a lower
   bound on ignorance, not a measure of it, and the set contains no federal
   rows, so it says nothing about Grants.gov recall.

## Priority 2 — production execution and reliability

8. **Apply and validate the AWS deployment.**
   The Terraform is written, reviewed and internally consistent. It has never
   been planned, never been applied, and its behaviour is therefore a claim
   about code rather than about anything observed. Validate the deployment,
   the HTTPS path, Bedrock permissions, persistence across task restarts, the
   scheduled invocation, the alarms and the DLQ.

9. **Run the rollback and restore drills.**
   Both are written in `docs/runbooks.md` and neither has been performed. A
   backup nobody has restored is a hypothesis. Do them against a scratch
   volume, with a real backup, and time them.

10. **Turn on `readonlyRootFilesystem`.**
    Deliberately left off. Fargate supports no tmpfs, so a read-only root
    means every scratch path — uv's cache, Python's temp files, SQLite's
    journal and WAL — has to live on a volume, and getting it wrong produces
    a task that starts and then fails on its first write. Worth doing against
    a real task rather than switched on untested.

11. **Move to a queue-backed worker if a second writer is ever needed.**
    `LocalJobExecutor` runs jobs in the API process, which is correct while
    the run lease and the single-task service both guarantee one. The
    `JobExecutor` protocol is the seam. Note that `desired_count = 1` is
    load-bearing: SQLite on EFS is single-writer, and the day two writers are
    genuinely needed the answer is RDS Postgres behind the same `Repository`
    protocol, not a second SQLite reader.

12. **Emit structured metrics rather than parsing logs.**
    The `run-halted` alarm reads a CloudWatch log metric filter on a log
    line. It works, and it is fragile in the specific way that a rename of a
    log key silently stops an alarm from ever firing again. `KAIROS_ENABLE_OTEL`
    is parsed and wired to nothing.

## Priority 3 — security and identity

13. **Narrow the CORS preview-deploy namespace.**
    `ALLOWED_ORIGIN_REGEX` admits any `kairos-*.vercel.app`, and Vercel
    allocates those subdomains first-come across accounts — so a third party
    who registers a project named `kairos-anything` holds a browser-trusted
    origin against this API. The only thing containing it is
    `allow_credentials` defaulting to `False`, which means no cookie or
    credential is attached cross-origin and the bearer token stays
    unreachable. **Do not enable `allow_credentials` while this regex is
    this wide.** The fix is an explicit allowlist of the preview URLs that
    actually exist, or a check against the Vercel deployment API. Note also
    that the pattern is unanchored and is safe only because the installed
    Starlette uses `fullmatch`; pin that behaviour with a test if the regex
    stays.

14. **Decide how much `/ready` should say to a stranger.**
    It is unauthenticated so a load balancer can reach it, and in production
    it reports `authentication: missing`, `schema: unmigrated` and
    `spend_cap: unenforceable`. Those are exactly the checks an operator
    needs and exactly the reconnaissance an attacker wants. It already
    withholds model IDs, paths and the token. The options are to keep it as
    is, to return a bare 503 with the detail behind a credential, or to put
    the probe on a separate internal-only listener.

15. **Decide on a real identity provider.**
    `api/auth.py` has the `Authenticator` seam and two implementations: a
    shared token (honest about proving only that somebody holds a secret) and
    a hashed credential file with revocation, expiry and restart-free
    rotation. An OIDC/JWT adapter is a product decision and is deliberately
    documented rather than faked.

16. **Ship the credential file from a secret store.**
    `KAIROS_CREDENTIALS_FILE` is read from disk and gitignored. Terraform
    provisions only the single shared token; mounting the credential file as
    a second secret is not yet written.

17. **Rotate on a schedule, not only on suspicion.**
    Rotation is documented and manual. Secrets Manager rotation is
    compatible with the design — the task reads the secret by ARN at start,
    and both the secret version and the EventBridge connection carry
    `ignore_changes` — but no rotation Lambda exists.

18. **Close the DNS-rebinding window in the scrape address guard.**
    `agent/scraping/netguard.py` resolves a host, checks every address it
    answers with, and refuses anything not publicly routable — on the URL
    given *and* on every redirect hop, which is the half that was missing
    while `follow_redirects=True` did the hopping. What it cannot close is
    the gap between the check and the connection: httpx resolves the name a
    second time to open the socket, and a record that is public on the first
    lookup and internal on the second still gets through.

    Closing it means resolving once, pinning that address, and connecting to
    it directly with a `Host` header — a transport-level change to how every
    fetch is made, not a bigger blocklist. Worth doing before the scraper
    runs anywhere with credentials worth stealing; the current guard is the
    cheap 90% and is written down here rather than assumed away.

---

## Completed foundations

Closed, and regression-tested. These should stay true.

- Opportunity, run, skip, draft, profile, job and scheduler-failure read
  endpoints exist and are founder-scoped.
- Inbox state can be updated without mutating the recorded verdict.
- Profiles use whole-object replacement with path *and* body id validation.
- Recorded assessments, rejections, drafts and run reports are immutable;
  corrections are a new run, never a rewrite.
- The frontend keeps every credential server-side and holds no
  `NEXT_PUBLIC_*` secret; CI fails if one appears.
- Every path, query and body parameter is bounded: list limits are
  `1..1000`, identifiers are non-empty and length-capped, enum-like inputs
  are closed sets, and the request bodies forbid unknown fields. A malformed
  request is a 4xx, never a 500, and a sweep asserts that so a route added
  later inherits the property.
- The adversarial suite covers what happens when the *model* misbehaves, not
  just when the input is hostile: fabricated citations, a real citation that
  supports nothing, negated evidence, spelled-out quantities, cross-founder
  recall isolation, safety-layer exceptions failing closed, malformed model
  output, abstention, and partial usage at budget crossings. It is verified
  by mutation — disabling a check fails specific tests — because a suite that
  passes against broken code is not testing anything.

### Two limits worth keeping in view

Both are fixed bugs whose *fixes* are still lexical, so both will misjudge
some inputs. Every misjudgment pushes a field to "you answer this", which is
the safe direction, and neither can produce an invented fact:

- `evidence_supports_claim` reads clause polarity, not meaning. Negation
  carried by structure rather than by a marker word ("far from settled")
  reads as positive.
- `extract_numbers` normalises digit and word forms to comparable values. It
  deliberately ignores standalone `one`/`zero` and imprecise plurals
  ("hundreds of students"), which assert no specific number to check.
- A run is a durable job, not a held-open connection. Crashes cannot leave
  one "running" forever.
- Two runs for the same founder cannot overlap.
- Two concurrent charges cannot both pass the daily cap.
- One founder cannot read or write another's anything, and a refusal is
  indistinguishable from a resource that does not exist.
