# Runbooks

Operational procedures for Kairos.

> **Read this first.** Every procedure below is **written, not exercised**.
> Nothing in this repository has been applied to a live AWS account. No
> deployment, no HTTPS path, no Bedrock call, no scheduled invocation, no
> alarm, no rollback and no restore has been observed working. The commands
> are correct as far as they can be reviewed; they are not correct as far as
> anyone has watched them run.
>
> The distinction is marked on every procedure:
>
> | Marker | Meaning |
> |---|---|
> | **LOCAL** | Run and verified on a developer machine |
> | **WRITTEN** | Reviewed, never executed against AWS |
>
> A backup you have never restored is a hypothesis. So is a rollback you
> have never performed. Run the drills in §9 against a scratch environment
> before you need them for real.

---

## 1. Preflight — before any deploy

**LOCAL.** Verified: the script runs and correctly fails a production
configuration that is missing model IDs, credentials, prices and migrations.

```bash
uv run scripts/preflight.py --env production
```

It checks configuration resolution, model IDs, credentials (including that a
credential file stores hashes and not raw tokens), whether the daily dollar
cap can actually fire, state-directory writability, the schema revision, and
that no credential or state file is tracked in git. Exit 1 means do not
deploy.

Nothing it does calls Bedrock or spends money. It mutates nothing.

Against an already-deployed backend, add the URL:

```bash
KAIROS_API_TOKEN=... uv run scripts/preflight.py \
  --env production --url https://api.example.com
```

That additionally probes `/health`, `/ready`, and — the one that matters —
that an *unauthenticated* read is refused. An open API that happens to have
no traffic is still open.

---

## 2. Plan the infrastructure

**WRITTEN.**

```bash
cd infra
terraform fmt -check -recursive
terraform init -backend-config=... # see infra/README.md
terraform validate
terraform plan -var-file=production.tfvars -out=tfplan
terraform show tfplan | less        # read it
```

Read the plan. Specifically:

- **No security group opening a port to `0.0.0.0/0` except 80 and 443 on the
  ALB.** Port 80 in production is a redirect and forwards nothing.
- **No `Resource = "*"` on `bedrock:InvokeModel`.** Production's precondition
  should have failed the plan already; if you are seeing a wildcard, you are
  not planning production.
- **`aws_secretsmanager_secret_version` is not being replaced.** It carries
  `ignore_changes`, so a replacement means something else moved and the API
  token is about to change under everything using it.
- **The task definition's image tag is a SHA, not `latest`.**

Then:

```bash
terraform apply tfplan
terraform output          # read `transport`, `bedrock_access`, `spend_cap`
```

Those three outputs are the posture check. `UNENFORCEABLE`, `WILDCARD` or
`http (DEMO ONLY ...)` in a production apply means stop.

---

## 3. Build and scan the image

**WRITTEN** (the image has never been built in this environment — there is no
Docker here).

```bash
REPO="$(terraform -chdir=infra output -raw ecr_repository_url)"
TAG="sha-$(git rev-parse --short HEAD)"

docker build -t "$REPO:$TAG" .

# Scan before pushing. A vulnerable image in ECR is a vulnerable image
# somebody deploys at 2am without re-checking.
trivy image --severity CRITICAL,HIGH --exit-code 1 "$REPO:$TAG"

aws ecr get-login-password | docker login --username AWS \
  --password-stdin "${REPO%%/*}"
docker push "$REPO:$TAG"
```

ECR also scans on push; `aws ecr describe-image-scan-findings --repository-name
... --image-id imageTag=$TAG` reads the result. The response policy is in
`docs/security.md`.

---

## 4. Migrate the database

**WRITTEN** for the ECS path. **LOCAL** for the migration itself, which is
covered by 20 tests including adoption of an existing database with live rows.

The migration runs **before** the new task starts. In production the
application no longer creates its own schema, so a deploy that skips this
fails `/ready` with `schema: unmigrated` rather than booting on a
half-invented one.

```bash
# 1. Back up first (§7). Always. A migration is the most likely reason
#    you will want the backup you are about to take.

# 2. Review the SQL without running it.
KAIROS_DB_URL="sqlite:////data/kairos.db" uv run alembic upgrade head --sql

# 3. Run it as a one-off task on the same EFS volume.
aws ecs run-task \
  --cluster kairos-production \
  --task-definition kairos-production-backend \
  --launch-type FARGATE \
  --network-configuration "awsvpcConfiguration={subnets=[$PRIVATE_SUBNETS],securityGroups=[$SERVICE_SG],assignPublicIp=DISABLED}" \
  --overrides '{"containerOverrides":[{"name":"backend","command":["uv","run","alembic","upgrade","head"]}]}'

# 4. Confirm before deploying the code that depends on it.
#    (via the same one-off mechanism, with command ["uv","run","alembic","current"])
```

The initial revision **adopts** an existing database rather than demanding a
fresh one — it creates only tables that are absent, so a pre-migrations
database keeps every run report, draft and profile. Running it twice is a
no-op. See `migrations/README.md`.

---

## 5. Deploy

**WRITTEN.**

```bash
terraform -chdir=infra apply -var-file=production.tfvars -var image_tag="$TAG"
aws ecs update-service --cluster kairos-production \
  --service kairos-production-backend --force-new-deployment

aws ecs wait services-stable --cluster kairos-production \
  --services kairos-production-backend
```

`desired_count` is 1 and `deployment_minimum_healthy_percent` is 0, so the
old task stops before the new one starts. **That is a deliberate outage of
tens of seconds**, because SQLite on EFS is single-writer and two tasks
sharing it is worse than a brief gap.

---

## 6. Smoke test

**WRITTEN** for the deployed path. **LOCAL** for `smoke_bedrock.py`, which
exists and runs but has never reached live Bedrock.

```bash
BASE="$(terraform -chdir=infra output -raw backend_url)"
TOKEN="$(aws secretsmanager get-secret-value \
  --secret-id "$(terraform -chdir=infra output -raw api_token_secret_arn)" \
  --query SecretString --output text)"

curl -fsS "$BASE/health"                      # -> {"status":"ok"}
curl -fsS "$BASE/ready" | jq .                # every check "ok"
curl -s -o /dev/null -w '%{http_code}\n' "$BASE/founders/founder_demo"   # -> 401
curl -fsS -H "Authorization: Bearer $TOKEN" "$BASE/founders/founder_demo" | jq .founder_id

# One real run, end to end. This spends money.
curl -fsS -X POST -H "Authorization: Bearer $TOKEN" -H 'content-type: application/json' \
  -d '{"use_demo_catalog":true,"include_grants_gov":false,"source":"manual","idempotency_key":"smoke-'"$(date +%s)"'"}' \
  "$BASE/founders/founder_demo/runs" | tee /tmp/job.json

JOB="$(jq -r .job_id /tmp/job.json)"
curl -fsS -H "Authorization: Bearer $TOKEN" "$BASE/founders/founder_demo/jobs/$JOB" | jq .job.status
```

`202` and a job id is success. Poll until the status is terminal. `halted`
is a *finished* run with a report — a budget cap or a throttle — not a
failure.

Then unset `TOKEN`. It is in your shell history and your scrollback either
way; do not also leave it in the environment.

---

## 7. Verify the schedule actually fires

**WRITTEN.** This is the check most likely to be skipped and most likely to
matter: a deployment where everything works except the schedule looks
identical to a deployment where nothing has been found worth surfacing.

```bash
# What the scheduler thinks it is doing.
aws scheduler get-schedule --name kairos-production-daily-run

# Force one now rather than waiting until 07:00 UTC.
aws scheduler update-schedule --name kairos-production-daily-run \
  --schedule-expression "at($(date -u -v+3M +%Y-%m-%dT%H:%M:%S))" ...
# ...then put the cron back afterwards. Do not leave a one-shot schedule in place.

# Did a job appear, and does it say `source: scheduled`?
curl -fsS -H "Authorization: Bearer $TOKEN" \
  "$BASE/founders/founder_demo/jobs" | jq '.[0] | {job_id, source, status}'

# Did anything fail to start?
curl -fsS -H "Authorization: Bearer $TOKEN" \
  "$BASE/founders/founder_demo/scheduler/failures" | jq .

# Is the dead-letter queue empty?
aws sqs get-queue-attributes \
  --queue-url "$(terraform -chdir=infra output -raw scheduler_dlq_url)" \
  --attribute-names ApproximateNumberOfMessagesVisible
```

A message in the DLQ means the invocation exhausted its retries. The body
carries the failure reason.

---

## 8. Rollback

**WRITTEN.** Never performed.

**Rolling back code is not the same as rolling back a schema**, and doing the
first while assuming the second is how a rollback makes an incident worse.

### Code only (the schema did not change)

```bash
# Find the previous good task definition revision.
aws ecs list-task-definitions --family-prefix kairos-production-backend \
  --sort DESC --max-items 5

aws ecs update-service --cluster kairos-production \
  --service kairos-production-backend \
  --task-definition kairos-production-backend:<previous-revision>

aws ecs wait services-stable --cluster kairos-production \
  --services kairos-production-backend
```

Confirm with `curl -fsS "$BASE/ready"`.

### The schema changed

**Do not run `alembic downgrade` on a database with data you care about.**
The initial revision's `downgrade` drops every table — it exists so the
revision is complete and round-trippable in tests, not as a rollback
procedure.

The rollback is: stop the service, restore the backup taken in §4 before the
migration, deploy the previous image, start the service. That is §9.

This is why the two rules in `migrations/README.md` matter — never drop a
column in the same revision that stops writing to it, and never add `NOT
NULL` without a backfill. Both exist so that most rollbacks are code-only.

---

## 9. Backup and restore

**LOCAL** for the SQLite commands (they are standard and were reviewed).
**WRITTEN** for the ECS orchestration. Never performed end to end.

### Back up

```bash
# From a one-off task on the same volume. The online backup API, not `cp`:
# copying the file while a writer is mid-transaction produces a file that
# may not open.
sqlite3 /data/kairos.db ".backup '/data/backups/kairos-$(date -u +%Y%m%dT%H%M%SZ).db'"

# The state directory holds the spend ledger, the run leases and the
# scheduler failure log. Losing the ledger means losing today's proof that
# you are under the daily cap — and DailyLedger refuses to spend without
# one rather than resetting to zero, so this is an availability concern too.
tar czf "/data/backups/state-$(date -u +%Y%m%dT%H%M%SZ).tgz" /data/state
```

EFS also has AWS Backup enabled in production (daily recovery points, 35 day
retention). That is the safety net; the explicit backup above is the one you
take *before* a migration, because a nightly snapshot is up to 24 hours old.

### Verify the backup — before you need it

```bash
sqlite3 /data/backups/kairos-<stamp>.db "PRAGMA integrity_check;"   # -> ok
sqlite3 /data/backups/kairos-<stamp>.db "SELECT count(*) FROM runs;"
sqlite3 /data/backups/kairos-<stamp>.db "SELECT version_num FROM alembic_version;"
```

The third one matters: a backup at an older revision needs the code from that
revision, or a fresh `upgrade head` after restoring.

### Restore

```bash
# 1. STOP THE SERVICE. A restore under a live writer is a corrupt database,
#    not a restore.
aws ecs update-service --cluster kairos-production \
  --service kairos-production-backend --desired-count 0
aws ecs wait services-stable --cluster kairos-production \
  --services kairos-production-backend

# 2. Replace, keeping the bad database rather than deleting it — you may
#    need to explain what happened later.
mv /data/kairos.db "/data/kairos.db.broken-$(date -u +%Y%m%dT%H%M%SZ)"
cp /data/backups/kairos-<stamp>.db /data/kairos.db
tar xzf /data/backups/state-<stamp>.tgz -C /

# 3. Bring the schema forward if the backup predates the current code.
#    (one-off task: ["uv","run","alembic","upgrade","head"])

# 4. Start.
aws ecs update-service --cluster kairos-production \
  --service kairos-production-backend --desired-count 1
```

Then check `/ready` and confirm the run history looks like the backup, not
like an empty database.

**The drill:** do this against a scratch EFS volume, with a real backup, and
time it. An untested restore is not a recovery plan.

---

## 10. Rotate the API token

**WRITTEN.**

The task reads the secret by ARN at container start, so rotation is a secret
value change plus a restart. No Terraform apply, no ARN churn.

```bash
NEW="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
ARN="$(terraform -chdir=infra output -raw api_token_secret_arn)"

# 1. New secret value.
aws secretsmanager put-secret-value --secret-id "$ARN" --secret-string "$NEW"

# 2. EventBridge holds its own copy in the connection's auth header.
#    Update it in the same window or the next scheduled run gets a 401.
aws events update-connection --name kairos-production-backend \
  --auth-parameters "ApiKeyAuthParameters={ApiKeyName=Authorization,ApiKeyValue=Bearer $NEW}"

# 3. Restart so the task picks it up.
aws ecs update-service --cluster kairos-production \
  --service kairos-production-backend --force-new-deployment

# 4. Update the frontend host (Vercel → Settings → Environment Variables →
#    KAIROS_API_TOKEN) and redeploy it.

unset NEW
```

There is a window between (1) and (3) where the old token still works,
because the running task holds it in memory. That is deliberate: it means
rotation is not an outage. It also means **rotation alone does not revoke** —
if you are rotating because a token leaked, do step (3) immediately and treat
the gap as exposure.

Both `aws_secretsmanager_secret_version` and `aws_cloudwatch_event_connection`
carry `ignore_changes` on their values, so the next `terraform apply` will not
helpfully reset them back to what is in state.

### Revoking one credential of several

With `KAIROS_CREDENTIALS_FILE`, revocation does not need a restart at all:
set `"revoked": true` on the entry and write the file. It is re-read when its
mtime changes, so the next request is refused. Revoke rather than delete — a
revoked entry keeps the audit trail able to say "this token was used, and it
had been revoked".

---

## 11. Incident: runaway spend

**WRITTEN.**

**Symptoms:** the `run-halted` alarm firing repeatedly, a Bedrock bill
climbing, or `usd_estimate` on recent runs far above normal.

**Stop the bleeding first. Diagnose second.**

```bash
# 1. Stop the schedule. This is the fastest single action and it is
#    reversible.
aws scheduler update-schedule --name kairos-production-daily-run --state DISABLED

# 2. If a run is in flight, cancel it.
curl -fsS -H "Authorization: Bearer $TOKEN" \
  "$BASE/founders/founder_demo/jobs" | jq -r '.[] | select(.status=="running") | .job_id'
curl -fsS -X POST -H "Authorization: Bearer $TOKEN" \
  "$BASE/founders/founder_demo/jobs/<job_id>/cancel"

# 3. If that is not enough, take the service down. Nothing can call Bedrock
#    if nothing is running.
aws ecs update-service --cluster kairos-production \
  --service kairos-production-backend --desired-count 0
```

Then diagnose:

```bash
# What today's ledger says. This is the number the cap enforces against.
sqlite3 /data/state/daily_spend.sqlite3 "SELECT * FROM daily_spend ORDER BY day DESC LIMIT 7;"

# What the runs themselves recorded.
curl -fsS -H "Authorization: Bearer $TOKEN" "$BASE/founders/founder_demo/runs?limit=20" \
  | jq '.[] | {run_id, started_at, tokens: .usage.total_tokens, usd: .usage.usd_estimate, halted_reason}'
```

**The most likely cause is that the cap was never real.** With token prices
at 0 every call costs $0.00, the daily USD cap can never trip, and only the
per-run token ceiling is doing anything. Check:

```bash
curl -fsS "$BASE/ready" | jq .checks.spend_cap    # "unenforceable" means exactly this
```

Fix by setting real prices (`terraform apply` with the price variables) and
lowering `KAIROS_MAX_RUN_TOKENS` until you understand the spend. Then
re-enable the schedule.

---

## 12. Incident: repeated scheduled failures

**WRITTEN.**

**Symptoms:** `dead-letter-received` or `scheduled-run-failed` alarm, or the
dashboard's "Runs that did not happen" panel showing entries.

```bash
# What the API recorded. Sanitised — no credentials, no prompts.
curl -fsS -H "Authorization: Bearer $TOKEN" \
  "$BASE/founders/founder_demo/scheduler/failures?limit=20" | jq .
```

The `failure_class` says where to look:

| Class | Meaning | Usually |
|---|---|---|
| `startup` | The run could not begin | Missing model ID, no profile, or Bedrock access revoked |
| `timeout` | Exceeded `KAIROS_RUN_TIMEOUT_S` | An upstream source hanging, or a genuinely slow run |
| `crash` | An exception escaped the pipeline | A real bug; the log group has the traceback |
| `orphaned` | The process died mid-run | The task was replaced — a deploy, or an OOM |

Then the DLQ, which holds what EventBridge could not deliver:

```bash
aws sqs receive-message --queue-url "$(terraform -chdir=infra output -raw scheduler_dlq_url)" \
  --max-number-of-messages 10 --visibility-timeout 30 | jq -r '.Messages[].Body' | jq .
```

A `401` in there means the token rotated without the EventBridge connection
being updated — §10, step 2.

Nothing needs to be replayed by hand: the next schedule fires normally. Do
**not** re-drive DLQ messages into the endpoint. Each carries an
idempotency key from its original execution, so a replay resolves to the
original job rather than doing the work — which is correct, and also means
replaying accomplishes nothing.

### If a run is stuck

A job that says `running` with no process behind it is repaired at startup —
`recover_orphaned_jobs` marks every queued/running row failed before the API
accepts anything new. So a restart clears it:

```bash
aws ecs update-service --cluster kairos-production \
  --service kairos-production-backend --force-new-deployment
```

The lease that job held expires on its own TTL (twice the run timeout, one
hour by default). It is deliberately *not* force-released at startup, because
nothing can prove the stale row and the stale lease belonged to the same
invocation.

---

## 13. What has never been verified

Repeating this because it is the most important thing on the page.

| | Status |
|---|---|
| `scripts/preflight.py` | **LOCAL** — runs, correctly fails a bad production config |
| Migrations (fresh, adoption, idempotency, rollback of schema) | **LOCAL** — 20 tests |
| Python test suite | **LOCAL** |
| Frontend tests, typecheck, lint, build | **LOCAL** |
| `terraform fmt` / `validate` / `plan` | **NOT RUN** — no Terraform binary available |
| `docker build` | **NOT RUN** — no Docker available |
| Any AWS resource | **NEVER APPLIED** |
| Live Bedrock call | **NEVER MADE** |
| HTTPS path, scheduled invocation, alarms, DLQ | **NEVER EXERCISED** |
| Rollback, restore, rotation | **NEVER PERFORMED** |
