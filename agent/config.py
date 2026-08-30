"""Configuration. Everything comes from the environment; nothing from memory.

Two rules from the spec meet here:

- *Never hardcode a model ID, endpoint, or ARN from memory.* Bedrock model
  IDs are region-specific and versioned. They are discovered with the AWS CLI
  and pasted into `.env` (the command is in `.env.example`).
- *No silent fallbacks.* A missing model ID raises at startup rather than
  defaulting to something plausible. A wrong-but-plausible ID produces a
  confusing runtime error hours later; an empty one produces a clear error now.

Deterministic code (`eligibility`, `guardrails`, `sanitize`) must not import
this module — those layers have to stay runnable in tests with no AWS
credentials and no `.env` at all.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

# Import-time side effect: a `.env` in the repo root is loaded into
# `os.environ` for every process that imports anything under `agent/`,
# including pytest. `load_dotenv()` does not override variables that are
# already set, so a real environment still wins — but an unset one is
# filled in from the developer's file.
#
# That makes some tests environment-dependent. `test_ready_flags_an_
# unenforceable_spend_cap_in_production` sets KAIROS_ENV, KAIROS_API_TOKEN
# and KAIROS_DAILY_USD_CAP and expects `spend_cap: unenforceable`, which
# requires the four KAIROS_PRICE_* variables to be unset. A developer whose
# `.env` sets live prices — as `.env.example` shows — sees that test fail
# locally and pass in CI, where there is no `.env`. Tests that depend on a
# variable being absent have to clear it explicitly with `monkeypatch.
# delenv(..., raising=False)`; they cannot assume the environment is empty.
load_dotenv()

REPO_ROOT = Path(__file__).resolve().parent.parent


class ConfigError(RuntimeError):
    """Raised when a required value is absent. Never swallowed."""


def _require(key: str, hint: str) -> str:
    """Read a required setting, or raise `ConfigError` naming it and how to find it.

    Blank counts as missing. That is deliberate and load-bearing: the README
    tells you to `cp .env.example .env`, which puts every key in the
    environment set to the empty string. If blank were treated as present,
    every one of those keys would read as configured and the failure would
    surface later as a Bedrock call against a model id of `""`.
    """
    value = os.getenv(key, "").strip()
    if not value:
        raise ConfigError(
            f"{key} is not set. {hint}\n"
            f"Copy .env.example to .env and fill it in. Values are not guessed."
        )
    return value


def stamp_placeholder_models(label: str) -> None:
    """Fill the model IDs with obviously-fake strings, for paths that never
    call a model.

    `os.environ.setdefault` is the wrong primitive here and using it was a
    bug: `cp .env.example .env` — step two of the README's own setup — puts
    `BEDROCK_MODEL_REASONING=` into the environment as an empty string, so the
    key exists, `setdefault` declines to touch it, and `_require` then refuses
    to start. That made `--dry-run` fail for exactly the person the dry run
    exists to serve: someone with a clean clone and no AWS account.

    Blank is missing, here as in `_require`. The label is stamped onto every
    `DraftField` these paths produce, so a fixture-derived answer says so.
    """
    for key in ("BEDROCK_MODEL_REASONING", "BEDROCK_MODEL_CLASSIFY"):
        if not os.getenv(key, "").strip():
            os.environ[key] = label
    settings.cache_clear()


def _int(key: str, default: int) -> int:
    """Read an int setting, falling back to `default` when unset or blank.

    A present-but-unparseable value raises `ValueError` rather than falling
    back — a typo in a numeric cap should stop the process, not silently
    restore a default the operator thought they had overridden.
    """
    raw = os.getenv(key, "").strip()
    return int(raw) if raw else default


def _float(key: str, default: float) -> float:
    """Read a float setting. Blank means default; unparseable raises, as in `_int`."""
    raw = os.getenv(key, "").strip()
    return float(raw) if raw else default


def _bool(key: str, default: bool = False) -> bool:
    """Read a boolean setting.

    True only for `1`, `true`, `yes`, `on` (case-insensitive). Everything else
    is False, so a typo like `KAIROS_ENABLE_BROWSER=treu` reads as off — the
    safe direction for every flag currently using this, all of which enable
    something when true.
    """
    raw = os.getenv(key, "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class ModelTier:
    """One Bedrock model plus the sampling discipline it runs under.

    Temperature discipline (Section 9, rule 10): extraction and
    classification run at 0. Only the Drafter's prose goes above it, and that
    output is still grounding-checked.
    """

    model_id: str
    temperature: float
    max_tokens: int


@dataclass(frozen=True)
class Prices:
    """USD per 1M tokens. Left at 0 until confirmed against live pricing.

    A 0 price makes `usd_estimate` read 0.0, which is visibly wrong rather
    than quietly wrong. Prices are never invented: there is no default table
    of "roughly what Anthropic charges" in this repository, because a stale
    guess would silently under-count spend against a real cap.

    The consequence is worth naming: with prices at 0 the daily USD cap can
    never trip, since every call costs $0.00. Only the per-run token ceiling
    is doing work. `/ready` reports that as `spend_cap: unenforceable` in
    production mode, and `configured` is the predicate it uses.
    """

    reasoning_in: float
    reasoning_out: float
    classify_in: float
    classify_out: float

    @property
    def configured(self) -> bool:
        """True when a live price has been supplied for both tiers.

        Output prices only: every call this system makes produces output, so
        a nonzero output price is enough to make the dollar cap real.
        """
        return self.reasoning_out > 0 and self.classify_out > 0


@dataclass(frozen=True)
class Settings:
    """The whole resolved configuration, frozen.

    Built once by `settings()` and cached. Frozen because a setting that can
    be mutated at runtime is a setting that a log line and the code that acted
    on it can disagree about.
    """

    region: str
    reasoning: ModelTier
    classify: ModelTier
    drafting_temperature: float
    prices: Prices

    max_run_tokens: int
    max_assessments: int
    daily_usd_cap: float
    #: Wall-clock ceiling on one pipeline run. The job executor cancels a
    #: run that outlives it; the lease TTL must comfortably exceed it.
    run_timeout_s: float

    grants_gov_base_url: str
    http_timeout_s: float
    enable_browser: bool
    allow_unverified_seed: bool

    db_url: str
    state_dir: Path
    enable_otel: bool
    api_token: str
    #: Path to a JSON credential file for multi-founder authorization. When
    #: set it supersedes `api_token` — it is the only one of the two that can
    #: tell founders apart. Tokens are stored hashed; see api/auth.py.
    credentials_file: str
    #: Supabase identity. `supabase_issuer` is `{project_url}/auth/v1` and is
    #: verified as the `iss` claim; exactly one of the two keys is supplied.
    #: When an issuer and a key are both set they supersede every other
    #: authenticator — they are the only one that identifies a real person.
    supabase_issuer: str
    supabase_jwt_secret: str
    supabase_public_key: str
    #: Serve requests with no credential at all, as `ANONYMOUS_LOCAL`. Off
    #: unless explicitly turned on, because the alternative — treating an
    #: absent token as "run open" — makes a forgotten variable indistinguishable
    #: from a deliberate localhost demo. Production refuses it regardless.
    allow_open_api: bool
    #: Production mode. Off by default so a clean clone stays runnable with
    #: no credentials. On, it stops being advisory: `/ready` fails without a
    #: token or without live prices, and the auth layer refuses anonymous
    #: identity outright. Set `KAIROS_ENV=production` to turn it on.
    environment: str

    @property
    def production(self) -> bool:
        """Whether this deployment is in the strict posture. See `environment`."""
        return self.environment == "production"

    @property
    def data_dir(self) -> Path:
        """Seed catalogs, form transcriptions and the demo profile."""
        return REPO_ROOT / "data"

    @property
    def prompts_dir(self) -> Path:
        """Sub-agent system prompts, loaded from disk at run time."""
        return REPO_ROOT / "agent" / "prompts"

    @property
    def fixtures_dir(self) -> Path:
        """Recorded HTTP fixtures. Test-only; nothing in the run path reads this."""
        return REPO_ROOT / "tests" / "fixtures"


@lru_cache(maxsize=1)
def settings() -> Settings:
    """Load and validate configuration. Cached; call it, don't copy it.

    The cache is what makes "call it, don't copy it" safe — every caller
    gets the same frozen object. It also means an environment change after
    the first call is invisible until `settings.cache_clear()`, which is
    why tests that monkeypatch `KAIROS_*` must clear it, and why
    `stamp_placeholder_models` clears it after writing.
    """
    hint_models = (
        "Discover it with: aws bedrock list-foundation-models "
        "--region $AWS_REGION --query "
        "'modelSummaries[?contains(modelId, `anthropic`)].modelId'"
    )
    state_dir = Path(os.getenv("KAIROS_STATE_DIR", ".kairos")).expanduser()

    return Settings(
        region=os.getenv("AWS_REGION", "us-east-1").strip(),
        reasoning=ModelTier(
            model_id=_require("BEDROCK_MODEL_REASONING", hint_models),
            temperature=0.0,
            max_tokens=_int("KAIROS_REASONING_MAX_TOKENS", 2048),
        ),
        classify=ModelTier(
            model_id=_require("BEDROCK_MODEL_CLASSIFY", hint_models),
            temperature=0.0,
            max_tokens=_int("KAIROS_CLASSIFY_MAX_TOKENS", 1024),
        ),
        # The one place above zero. Prose only, and still grounding-checked.
        drafting_temperature=_float("KAIROS_DRAFTING_TEMPERATURE", 0.4),
        prices=Prices(
            reasoning_in=_float("KAIROS_PRICE_REASONING_IN_PER_MTOK", 0.0),
            reasoning_out=_float("KAIROS_PRICE_REASONING_OUT_PER_MTOK", 0.0),
            classify_in=_float("KAIROS_PRICE_CLASSIFY_IN_PER_MTOK", 0.0),
            classify_out=_float("KAIROS_PRICE_CLASSIFY_OUT_PER_MTOK", 0.0),
        ),
        max_run_tokens=_int("KAIROS_MAX_RUN_TOKENS", 250_000),
        max_assessments=_int("KAIROS_MAX_ASSESSMENTS", 25),
        daily_usd_cap=_float("KAIROS_DAILY_USD_CAP", 3.0),
        run_timeout_s=_float("KAIROS_RUN_TIMEOUT_S", 1800.0),
        grants_gov_base_url=os.getenv(
            "GRANTS_GOV_BASE_URL", "https://api.grants.gov/v1/api"
        ).rstrip("/"),
        http_timeout_s=_float("KAIROS_HTTP_TIMEOUT_S", 15.0),
        enable_browser=_bool("KAIROS_ENABLE_BROWSER", False),
        allow_unverified_seed=_bool("KAIROS_ALLOW_UNVERIFIED_SEED", False),
        db_url=os.getenv("KAIROS_DB_URL", "sqlite:///./kairos.db"),
        state_dir=state_dir,
        enable_otel=_bool("KAIROS_ENABLE_OTEL", False),
        # Empty means the API runs open — acceptable only on localhost, and
        # only outside production mode, where `/ready` fails without it.
        api_token=os.getenv("KAIROS_API_TOKEN", "").strip(),
        credentials_file=os.getenv("KAIROS_CREDENTIALS_FILE", "").strip(),
        supabase_issuer=os.getenv("KAIROS_SUPABASE_ISSUER", "").strip(),
        supabase_jwt_secret=os.getenv("KAIROS_SUPABASE_JWT_SECRET", "").strip(),
        # PEM, newlines and all. Read from the environment as-is; a secret
        # store hands it over whole rather than a path to it.
        supabase_public_key=os.getenv("KAIROS_SUPABASE_PUBLIC_KEY", "").strip(),
        # Default False: an unconfigured deployment must fail closed. `_bool`
        # reads an unrecognised value as False, so a typo in the flag that
        # opens an API leaves it shut.
        allow_open_api=_bool("KAIROS_ALLOW_OPEN_API", False),
        environment=os.getenv("KAIROS_ENV", "local").strip().lower(),
    )
