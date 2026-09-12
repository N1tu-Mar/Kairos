"""The deployment configuration contract: every variable, in one table.

Python settings, Terraform's container environment, the dashboard's server
environment and the operator docs all describe the same variables. Each of
those used to be edited by hand, and they drifted — a Terraform variable that
nothing read, keys missing from `.env.example`. This module is the list they
are checked against by `tests/test_deploy_contract.py`.

It is data, not a loader. `agent/config.py` still reads the environment and
still refuses to start on a bad value; nothing here changes a default.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Surface = Literal["backend", "frontend", "operator", "platform"]


@dataclass(frozen=True)
class DeploySetting:
    #: Environment variable name.
    env: str
    #: Who reads it: the API/worker process, the Next.js server, an operator
    #: script, or the hosting platform itself.
    surface: Surface
    #: The value used when unset, as environment text. None means required.
    default: str | None
    description: str
    #: Dotted attribute on `agent.config.Settings`, for backend settings.
    attr: str | None = None
    #: A credential. Never in Terraform `environment`, never `NEXT_PUBLIC_`.
    secret: bool = False
    #: Must be set for a production deployment to be safe.
    production_required: bool = False
    #: The exact HCL expression Terraform injects, `"secret"` when it comes
    #: from Secrets Manager, or None when Terraform does not set it.
    terraform: str | None = None
    #: Terraform deliberately injects something other than the Python
    #: default (a path on EFS, say). Checked as an explicit exception.
    terraform_overrides_default: bool = False


def _b(env, default, description, attr, **kw) -> DeploySetting:
    return DeploySetting(env, "backend", default, description, attr=attr, **kw)


CONTRACT: tuple[DeploySetting, ...] = (
    # ── Identity and posture ────────────────────────────────────────────
    _b("KAIROS_ENV", "local", "local or production. Production is strict and fail-closed.", "environment",
       production_required=True, terraform="var.environment"),
    _b("KAIROS_AUTH_MODE", "local_shared", "local_shared (laptop) or supabase (deployment).", "auth_mode",
       production_required=True, terraform='local.production ? "supabase" : "local_shared"'),
    _b("KAIROS_SUPABASE_ISSUER", "", "Supabase project URL plus /auth/v1; verified as iss.", "supabase_issuer",
       production_required=True, terraform="var.supabase_issuer"),
    _b("KAIROS_SUPABASE_JWT_SECRET", "", "Static HS256 key instead of JWKS. Prefer JWKS.", "supabase_jwt_secret",
       secret=True),
    _b("KAIROS_SUPABASE_PUBLIC_KEY", "", "Static PEM key instead of JWKS.", "supabase_public_key"),
    _b("KAIROS_API_TOKEN", "", "Shared bearer token for the single-founder local posture.", "api_token",
       secret=True, terraform="secret"),
    _b("KAIROS_CREDENTIALS_FILE", "", "Hashed multi-founder credential file.", "credentials_file"),
    _b("KAIROS_SCHEDULER_TOKEN", "", "EventBridge's credential; may only trigger runs for one founder.",
       "scheduler_token", secret=True, production_required=True, terraform="secret"),
    _b("KAIROS_SCHEDULER_FOUNDER_ID", "founder_demo", "The one founder the scheduler may run.",
       "scheduler_founder_id", terraform="var.founder_id"),
    _b("KAIROS_AUTO_PROVISION_FOUNDER", "true", "Create a founder for a verified user with none.",
       "auto_provision_founder"),
    _b("KAIROS_ALLOW_OPEN_API", "false", "Serve with no credential. Local demo only; production refuses.",
       "allow_open_api"),
    # ── Models and spend ────────────────────────────────────────────────
    _b("AWS_REGION", "us-east-1", "Region for Bedrock and every AWS call.", "region",
       terraform="var.aws_region"),
    _b("BEDROCK_MODEL_REASONING", None, "Bedrock model id for Assessor, Drafter, Auditor.", "reasoning.model_id",
       production_required=True, terraform="var.bedrock_model_reasoning"),
    _b("BEDROCK_MODEL_CLASSIFY", None, "Bedrock model id for the classification tier.", "classify.model_id",
       production_required=True, terraform="var.bedrock_model_classify"),
    _b("KAIROS_REASONING_MAX_TOKENS", "2048", "Max output tokens per reasoning call.", "reasoning.max_tokens"),
    _b("KAIROS_CLASSIFY_MAX_TOKENS", "1024", "Max output tokens per classification call.", "classify.max_tokens"),
    _b("KAIROS_DRAFTING_TEMPERATURE", "0.4", "Drafter temperature; every other agent runs at 0.",
       "drafting_temperature"),
    _b("KAIROS_PRICE_REASONING_IN_PER_MTOK", "0", "USD per 1M input tokens, reasoning tier.",
       "prices.reasoning_in", terraform="var.price_reasoning_in_per_mtok"),
    _b("KAIROS_PRICE_REASONING_OUT_PER_MTOK", "0", "USD per 1M output tokens, reasoning tier.",
       "prices.reasoning_out", terraform="var.price_reasoning_out_per_mtok"),
    _b("KAIROS_PRICE_CLASSIFY_IN_PER_MTOK", "0", "USD per 1M input tokens, classification tier.",
       "prices.classify_in", terraform="var.price_classify_in_per_mtok"),
    _b("KAIROS_PRICE_CLASSIFY_OUT_PER_MTOK", "0", "USD per 1M output tokens, classification tier.",
       "prices.classify_out", terraform="var.price_classify_out_per_mtok"),
    _b("KAIROS_DAILY_USD_CAP", "3.0", "Daily spend cap in USD; needs real prices to be enforceable.",
       "daily_usd_cap", terraform="var.daily_usd_cap"),
    _b("KAIROS_MAX_RUN_TOKENS", "250000", "Token ceiling per run.", "max_run_tokens"),
    _b("KAIROS_MAX_ASSESSMENTS", "25", "Assessor calls per run.", "max_assessments"),
    _b("KAIROS_ASSESSMENT_CONCURRENCY", "1", "Assessor calls in flight at once, 1 to 4.",
       "assessment_concurrency"),
    # ── Runs and abuse limits ───────────────────────────────────────────
    _b("KAIROS_RUN_TIMEOUT_S", "1800", "Wall-clock ceiling on one run; lease TTL is double.", "run_timeout_s"),
    _b("KAIROS_INTAKE_TURNS_PER_HOUR", "30", "Paid intake chat turns per founder per hour.",
       "intake_turns_per_hour"),
    _b("KAIROS_MANUAL_RUNS_PER_HOUR", "3", "Manual run triggers per principal per hour.", "manual_runs_per_hour"),
    _b("KAIROS_ELIGIBILITY_REASSESSMENTS_PER_HOUR", "10", "Answer-triggered reassessments per hour.",
       "eligibility_reassessments_per_hour"),
    _b("KAIROS_AUTHENTICATED_WRITES_PER_MINUTE", "60", "Founder-owned writes per minute.",
       "authenticated_writes_per_minute"),
    # ── Discovery ───────────────────────────────────────────────────────
    _b("GRANTS_GOV_BASE_URL", "https://api.grants.gov/v1/api", "Grants.gov API base.", "grants_gov_base_url"),
    _b("KAIROS_HTTP_TIMEOUT_S", "15", "Timeout per outbound discovery request.", "http_timeout_s"),
    _b("KAIROS_ENABLE_BROWSER", "false", "Campus source and Playwright. Production refuses true.",
       "enable_browser", terraform='"false"'),
    _b("KAIROS_ALLOW_UNVERIFIED_SEED", "false", "Allow unverified seed rows into runs.", "allow_unverified_seed"),
    # ── Storage and telemetry ───────────────────────────────────────────
    _b("KAIROS_DB_URL", "sqlite:///./kairos.db", "SQLAlchemy URL. SQLite means single task only.", "db_url",
       terraform='"sqlite:////data/kairos.db"', terraform_overrides_default=True),
    _b("KAIROS_STATE_DIR", ".kairos", "Spend ledger, run leases and failure log.", "state_dir",
       terraform='"/data/state"', terraform_overrides_default=True),
    _b("KAIROS_ENABLE_OTEL", "false", "Emit OpenTelemetry traces.", "enable_otel"),
    DeploySetting("OTEL_EXPORTER_OTLP_ENDPOINT", "operator", "", "Collector endpoint, read by the OpenTelemetry SDK."),
    # ── Operator-run scraping (never read by the API or a run) ──────────
    DeploySetting("BRAVE_SEARCH_API_KEY", "operator", "", "Search API key for the review scraper.", secret=True),
    DeploySetting("KAIROS_SEARCH_API_KEY", "operator", "", "Alternate name for the search API key.", secret=True),
    DeploySetting("FIRECRAWL_API_KEY", "operator", "", "Firecrawl key for the review scraper.", secret=True),
    DeploySetting("FIRECRAWL_BASE_URL", "operator", "https://api.firecrawl.dev/v2", "Firecrawl API base."),
    DeploySetting("FIRECRAWL_TIMEOUT_S", "operator", "60", "Firecrawl request timeout."),
    # ── Dashboard (Next.js server) ──────────────────────────────────────
    DeploySetting("KAIROS_API_URL", "frontend", "http://127.0.0.1:8000", "Backend base URL. Server-only."),
    DeploySetting("KAIROS_FOUNDER_ID", "frontend", "founder_demo", "Fallback founder for local mode."),
    DeploySetting("KAIROS_API_TOKEN", "frontend", "", "Shared backend token, local_shared only.", secret=True),
    DeploySetting("KAIROS_AUTH_MODE", "frontend", "local_shared", "Forced to supabase on Vercel deploys."),
    DeploySetting("KAIROS_API_TIMEOUT_MS", "frontend", "10000", "Timeout per backend call."),
    DeploySetting("NEXT_PUBLIC_SUPABASE_URL", "frontend", "", "Supabase project URL. Public by design.",
                  production_required=True),
    DeploySetting("NEXT_PUBLIC_SUPABASE_ANON_KEY", "frontend", "", "Supabase publishable key. Public by design.",
                  production_required=True),
    DeploySetting("VERCEL_ENV", "platform", "", "Set by Vercel; production/preview force supabase auth."),
    DeploySetting("NODE_ENV", "platform", "", "Set by Next.js."),
)


def by_surface(*surfaces: Surface) -> dict[str, DeploySetting]:
    """Settings for the given surfaces, keyed by environment variable."""
    return {s.env: s for s in CONTRACT if s.surface in surfaces}
