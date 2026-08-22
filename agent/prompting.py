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

import hashlib
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


async def structured_call(
    agent,
    output_model: type[T],
    prompt: str,
    *,
    agent_name: str,
    limits: dict[str, int] | None = None,
) -> T:
    """Invoke a Strands agent and validate its output against a Pydantic model.

    Uses `Agent.structured_output_async(output_model, prompt)`, verified
    against strands-agents 1.53.0. On a validation failure the error text is
    appended to the prompt and the call is retried; after
    `MAX_STRUCTURED_RETRIES` it raises `Abstention`.

    `limits` is passed through only when the agent supports it — `structured_output_async`
    does not take a `limits` argument, so budget enforcement for these calls
    happens in `RunBudget.charge_agent_result` after the fact.
    """
    attempt_prompt = prompt
    last_error = "no attempt was made"

    for attempt in range(MAX_STRUCTURED_RETRIES + 1):
        try:
            return await agent.structured_output_async(output_model, attempt_prompt)
        except ValidationError as exc:
            last_error = str(exc)
            attempt_prompt = (
                f"{prompt}\n\n"
                f"Your previous response failed schema validation on attempt "
                f"{attempt + 1}. Fix exactly these problems and return valid "
                f"output:\n{last_error}"
            )
        except Exception as exc:  # noqa: BLE001
            # Transport, throttling, or a provider-side parse failure. Same
            # policy: retry with the error visible, then abstain. Never
            # swallow it into a default value.
            last_error = f"{type(exc).__name__}: {exc}"
            attempt_prompt = (
                f"{prompt}\n\n"
                f"Your previous response could not be parsed (attempt "
                f"{attempt + 1}): {last_error}. Return valid structured output."
            )

    raise Abstention(agent_name, last_error)
