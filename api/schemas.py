"""Request and response bodies owned by the HTTP layer.

Domain records (profiles, runs, drafts) live in `agent.models`; these are the
shapes that exist only because an HTTP caller sends or receives them.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field as PydanticField

from agent.models import (
    EligibilityAnswerValue,
    InboxState,
    IntakeDocument,
    IntakeMessage,
    IntakeSession,
)
from agent.scraping.models import ScrapedOpportunity
from api.deps import MAX_ID_LENGTH


#: Who asked for a run. Closed set: it is recorded on the job and on failure
#: log entries, so "did last night's *scheduled* run fail?" depends on the
#: value being trustworthy. An unrecognised value used to be silently
#: rewritten to "unknown", which threw away the caller's mistake.
RunSource = Literal["manual", "scheduled", "unknown"]


class RunTrigger(BaseModel):
    """Run request. Same code path whether a person or the scheduler asks.

    `idempotency_key` is how a retry resolves to the same logical
    invocation: EventBridge sends its execution id, the dashboard sends a
    generated one per click. `source` is recorded on the job and on any
    failure-log entry, so "did last night's *scheduled* run fail?" is
    answerable.

    `extra="forbid"`: a misspelled flag is a caller who thinks they asked for
    something. Accepting `use_demo_catalogue` and silently running against
    the real catalogue is the failure this prevents.
    """

    model_config = ConfigDict(extra="forbid")

    use_demo_catalog: bool = False
    include_grants_gov: bool = True
    idempotency_key: str | None = PydanticField(
        default=None, min_length=1, max_length=MAX_ID_LENGTH
    )
    source: RunSource = "unknown"


class InboxStateUpdate(BaseModel):
    """The one thing a person may change about a surfaced item."""

    model_config = ConfigDict(extra="forbid")

    state: InboxState


class EligibilityAnswerUpdate(BaseModel):
    """The founder's editable answer to one eligibility requirement."""

    model_config = ConfigDict(extra="forbid")

    answer: EligibilityAnswerValue


class IntakeFieldUpdate(BaseModel):
    """One explicit founder decision about one captured fact."""

    model_config = ConfigDict(extra="forbid")

    action: Literal["confirm", "correct", "reject"]
    expected_revision: int = PydanticField(ge=0)
    value: object | None = None
    client_action_id: str | None = PydanticField(default=None, min_length=1, max_length=200)


class IntakeClaimUpdate(BaseModel):
    """One explicit founder decision about a narrative memory claim."""

    model_config = ConfigDict(extra="forbid")

    action: Literal["confirm", "correct", "reject"]
    expected_revision: int = PydanticField(ge=0)
    text: str | None = PydanticField(default=None, max_length=4_000)
    category: str | None = PydanticField(default=None, max_length=100)
    client_action_id: str = PydanticField(min_length=1, max_length=200)


class IntakeRevision(BaseModel):
    """Optimistic revision required for terminal session transitions."""

    model_config = ConfigDict(extra="forbid")

    expected_revision: int = PydanticField(ge=0)


class IntakeBatchConfirmation(BaseModel):
    """Confirm exactly one proposal batch at one optimistic revision."""

    model_config = ConfigDict(extra="forbid")

    expected_revision: int = PydanticField(ge=0)


class IntakeMessageCreate(BaseModel):
    """One idempotent founder turn sent from the chat composer."""

    model_config = ConfigDict(extra="forbid")

    text: str = PydanticField(min_length=1, max_length=8_000)
    client_message_id: str = PydanticField(min_length=1, max_length=MAX_ID_LENGTH)
    expected_revision: int = PydanticField(ge=0)


class IntakeSessionView(BaseModel):
    """Everything the chat needs, already scoped to one founder."""

    model_config = ConfigDict(extra="forbid")

    session: IntakeSession
    messages: list[IntakeMessage]
    documents: list[IntakeDocument]
    missing_required: list[str]
    ready_to_complete: bool
    turn_pending: bool


class IntakeEvidenceView(BaseModel):
    """One founder-owned, sanitized evidence excerpt."""

    model_config = ConfigDict(extra="forbid")

    source_type: Literal["message", "document"]
    source_id: str
    location: str | None = None
    excerpt: str


class Identity(BaseModel):
    """Who the caller is, and which founder their dashboard should render.

    The frontend used to answer the second question from `KAIROS_FOUNDER_ID`,
    a single server-side variable. That is right for one founder and silently
    wrong for two: both people sign in as themselves and are shown the same
    inbox. This model is what replaces it.

    `founder_id` is the one to render, and it is chosen here rather than in
    the frontend so the choice is made once and is deterministic — the same
    session picks the same founder on every request, which iterating a
    `frozenset` would not guarantee. `founder_ids` carries the whole set for
    the cofounder case, where one person holds several.

    `subject` is the identity provider's opaque user id, never an email.
    `method` lets the dashboard tell an anonymous local session apart from
    somebody actually signed in.
    """

    subject: str
    founder_id: str | None
    founder_ids: list[str]
    can_write: bool
    method: str


CandidateLane = Literal["university", "general", "both"]


class ScraperCandidateGroup(BaseModel):
    """One candidate file, shaped for the dashboard."""

    lane: Literal["university", "general"]
    label: str
    source_file: str
    total: int
    candidates: list[ScrapedOpportunity]
