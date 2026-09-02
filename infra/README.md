# Kairos on AWS

Terraform for the backend. The frontend stays on Vercel — it is a Next.js
app and that is what Vercel is for; only `KAIROS_API_URL` connects the dashboard to the backend, set server-side in
the Vercel dashboard, never `NEXT_PUBLIC_*`. Production authenticates humans
with Supabase (`KAIROS_AUTH_MODE=supabase` plus the two `NEXT_PUBLIC_SUPABASE_*`
variables). Do **not** put `KAIROS_API_TOKEN` in Vercel: that shared backend
credential is how an unsigned visitor used to act as the founder. EventBridge
holds a separate `KAIROS_SCHEDULER_TOKEN` in Secrets Manager.

```
EventBridge Scheduler ──▶ ALB ──▶ ECS Fargate (1 task: FastAPI + pipeline) ──▶ Bedrock
   (daily, Bearer)  │        (HTTPS)        │                    │
        ┌───────────┘                       │                   EFS (/data: SQLite,
        ▼                                   │                    leases, spend ledger)
   SQS dead-letter ──▶ CloudWatch alarm ──▶ SNS
                                            ▲
Vercel (Next.js) ──────────────▶ ALB ───────┘
```

> **Nothing in this directory has been applied to a live AWS account.**
> It is reviewed, formatted and internally consistent; it is not validated.
> Every claim about how it behaves in production is a claim about the code,
> not about an observed deployment. See "What is actually verified" below.

## Two environments, and no way to confuse them

`var.environment` is required and has no default. Naming the environment is
the one decision that should never be inherited from whatever was in the
shell's history.

| | `demo` | `production` |
|---|---|---|
| HTTPS | optional | **required** — plan fails without `certificate_arn` |
| Port 80 | serves | redirects only |
| Task networking | public IP, ALB-only ingress | private subnets behind NAT, no inbound path |
| Bedrock IAM | `Resource = "*"` allowed | **required** `bedrock_model_arns` |
| Dollar cap | may be decorative | **required** real prices if `daily_usd_cap > 0` |
| ECR tags | mutable | immutable |
| Schema | app runs `create_all()` | `alembic upgrade head` at deploy; `/ready` fails if skipped |
| ECS Exec | enabled | disabled |
| EFS backup | off | on |
| ALB deletion protection | off | on |

The production requirements are `precondition` blocks, so they fail
`terraform plan`, not `terraform apply`. A misconfiguration caught in review
costs a minute; the same one caught after apply has already put a credential
on the wire.

Every resource is name-prefixed `kairos-<environment>` and tagged with it, so
a console full of resources never leaves you guessing which one takes real
traffic.

## Before the first apply: remote state

The state file contains the generated API token. Local state means that
credential lives unencrypted in a file on somebody's laptop, with no locking
to stop two applies colliding.

```bash
aws s3api create-bucket --bucket kairos-tfstate-<account-id> --region us-east-1
aws s3api put-bucket-versioning --bucket kairos-tfstate-<account-id> \
  --versioning-configuration Status=Enabled
aws s3api put-bucket-encryption --bucket kairos-tfstate-<account-id> \
  --server-side-encryption-configuration \
  '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"}}]}'
aws s3api put-public-access-block --bucket kairos-tfstate-<account-id> \
  --public-access-block-configuration \
  'BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true'

aws dynamodb create-table --table-name kairos-tflock \
  --attribute-definitions AttributeName=LockID,AttributeType=S \
  --key-schema AttributeName=LockID,KeyType=HASH \
  --billing-mode PAY_PER_REQUEST
```

The bucket name is account-specific, so it is not committed. Pass it at init:

```bash
terraform init \
  -backend-config="bucket=kairos-tfstate-<account-id>" \
  -backend-config="key=kairos/terraform.tfstate" \
  -backend-config="region=us-east-1" \
  -backend-config="dynamodb_table=kairos-tflock" \
  -backend-config="encrypt=true"
```

You will also need to add a `backend "s3" {}` block to the `terraform {}`
block in `main.tf`. It is deliberately absent so that `terraform init` in a
clone does not immediately demand an S3 bucket nobody has created.

## Deploy

```bash
cd infra

# Model IDs are discovered, never guessed (agent/config.py):
aws bedrock list-foundation-models --region us-east-1 \
  --query 'modelSummaries[?contains(modelId, `anthropic`)].modelId'

# And their ARNs, which is what scopes the IAM grant:
aws bedrock list-foundation-models --region us-east-1 \
  --query 'modelSummaries[?contains(modelId, `anthropic`)].modelArn'

terraform plan -var-file=production.tfvars    # read this
terraform apply -var-file=production.tfvars
```

A `production.tfvars` looks like this. **It is gitignored — never commit
one.**

```hcl
environment             = "production"
certificate_arn         = "arn:aws:acm:us-east-1:...:certificate/..."
bedrock_model_reasoning = "us.anthropic.claude-..."
bedrock_model_classify  = "us.anthropic.claude-..."
bedrock_model_arns = [
  "arn:aws:bedrock:us-east-1::foundation-model/anthropic.claude-...",
  "arn:aws:bedrock:us-east-1:<account>:inference-profile/us.anthropic.claude-...",
]
# Confirm against the live pricing page. There is no default price table
# anywhere in this repository, because a stale guess under-counts spend.
price_reasoning_in_per_mtok  = "3.00"
price_reasoning_out_per_mtok = "15.00"
price_classify_in_per_mtok   = "0.80"
price_classify_out_per_mtok  = "4.00"
alarm_email                  = "you@example.com"
image_tag                    = "sha-abc1234"   # not "latest"
supabase_issuer              = "https://<project-ref>.supabase.co/auth/v1"
```

Then build, migrate, and push:

```bash
aws ecr get-login-password | docker login --username AWS \
  --password-stdin "$(terraform output -raw ecr_repository_url | cut -d/ -f1)"
docker build -t "$(terraform output -raw ecr_repository_url):sha-$(git rev-parse --short HEAD)" ..
docker push "$(terraform output -raw ecr_repository_url):sha-$(git rev-parse --short HEAD)"

# The migration runs BEFORE the new task starts. In production the app no
# longer creates its own schema, so a deploy that skips this fails /ready
# with schema: unmigrated rather than booting on a half-invented schema.
# See docs/runbooks.md for running it as a one-off ECS task.

aws ecs update-service --cluster kairos-production \
  --service kairos-production-backend --force-new-deployment
```

Point the frontend at it (Vercel → Settings → Environment Variables).
Production must **not** set `KAIROS_API_TOKEN`:

```
KAIROS_API_URL                  = <terraform output backend_url>
KAIROS_AUTH_MODE                = supabase
NEXT_PUBLIC_SUPABASE_URL        = https://<project-ref>.supabase.co
NEXT_PUBLIC_SUPABASE_ANON_KEY   = <Supabase anon key — public by design>
```

The scheduler token stays in Secrets Manager and is sent only by EventBridge.
If it is ever copied into Vercel, every page proxy can spend it.

## What the outputs tell you

`terraform output` deliberately reports posture, not just addresses. Three of
them exist to make a bad configuration impossible to miss:

- `transport` — `https`, or `http (DEMO ONLY — the bearer token is in the clear)`
- `bedrock_access` — scoped to N ARNs, or `WILDCARD`
- `spend_cap` — enforced, off, or `UNENFORCEABLE` (a cap set with zero prices
  reads like a control in the dashboard while doing nothing)
- `alarm_subscription` — an email subscription is unconfirmed until the
  recipient clicks the link, and Terraform reports it as created either way

No output contains a secret. The token's *ARN* is safe to print; reading the
value behind it is a separate, audited call the operator makes deliberately.

## Alarms and the dead letter queue

Failure reporting used to stop at CloudWatch logs, which means it stopped at
"somebody thought to look". Five alarms now publish to an SNS topic:

| Alarm | Fires when | Why it matters |
|---|---|---|
| `no-healthy-task` | healthy targets < 1 for 3 min | Nothing is serving, no run can start. Missing data is treated as breaching. |
| `scheduled-run-failed` | an invocation fails after its retries | Separates "Kairos was quiet" from "Kairos has been broken for four days". |
| `dead-letter-received` | anything lands in the DLQ | The retries are exhausted and the run never happened. |
| `run-halted` | more than 2 halts in an hour | Not an outage — a halted run is a designed, reported outcome — but repeated halts mean the caps are wrong. |
| `backend-5xx` | > 5 target 5xx in 10 min | Something in the request path is broken. |

Scheduled invocations retry twice within the hour and then go to
`kairos-<env>-scheduler-dlq`, which keeps messages for 14 days. The queue is
SSE-encrypted and its policy denies anything not arriving over TLS.

## The Bedrock grant

`bedrock_model_arns` is what turns `Resource = "*"` into a scoped grant.
A wildcard is permission to invoke every model in the account, including ones
nobody has priced — which is the same failure mode as a dollar cap that
cannot fire, arriving from the other direction. Production refuses to plan
without it.

## Identity

The Terraform provisions EventBridge with a scheduler-only bearer token
(`KAIROS_SCHEDULER_TOKEN`), which can create a scheduled run for one founder
and is refused by every other endpoint. Humans authenticate with Supabase
user JWTs (`KAIROS_AUTH_MODE=supabase`, `KAIROS_SUPABASE_ISSUER`). Production
refuses to plan without the issuer, and the dashboard must never hold the
scheduler secret.

## Deliberate shortcuts, written down

- **Default VPC.** A bespoke VPC is not what makes this system safe and it is
  a lot of Terraform. What matters is where the task sits, and in production
  that is a private subnet with no inbound path.
- **`desired_count = 1`, and it must stay 1.** SQLite on EFS is single-writer
  in practice. The run lease (`agent/scheduler.py`) is the second line of
  defence, not the first. The day two writers are genuinely needed, the
  answer is RDS Postgres behind the same `Repository` protocol, not a second
  SQLite reader.
- **`readonlyRootFilesystem` is off.** Fargate supports no tmpfs, so a
  read-only root means every scratch path — uv's cache, Python's temp files,
  SQLite's journal and WAL — has to live on a volume. Worth doing, worth
  doing against a real task, and therefore in `incomplete.md` rather than
  switched on untested.
- **CORS is code, not config.** The Vercel origin list lives in
  `api/main.py::ALLOWED_ORIGINS`; a new production domain means a code
  change there, on purpose — an env-driven origin list is how a wildcard
  sneaks in.
- **Egress is open.** The task calls Bedrock, Grants.gov, Secrets Manager and
  CloudWatch. Pinning those to prefix lists is a maintenance burden that buys
  little when nothing can reach *in* except the ALB.

## What is actually verified

| | Status |
|---|---|
| HCL reviewed for dependency cycles and unknown-value `for_each` | done by hand |
| `terraform fmt -check` / `terraform validate` | **not run** — no Terraform binary in the authoring environment |
| `terraform plan` against an account | **not run** |
| Applied to AWS | **never** |
| HTTPS path, Bedrock permissions, EFS persistence across restarts, scheduled invocation, alarms, DLQ, rollback | **not exercised** |

Run these yourself before believing any of it:

```bash
cd infra
terraform fmt -check -recursive
terraform init -backend=false
terraform validate
terraform plan -var-file=production.tfvars
```

`docs/runbooks.md` has the deployment, rollback, restore, rotation and
runaway-spend procedures, with the same distinction between what has been
exercised and what has only been written down.
