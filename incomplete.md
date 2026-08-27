# Incomplete work

Current as of 2026-08-27.

**Local health.** 776 Python tests pass with no `xfail` remaining; 54
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
| Live golden-set result | The above. The current 80% groundedness figure is an offline defense-layer number, not a live-model one |

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
   40 rows, 34 verified quote by quote. The promise was 60–100 campus,
   nonprofit, fellowship and corporate programs.

4. **Model more real application forms.**
   Four structured forms exist. Discovering and assessing a fund is not
   enough to draft its application without the actual questions, field
   types, limits and certification fields.

5. **Schedule the campus scraper.**
   `KAIROS_ENABLE_BROWSER` now adds a campus source to a Scout run, and it
   loads only human-`ACCEPTED` rows, so the review boundary survived
   integration. What is still missing is the other half: the scraper itself
   is operator-run, so nothing refreshes those rows on a schedule.

6. **Close the discovery-recall gap.**
   The benchmark exists and reports 85.7% retrieval recall and 72.2%
   eligibility coverage at 100% precision. The number to move is the second
   one — Grants.gov preserves eligibility prose but leaves most structured
   fields `UNKNOWN`, which limits the deterministic filter to fewer decisions
   than it could make. Precision is at 100%, so there is room to extract more
   without loosening anything.

7. **Extend the benchmark past retrieval.**
   It measures what was found. It does not yet measure duplicate handling or
   stale-record behaviour, and both are failure modes a founder would notice
   before a recall number moved.

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

13. **Decide on a real identity provider.**
    `api/auth.py` has the `Authenticator` seam and two implementations: a
    shared token (honest about proving only that somebody holds a secret) and
    a hashed credential file with revocation, expiry and restart-free
    rotation. An OIDC/JWT adapter is a product decision and is deliberately
    documented rather than faked.

14. **Ship the credential file from a secret store.**
    `KAIROS_CREDENTIALS_FILE` is read from disk and gitignored. Terraform
    provisions only the single shared token; mounting the credential file as
    a second secret is not yet written.

15. **Rotate on a schedule, not only on suspicion.**
    Rotation is documented and manual. Secrets Manager rotation is
    compatible with the design — the task reads the secret by ARN at start,
    and both the secret version and the EventBridge connection carry
    `ignore_changes` — but no rotation Lambda exists.

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
- A run is a durable job, not a held-open connection. Crashes cannot leave
  one "running" forever.
- Two runs for the same founder cannot overlap.
- Two concurrent charges cannot both pass the daily cap.
- One founder cannot read or write another's anything, and a refusal is
  indistinguishable from a resource that does not exist.
