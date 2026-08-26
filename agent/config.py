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

load_dotenv()

REPO_ROOT = Path(__file__).resolve().parent.parent


class ConfigError(RuntimeError):
    """Raised when a required value is absent. Never swallowed."""


def _require(key: str, hint: str) -> str:
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
    raw = os.getenv(key, "").strip()
    return int(raw) if raw else default


def _float(key: str, default: float) -> float:
    raw = os.getenv(key, "").strip()
    return float(raw) if raw else default


def _bool(key: str, default: bool = False) -> bool:
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
    than quietly wrong. The daily cap still enforces on raw token counts.
    """

    reasoning_in: float
    reasoning_out: float
    classify_in: float
    classify_out: float


@dataclass(frozen=True)
class Settings:
    region: str
    reasoning: ModelTier
    classify: ModelTier
    drafting_temperature: float
    prices: Prices

    max_run_tokens: int
    max_assessments: int
    daily_usd_cap: float

    grants_gov_base_url: str
    http_timeout_s: float
    enable_browser: bool
    allow_unverified_seed: bool

    db_url: str
    state_dir: Path
    enable_otel: bool
    api_token: str

    @property
    def data_dir(self) -> Path:
        return REPO_ROOT / "data"

    @property
    def prompts_dir(self) -> Path:
        return REPO_ROOT / "agent" / "prompts"

    @property
    def fixtures_dir(self) -> Path:
        return REPO_ROOT / "tests" / "fixtures"


@lru_cache(maxsize=1)
def settings() -> Settings:
    """Load and validate configuration. Cached; call it, don't copy it."""
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
        grants_gov_base_url=os.getenv(
            "GRANTS_GOV_BASE_URL", "https://api.grants.gov/v1/api"
        ).rstrip("/"),
        http_timeout_s=_float("KAIROS_HTTP_TIMEOUT_S", 15.0),
        enable_browser=_bool("KAIROS_ENABLE_BROWSER", False),
        allow_unverified_seed=_bool("KAIROS_ALLOW_UNVERIFIED_SEED", False),
        db_url=os.getenv("KAIROS_DB_URL", "sqlite:///./kairos.db"),
        state_dir=state_dir,
        enable_otel=_bool("KAIROS_ENABLE_OTEL", False),
        # Empty means the API runs open — acceptable only on localhost.
        # api/main.py refuses nothing but logs the exposure at startup.
        api_token=os.getenv("KAIROS_API_TOKEN", "").strip(),
    )
