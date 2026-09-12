# Deployment configuration

Generated from `agent/deploy_contract.py`. That module is the single list of
variables; `tests/test_deploy_contract.py` fails when Python, Terraform, the
dashboard, `.env.example` or this page disagree with it.

Columns:

- **Default** is the value used when unset. "required" means the process refuses to start without it.
- **Prod** marks a variable a production deployment must set.
- **Terraform** is the exact expression `infra/main.tf` injects into the task, "secret" for Secrets Manager, or blank.

## Single-task limitation

The current deployment is one ECS Fargate task with SQLite on EFS. Run jobs
execute as asyncio tasks inside that API process. Therefore:

- `desired_count` must stay `1`. A second task is a second SQLite writer and a
  second job executor that cannot see or cancel the first one's runs.
- Scaling out is not a configuration change. It needs the migration in
  `docs/ops/production-scaling-plan.md`.
- `scripts/preflight.py --env production` reports this as a `topology` warning,
  and the API logs it at startup in production.


## Backend (API process and run jobs)

| Variable | Default | Prod | Secret | Terraform | Purpose |
|---|---|---|---|---|---|
| `KAIROS_ENV` | `local` | yes |  | `var.environment` | local or production. Production is strict and fail-closed. |
| `KAIROS_AUTH_MODE` | `local_shared` | yes |  | `local.production ? "supabase" : "local_shared"` | local_shared (laptop) or supabase (deployment). |
| `KAIROS_SUPABASE_ISSUER` | empty | yes |  | `var.supabase_issuer` | Supabase project URL plus /auth/v1; verified as iss. |
| `KAIROS_SUPABASE_JWT_SECRET` | empty |  | yes |  | Static HS256 key instead of JWKS. Prefer JWKS. |
| `KAIROS_SUPABASE_PUBLIC_KEY` | empty |  |  |  | Static PEM key instead of JWKS. |
| `KAIROS_API_TOKEN` | empty |  | yes | secret | Shared bearer token for the single-founder local posture. |
| `KAIROS_CREDENTIALS_FILE` | empty |  |  |  | Hashed multi-founder credential file. |
| `KAIROS_SCHEDULER_TOKEN` | empty | yes | yes | secret | EventBridge's credential; may only trigger runs for one founder. |
| `KAIROS_SCHEDULER_FOUNDER_ID` | `founder_demo` |  |  | `var.founder_id` | The one founder the scheduler may run. |
| `KAIROS_AUTO_PROVISION_FOUNDER` | `true` |  |  |  | Create a founder for a verified user with none. |
| `KAIROS_ALLOW_OPEN_API` | `false` |  |  |  | Serve with no credential. Local demo only; production refuses. |
| `AWS_REGION` | `us-east-1` |  |  | `var.aws_region` | Region for Bedrock and every AWS call. |
| `BEDROCK_MODEL_REASONING` | required | yes |  | `var.bedrock_model_reasoning` | Bedrock model id for Assessor, Drafter, Auditor. |
| `BEDROCK_MODEL_CLASSIFY` | required | yes |  | `var.bedrock_model_classify` | Bedrock model id for the classification tier. |
| `KAIROS_REASONING_MAX_TOKENS` | `2048` |  |  |  | Max output tokens per reasoning call. |
| `KAIROS_CLASSIFY_MAX_TOKENS` | `1024` |  |  |  | Max output tokens per classification call. |
| `KAIROS_DRAFTING_TEMPERATURE` | `0.4` |  |  |  | Drafter temperature; every other agent runs at 0. |
| `KAIROS_PRICE_REASONING_IN_PER_MTOK` | `0` |  |  | `var.price_reasoning_in_per_mtok` | USD per 1M input tokens, reasoning tier. |
| `KAIROS_PRICE_REASONING_OUT_PER_MTOK` | `0` |  |  | `var.price_reasoning_out_per_mtok` | USD per 1M output tokens, reasoning tier. |
| `KAIROS_PRICE_CLASSIFY_IN_PER_MTOK` | `0` |  |  | `var.price_classify_in_per_mtok` | USD per 1M input tokens, classification tier. |
| `KAIROS_PRICE_CLASSIFY_OUT_PER_MTOK` | `0` |  |  | `var.price_classify_out_per_mtok` | USD per 1M output tokens, classification tier. |
| `KAIROS_DAILY_USD_CAP` | `3.0` |  |  | `var.daily_usd_cap` | Daily spend cap in USD; needs real prices to be enforceable. |
| `KAIROS_MAX_RUN_TOKENS` | `250000` |  |  |  | Token ceiling per run. |
| `KAIROS_MAX_ASSESSMENTS` | `25` |  |  |  | Assessor calls per run. |
| `KAIROS_ASSESSMENT_CONCURRENCY` | `1` |  |  |  | Assessor calls in flight at once, 1 to 4. |
| `KAIROS_RUN_TIMEOUT_S` | `1800` |  |  |  | Wall-clock ceiling on one run; lease TTL is double. |
| `KAIROS_INTAKE_TURNS_PER_HOUR` | `30` |  |  |  | Paid intake chat turns per founder per hour. |
| `KAIROS_MANUAL_RUNS_PER_HOUR` | `3` |  |  |  | Manual run triggers per principal per hour. |
| `KAIROS_ELIGIBILITY_REASSESSMENTS_PER_HOUR` | `10` |  |  |  | Answer-triggered reassessments per hour. |
| `KAIROS_AUTHENTICATED_WRITES_PER_MINUTE` | `60` |  |  |  | Founder-owned writes per minute. |
| `GRANTS_GOV_BASE_URL` | `https://api.grants.gov/v1/api` |  |  |  | Grants.gov API base. |
| `KAIROS_HTTP_TIMEOUT_S` | `15` |  |  |  | Timeout per outbound discovery request. |
| `KAIROS_ENABLE_BROWSER` | `false` |  |  | `"false"` | Campus source and Playwright. Production refuses true. |
| `KAIROS_ALLOW_UNVERIFIED_SEED` | `false` |  |  |  | Allow unverified seed rows into runs. |
| `KAIROS_DB_URL` | `sqlite:///./kairos.db` |  |  | `"sqlite:////data/kairos.db"` | SQLAlchemy URL. SQLite means single task only. |
| `KAIROS_STATE_DIR` | `.kairos` |  |  | `"/data/state"` | Spend ledger, run leases and failure log. |
| `KAIROS_ENABLE_OTEL` | `false` |  |  |  | Emit OpenTelemetry traces. |

## Dashboard (Next.js server)

| Variable | Default | Prod | Secret | Terraform | Purpose |
|---|---|---|---|---|---|
| `KAIROS_API_URL` | `http://127.0.0.1:8000` |  |  |  | Backend base URL. Server-only. |
| `KAIROS_FOUNDER_ID` | `founder_demo` |  |  |  | Fallback founder for local mode. |
| `KAIROS_API_TOKEN` | empty |  | yes |  | Shared backend token, local_shared only. |
| `KAIROS_AUTH_MODE` | `local_shared` |  |  |  | Forced to supabase on Vercel deploys. |
| `KAIROS_API_TIMEOUT_MS` | `10000` |  |  |  | Timeout per backend call. |
| `NEXT_PUBLIC_SUPABASE_URL` | empty | yes |  |  | Supabase project URL. Public by design. |
| `NEXT_PUBLIC_SUPABASE_ANON_KEY` | empty | yes |  |  | Supabase publishable key. Public by design. |

## Set by the platform

| Variable | Default | Prod | Secret | Terraform | Purpose |
|---|---|---|---|---|---|
| `VERCEL_ENV` | empty |  |  |  | Set by Vercel; production/preview force supabase auth. |
| `NODE_ENV` | empty |  |  |  | Set by Next.js. |

## Operator scripts only

| Variable | Default | Prod | Secret | Terraform | Purpose |
|---|---|---|---|---|---|
| `OTEL_EXPORTER_OTLP_ENDPOINT` | empty |  |  |  | Collector endpoint, read by the OpenTelemetry SDK. |
| `BRAVE_SEARCH_API_KEY` | empty |  | yes |  | Search API key for the review scraper. |
| `KAIROS_SEARCH_API_KEY` | empty |  | yes |  | Alternate name for the search API key. |
| `FIRECRAWL_API_KEY` | empty |  | yes |  | Firecrawl key for the review scraper. |
| `FIRECRAWL_BASE_URL` | `https://api.firecrawl.dev/v2` |  |  |  | Firecrawl API base. |
| `FIRECRAWL_TIMEOUT_S` | `60` |  |  |  | Firecrawl request timeout. |
