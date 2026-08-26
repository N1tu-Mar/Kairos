"""Prompt loading, prompt versioning, and the structured-output contract.

Every prompt lives in `agent/prompts/*.md` (Section 9, rule 7). No inline
multi-line strings anywhere in the codebase — the prompts are the actual
product and they need to be diffable in review.

`prompt_version` is the **git blob hash** of the prompt file, computed the
same way `git hash-object` computes it. That means a `FieldRecord` written
today can be traced to the exact prompt text that produced it, by a
`git cat-file -p <hash>`, months later. Computed in-process so nothing here
shells out or requires a git checkout.
"""

from __future__ import annotations

import asyncio
import hashlib
import random
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel, ValidationError

PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"

T = TypeVar("T", bound=BaseModel)


def git_blob_hash(content: bytes) -> str:
    """The hash `git hash-object` would print for this content."""
    header = f"blob {len(content)}\0".encode()
    return hashlib.sha1(header + content).hexdigest()


@dataclass(frozen=True)
class Prompt:
    name: str
    text: str
    version: str

    def __str__(self) -> str:
        return self.text


@lru_cache(maxsize=None)
def load_prompt(name: str) -> Prompt:
    """Load `agent/prompts/<name>.md` and stamp it with its blob hash."""
    path = PROMPTS_DIR / f"{name}.md"
    if not path.exists():
        raise FileNotFoundError(
            f"prompt {name!r} not found at {path}. Prompts are version-controlled "
            f"files, not inline strings."
        )
    raw = path.read_bytes()
    return Prompt(name=name, text=raw.decode("utf-8"), version=git_blob_hash(raw))


class Abstention(Exception):
    """A sub-agent legitimately declined to answer (Section 11.6).

    Raised after retries are exhausted. The orchestrator handles it as a
    real outcome — Assessor abstentions surface as "needs a human look",
    Drafter abstentions become NEEDS_FOUNDER fields, Auditor abstentions
    block the ready state. It is never converted into a best-effort string.
    """

    def __init__(self, agent_name: str, detail: str) -> None:
        super().__init__(f"{agent_name} abstained: {detail}")
        self.agent_name = agent_name
        self.detail = detail


#: Structured output or abstain (Section 9, rule 9). Two retries, each with
#: the validation error appended so the model can see exactly what it got
#: wrong, and then an abstention. Never a best-effort freeform string.
MAX_STRUCTURED_RETRIES = 2

#: Transient AWS conditions get their own, separate retry budget (Section
#: 11.12): exponential backoff, three attempts, then abort the run. They are
#: deliberately not counted against the schema retries above — a throttle
#: says nothing about whether the model can follow a schema, and burning a
#: schema attempt on it would make a busy region look like a broken prompt.
MAX_THROTTLE_ATTEMPTS = 3
THROTTLE_BASE_DELAY_S = 1.0

#: Service-side conditions worth waiting out. Anything not in this set —
#: AccessDenied, ValidationException, a bad model ID — is a real error and
#: must surface immediately rather than being retried into a delay.
TRANSIENT_AWS_CODES = frozenset(
    {
        "ThrottlingException",
        "TooManyRequestsException",
        "ServiceUnavailableException",
        "ServiceQuotaExceededException",
        "ModelNotReadyException",
        "ModelTimeoutException",
        "RequestTimeout",
        "RequestTimeoutException",
    }
)


class Throttled(Exception):
    """Bedrock throttled us past the backoff budget (Section 11.12).

    Deliberately *not* an `Abstention`. An abstention means the model looked
    at the material and declined; this means we never got an answer at all,
    and treating "the service was busy" as "the founder needs to look at this"
    would put a service incident in someone's inbox as if it were a judgment.
    Section 11.12's row for throttling reads "abort the run and report", so
    this propagates through the retry loop and halts.
    """

    def __init__(self, agent_name: str, detail: str) -> None:
        super().__init__(f"{agent_name} was throttled: {detail}")
        self.agent_name = agent_name
        self.detail = detail


def _never_retry() -> tuple[type[BaseException], ...]:
    """Exceptions that must escape the retry loop untouched.

    The retry loop exists to give a model a second chance at a schema. It is
    not a general error handler, and treating it as one is actively harmful:
    a budget cap that fires inside a model call would be caught, retried
    twice, and end up spending three times the ceiling it was supposed to
    enforce — turning the wallet guard into a wallet amplifier. Control-flow
    signals propagate.
    """
    from agent.budget import BudgetExceeded

    return (
        BudgetExceeded,
        Abstention,
        Throttled,
        asyncio.CancelledError,
        KeyboardInterrupt,
    )


@lru_cache(maxsize=1)
def _throttle_types() -> tuple[type[BaseException], ...]:
    """Imported lazily and cached — `strands` is heavy and the deterministic
    layers import this module only for `load_prompt`."""
    from botocore.exceptions import ClientError
    from strands.types.exceptions import ModelThrottledException

    return (ModelThrottledException, ClientError)


def _aws_error_code(exc: BaseException) -> str | None:
    """The `Error.Code` from a botocore `ClientError`, if there is one."""
    response = getattr(exc, "response", None)
    if not isinstance(response, dict):
        return None
    error = response.get("Error")
    if not isinstance(error, dict):
        return None
    code = error.get("Code")
    return code if isinstance(code, str) else None


def is_transient(exc: BaseException) -> bool:
    """Is this worth waiting out, or is it a real error?

    Strands wraps Bedrock's `ThrottlingException` in `ModelThrottledException`
    (verified in strands-agents 1.53.0, `models/bedrock.py`), but a raw
    `ClientError` can still reach us from a call that does not go through the
    model provider, so both are checked.
    """
    throttled, client_error = _throttle_types()
    if isinstance(exc, throttled):
        return True
    if isinstance(exc, client_error):
        return _aws_error_code(exc) in TRANSIENT_AWS_CODES
    return False


def backoff_delay(attempt: int) -> float:
    """1s, 2s, 4s, each with jitter in [0.5x, 1.0x].

    Jitter matters once more than one run is in flight: without it, every
    caller throttled by the same spike retries at the same instant and
    re-creates the spike it was backing off from.
    """
    return THROTTLE_BASE_DELAY_S * (2**attempt) * (0.5 + random.random() / 2)


def _retry_prompt(prompt: str, attempt: int, error: str) -> str:
    return (
        f"{prompt}\n\n"
        f"Your previous response could not be used (attempt {attempt + 1}): "
        f"{error}\nReturn valid structured output matching the schema."
    )


async def _invoke_with_backoff(
    agent,
    output_model: type[T],
    prompt: str,
    *,
    limits: dict[str, int] | None,
    agent_name: str,
):
    """One model invocation, waiting out transient service conditions.

    Returns the Strands `AgentResult` — the caller needs `.metrics` to charge
    the budget, not just the parsed output.
    """
    last: BaseException | None = None

    for attempt in range(MAX_THROTTLE_ATTEMPTS):
        try:
            return await agent.invoke_async(
                prompt, structured_output_model=output_model, limits=limits
            )
        except _never_retry():
            raise
        except Exception as exc:  # noqa: BLE001
            if not is_transient(exc):
                raise
            last = exc
            if attempt + 1 < MAX_THROTTLE_ATTEMPTS:
                await asyncio.sleep(backoff_delay(attempt))

    raise Throttled(agent_name, f"{type(last).__name__}: {last}")


async def structured_call(
    agent,
    output_model: type[T],
    prompt: str,
    *,
    agent_name: str,
    budget,
    tier: str,
) -> T:
    """Invoke a Strands agent, charge what it cost, and validate its output.

    Uses `Agent.invoke_async(prompt, structured_output_model=..., limits=...)`,
    verified against strands-agents 1.53.0. The older
    `Agent.structured_output_async` is deprecated in that version, but the
    reason for moving is not the warning: it returns the parsed model and
    nothing else, so there is no `AgentResult` and therefore no
    `metrics.accumulated_usage` to charge. Every model call in this codebase
    went unbilled for exactly that reason. `invoke_async` returns both.

    `budget` and `tier` are required rather than optional. An optional budget
    is a budget somebody eventually forgets to pass, and the failure is
    silent — the run completes, the ceiling reads zero, and the bill does not.
    """
    attempt_prompt = prompt
    last_error = "no attempt was made"

    for attempt in range(MAX_STRUCTURED_RETRIES + 1):
        try:
            result = await _invoke_with_backoff(
                agent,
                output_model,
                attempt_prompt,
                # Strands' own per-invocation cap, well below the run ceiling.
                # It bounds one runaway sub-agent; `budget` bounds the run.
                limits=budget.strands_limits(),
                agent_name=agent_name,
            )
        except _never_retry():
            raise
        except ValidationError as exc:
            last_error = str(exc)
            attempt_prompt = _retry_prompt(prompt, attempt, last_error)
            continue
        except Exception as exc:  # noqa: BLE001
            # A provider-side or transport failure that backoff did not
            # resolve. Retry with the error visible, then abstain. Never
            # swallow it into a default value.
            last_error = f"{type(exc).__name__}: {exc}"
            attempt_prompt = _retry_prompt(prompt, attempt, last_error)
            continue

        # Charge before inspecting the answer. A call that came back
        # unparseable still cost what it cost, and a ceiling that only counts
        # the successes is not a ceiling. This may raise `BudgetExceeded`,
        # which is in `_never_retry()` and halts the run.
        budget.charge_agent_result(result, tier=tier)

        stop_reason = getattr(result, "stop_reason", None)
        if isinstance(stop_reason, str) and stop_reason.startswith("limit_"):
            # Strands' per-call cap fired. Retrying spends more against a
            # limit that has already tripped, so this is an outcome, not a
            # thing to try again.
            raise Abstention(agent_name, f"per-call limit reached: {stop_reason}")

        parsed = getattr(result, "structured_output", None)
        if parsed is not None:
            return parsed

        last_error = f"no structured output returned (stop_reason={stop_reason!r})"
        attempt_prompt = _retry_prompt(prompt, attempt, last_error)

    raise Abstention(agent_name, last_error)
