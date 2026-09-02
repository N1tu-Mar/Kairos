"""Core data models for Kairos.

Every model here is a Pydantic v2 model, and every sub-agent returns one of
them validated against its schema (Section 9, rule 9). Nothing in this file
imports `strands` — these types are the contract between the deterministic
layer and the model layer, and the deterministic layer must stay importable
without an AWS session.

Two conventions carry most of the safety weight:

1.  **Three-valued logic, never a boolean** (Section 11.3). `None` on a
    structured eligibility field means "the source text did not state this",
    which is a different fact from "this is false". Collapsing those two is
    how an agent decides a founder is eligible for something they are not.

2.  **Every generated claim carries a receipt** (Section 11.8). A
    `DraftField` with `status == "GENERATED"` and an empty `provenance`
    list is a bug, and `ship_gate()` treats it as one.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

# ── Vocabularies ─────────────────────────────────────────────────────────────

DegreeLevel = Literal["undergrad", "masters", "phd", "postdoc"]
EntityType = Literal["none", "llc", "c_corp", "s_corp", "nonprofit"]
Stage = Literal["idea", "prototype", "mvp", "pilot", "revenue"]
IntakeFieldName = Literal[
    "startup_description",
    "full_name",
    "degree_level",
    "institution",
    "major",
    "citizenship",
    "entity_type",
    "team_size",
    "stage",
    "traction",
    "funding_range",
    "equity_ok",
    "has_faculty_advisor",
    "max_application_hours",
    "geographies",
]
IntakeFieldStatus = Literal["missing", "proposed", "confirmed"]
IntakeSessionStatus = Literal["active", "completed", "abandoned"]
IntakeMessageRole = Literal["founder", "assistant"]
IntakeDocumentStatus = Literal["processing", "ready", "rejected"]

#: Three-valued eligibility. `UNKNOWN` never silently passes and never
#: silently fails — it becomes a question for the founder (Section 11.3).
Eligibility = Literal["ELIGIBLE", "INELIGIBLE", "UNKNOWN"]

#: `INSUFFICIENT_INFO` is the Assessor's abstention path (Section 11.6).
#: An abstention is a correct answer. A guess is not.
Verdict = Literal["APPLY", "MAYBE", "SKIP", "INSUFFICIENT_INFO"]

FieldStatus = Literal["KNOWN", "GENERATED", "NEEDS_FOUNDER", "REUSED"]

#: What the founder has done with a surfaced item. The pipeline only ever
#: writes `new`; every later value comes from a person.
InboxState = Literal["new", "opened", "dismissed", "applied"]
EligibilityAnswerValue = Literal["yes", "no", "not_sure"]
EligibilityQuestionStatus = Literal["pending", "answered"]
AuditVerdict = Literal["SUPPORTED", "UNSUPPORTED", "UNVERIFIABLE"]
DraftStatus = Literal["DRAFT", "READY", "BLOCKED"]
SourceName = Literal["seed", "grants_gov", "browser"]


def _now() -> datetime:
    """Timezone-aware UTC now. Every `created_at`/`started_at` default goes through this so two records are always comparable."""
    return datetime.now(timezone.utc)


class Frozen(BaseModel):
    """Base for records that are written once and then read.

    Drafts mutate during a run; opportunities and knowledge chunks do not.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")


class Mutable(BaseModel):
    """Base for records that change while a run is in flight.

    Still `extra="forbid"`: a field name a model invented, or a caller's typo,
    is a validation error rather than a silently-ignored key.
    """

    model_config = ConfigDict(extra="forbid")


# ── Provenance primitives ────────────────────────────────────────────────────


class SourceSpan(Frozen):
    """A verbatim pointer back into source material.

    `text` is quoted, not paraphrased. If a span cannot be located in the
    source, the caller must emit `UNKNOWN` rather than a span — there is no
    "inferred" tier (Section 11.2).
    """

    chunk_id: str
    source: str = Field(description='e.g. "pitch_deck.pdf p.4" | "onboarding_q3"')
    text: str = Field(description="Verbatim span, unmodified.")
    char_start: int | None = None
    char_end: int | None = None


class ExtractedCriterion(Frozen):
    """An eligibility/award/deadline criterion lifted verbatim from a source.

    Rendered into founder-facing text by templating, never by asking a model
    to write a sentence about it (Section 11.2).
    """

    text: str
    source_doc: str
    char_start: int | None = None
    char_end: int | None = None


class KnowledgeChunk(Frozen):
    """One provenance-tagged fact about the founder.

    The closed world for drafting. If a claim is not supported by a chunk in
    here, the Drafter may not make it.
    """

    #: Longest a single fact may be. A chunk is one provenance-tagged claim —
    #: a paragraph, not a document — and the Drafter's closed-world check gets
    #: less useful the larger they are. Generous enough for a long answer to a
    #: long application question, which is the biggest real one.
    MAX_TEXT: ClassVar[int] = 20_000

    chunk_id: str = Field(max_length=200)
    text: str = Field(max_length=MAX_TEXT)
    source: str = Field(max_length=500)
    confidence: float = Field(ge=0.0, le=1.0, default=1.0)
    created_at: datetime = Field(default_factory=_now)


# ── Founder ──────────────────────────────────────────────────────────────────


class FounderProfile(Frozen):
    """Structured facts about the founder.

    Only these structured fields reach the hard eligibility filter. Free text
    the model summarised never does — that is the load-bearing defense against
    prompt injection (Section 10.6).
    """

    #: Most chunks one founder's closed world may hold. `PUT /founders/{id}`
    #: replaces a profile wholesale, so without a cap this list is an
    #: unbounded write: a body small enough to accept can still carry tens of
    #: thousands of chunks, and every one is stored, re-read on each run, and
    #: scanned by the grounding check. Well above any real intake.
    MAX_KNOWLEDGE_CHUNKS: ClassVar[int] = 2_000

    founder_id: str = Field(max_length=200)
    #: What to call this person on their own dashboard. Optional because a
    #: profile is usable without it and the eligibility filter never reads it.
    #: There is deliberately no `email` beside it: `save_profile` redacts the
    #: serialised profile on the way into storage, and an address written here
    #: comes back as `[REDACTED_EMAIL]`. Contact details belong to the
    #: identity provider, not to the eligibility surface.
    full_name: str | None = Field(default=None, max_length=200)
    degree_level: DegreeLevel
    institution: str = Field(max_length=300)
    #: Field of study, as the founder writes it. Not a filter input; it is
    #: context for drafting and for the founder recognising their own profile.
    major: str | None = Field(default=None, max_length=200)
    citizenship: str = Field(
        max_length=100, description='ISO-ish token, e.g. "us_citizen", "f1_visa"'
    )
    entity_type: EntityType
    team_size: int = Field(ge=1, le=10_000)
    stage: Stage
    #: Numbers only. Prose belongs in `knowledge_base` where it carries a source.
    traction: dict[str, float] = Field(default_factory=dict, max_length=200)
    funding_range: tuple[int, int]
    equity_ok: bool
    has_faculty_advisor: bool
    max_application_hours: int = Field(gt=0, le=10_000)
    #: US state / country tokens the founder can claim residency or study in.
    geographies: list[str] = Field(default_factory=list, max_length=500)
    reuse_eligibility_answers: bool = False
    knowledge_base: list[KnowledgeChunk] = Field(
        default_factory=list, max_length=MAX_KNOWLEDGE_CHUNKS
    )

    @property
    def min_award(self) -> int:
        """Bottom of the funding range the founder said they want."""
        return self.funding_range[0]

    @property
    def max_award(self) -> int:
        """Top of the funding range the founder said they want."""
        return self.funding_range[1]


# ── Conversational founder intake ────────────────────────────────────────────────


class IntakeEvidence(Frozen):
    """A bounded pointer to the founder-controlled source of one proposal."""

    source_type: Literal["message", "document", "existing_profile"]
    source_id: str = Field(min_length=1, max_length=200)
    location: str | None = Field(default=None, max_length=200)
    excerpt: str | None = Field(default=None, max_length=500)


class IntakeFieldState(Mutable):
    """One candidate profile fact and whether a founder approved it.

    `value` is deliberately JSON-shaped here. The deterministic intake layer
    validates it against `field` before this record may be persisted; keeping
    the transport union here avoids Pydantic coercing `1` into `True` (or the
    inverse) merely because both are members of a broad scalar union.
    """

    field: IntakeFieldName
    status: IntakeFieldStatus = "missing"
    value: object | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    evidence: list[IntakeEvidence] = Field(default_factory=list, max_length=20)
    proposed_at: datetime | None = None
    confirmed_at: datetime | None = None
    confirmed_by: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def state_is_coherent(self) -> IntakeFieldState:
        if self.status == "missing":
            if self.value is not None or self.confirmed_at is not None:
                raise ValueError("a missing intake field cannot carry a value")
        elif self.value is None:
            raise ValueError("a proposed or confirmed intake field requires a value")
        if self.status == "confirmed":
            if self.confirmed_at is None or not self.confirmed_by:
                raise ValueError("a confirmed intake field requires confirmation metadata")
        elif self.confirmed_at is not None or self.confirmed_by is not None:
            raise ValueError("only confirmed intake fields carry confirmation metadata")
        return self


class IntakeSession(Mutable):
    """Persisted state for one founder interview."""

    session_id: str = Field(min_length=1, max_length=200)
    founder_id: str = Field(min_length=1, max_length=200)
    status: IntakeSessionStatus = "active"
    revision: int = Field(default=0, ge=0)
    fields: dict[str, IntakeFieldState] = Field(default_factory=dict, max_length=50)
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)
    completed_at: datetime | None = None

    @model_validator(mode="after")
    def field_keys_match(self) -> IntakeSession:
        for key, value in self.fields.items():
            if key != value.field:
                raise ValueError(f"intake field key {key!r} does not match {value.field!r}")
        if self.status == "completed" and self.completed_at is None:
            raise ValueError("a completed intake session requires completed_at")
        return self


class IntakeMessage(Frozen):
    """One bounded chat turn. Message bodies never belong in audit logs."""

    message_id: str = Field(min_length=1, max_length=200)
    session_id: str = Field(min_length=1, max_length=200)
    founder_id: str = Field(min_length=1, max_length=200)
    role: IntakeMessageRole
    text: str = Field(min_length=1, max_length=8_000)
    client_message_id: str | None = Field(default=None, min_length=1, max_length=200)
    created_at: datetime = Field(default_factory=_now)


class IntakeDocumentChunk(Frozen):
    """Sanitized extracted text retained after the raw upload is deleted."""

    chunk_id: str = Field(min_length=1, max_length=200)
    location: str = Field(min_length=1, max_length=200)
    text: str = Field(min_length=1, max_length=20_000)
    truncated: bool = False


class IntakeDocument(Frozen):
    """Metadata and bounded extracted content; never the original file bytes."""

    document_id: str = Field(min_length=1, max_length=200)
    session_id: str = Field(min_length=1, max_length=200)
    founder_id: str = Field(min_length=1, max_length=200)
    filename: str = Field(min_length=1, max_length=200)
    media_type: str = Field(min_length=1, max_length=100)
    byte_size: int = Field(ge=0, le=10 * 1024 * 1024)
    status: IntakeDocumentStatus
    chunks: list[IntakeDocumentChunk] = Field(default_factory=list, max_length=100)
    error: str | None = Field(default=None, max_length=500)
    created_at: datetime = Field(default_factory=_now)


# ── Opportunity ──────────────────────────────────────────────────────────────


class EligibilityRules(Frozen):
    """Machine-checkable eligibility, extracted from source text.

    `None` means the source did not state it. It does NOT mean "no
    restriction" — a permissive default here would silently convert an
    unstated rule into a pass, which is the exact failure Section 11.3 exists
    to prevent. The filter maps `None` to `UNKNOWN`, and `UNKNOWN` becomes a
    founder-facing question.
    """

    degree_levels: list[DegreeLevel] | None = None
    citizenships: list[str] | None = None
    entity_types: list[EntityType] | None = None
    min_team_size: int | None = None
    max_team_size: int | None = None
    geographies: list[str] | None = None
    #: Restricted to named institutions, e.g. ["Georgia Tech"].
    institutions: list[str] | None = None
    requires_faculty_pi: bool | None = None
    #: True where the funder takes equity — disqualifying if `equity_ok` is False.
    takes_equity: bool | None = None


class Opportunity(Frozen):
    """A funding opportunity.

    `description_excerpt` is untrusted text from the open web. It is
    sanitised and length-capped at ingestion (`agent.sanitize`) and is only
    ever passed to a model inside a delimited untrusted block.
    """

    id: str
    title: str
    funder: str
    source: SourceName
    source_url: str
    award_min: int | None = None
    award_max: int | None = None
    deadline: date | None = None
    rolling: bool = False
    effort_hours_estimate: float | None = None
    eligibility: EligibilityRules = Field(default_factory=EligibilityRules)
    criteria: list[ExtractedCriterion] = Field(default_factory=list)
    description_excerpt: str = ""
    #: False until a human has opened `source_url` and confirmed the row.
    #: Unverified rows are excluded from runs unless explicitly allowed.
    verified: bool = False
    verified_at: datetime | None = None
    retrieved_at: datetime = Field(default_factory=_now)

    @property
    def best_award(self) -> int | None:
        """Highest stated award, for ranking and threshold checks."""
        return self.award_max if self.award_max is not None else self.award_min


class EligibilityQuestion(Mutable):
    """One founder-answerable requirement for one persisted opportunity."""

    question_id: str = Field(max_length=200)
    founder_id: str = Field(max_length=200)
    opportunity_id: str = Field(max_length=500)
    opportunity_title: str = Field(max_length=500)
    source_url: str = Field(max_length=2_000)
    deadline: date | None = None
    check: str = Field(max_length=100)
    question: str = Field(max_length=1_000)
    requirement: str = Field(max_length=20_000)
    source_doc: str = Field(default="", max_length=2_000)
    status: EligibilityQuestionStatus = "pending"
    answer: EligibilityAnswerValue | None = None
    answer_updated_at: datetime | None = None
    reused_from_question_id: str | None = Field(default=None, max_length=200)
    #: A definite answer has been saved but no run has consumed it yet.
    reassessment_pending: bool = False
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)

    @model_validator(mode="after")
    def align_status_with_answer(self) -> EligibilityQuestion:
        """`not_sure` stays pending; only a definite answer resolves the row."""
        self.status = "answered" if self.answer in {"yes", "no"} else "pending"
        return self


class Rejection(Frozen):
    """Why the deterministic filter dropped an opportunity.

    Structured so a judge asking "how do I know it isn't hiding things?"
    gets a table, not an explanation (Section 9, rule 5).
    """

    opportunity_id: str
    opportunity_title: str
    check: str = Field(description='Machine id, e.g. "DEGREE_LEVEL"')
    detail: str = Field(description="One line, written to be read by a human.")
    founder_value: str
    required_value: str


class Blocker(Frozen):
    """A deterministic obstacle the founder could actually remove.

    Deliberately NOT a rejection. Section 10.7 names "form an LLC" and "get a
    faculty PI" as things worth surfacing, so encoding them as INELIGIBLE
    would silently discard the exact opportunities the escalation policy says
    to show. They ride along with an ELIGIBLE/UNKNOWN verdict and become the
    Assessor's structured input for a MAYBE.
    """

    check: str
    detail: str
    #: Written for the founder: "form an LLC before applying".
    remedy: str


class EligibilityResult(Frozen):
    """Per-opportunity output of the pure-Python gate."""

    opportunity_id: str
    verdict: Eligibility
    #: Populated when verdict is INELIGIBLE.
    rejection: Rejection | None = None
    #: Checks that came back UNKNOWN, by name. Non-empty implies UNKNOWN.
    unknown_checks: list[str] = Field(default_factory=list)
    #: Deterministic but founder-fixable. Does not affect `verdict`.
    resolvable_blockers: list[Blocker] = Field(default_factory=list)


# ── Judgment ─────────────────────────────────────────────────────────────────


class Assessment(Mutable):
    """Assessor sub-agent output. Validated; never a freeform string.

    `reason` is written for the founder, not for a log file. It may not
    characterise competitiveness, acceptance rate, or odds of winning — we
    have no data for that and it would be pure invention (Section 10.5).
    """

    verdict: Verdict
    reason: str
    effort_hours: float = Field(ge=0)
    blocker: str | None = Field(
        default=None, description='e.g. "requires faculty PI"'
    )
    #: Drives escalation: a MAYBE only surfaces when the founder can actually
    #: act on the blocker — get an advisor, form an LLC (Section 10.7).
    blocker_founder_resolvable: bool = False
    #: Set by the orchestrator, not the model.
    opportunity_id: str = ""
    model_id: str = ""
    prompt_version: str = ""
    created_at: datetime = Field(default_factory=_now)


# ── Drafting ─────────────────────────────────────────────────────────────────


class ApplicationField(Frozen):
    """One question on a real application form, modelled as structured JSON.

    `label` is copied verbatim from the source form. The Section 10.1
    blocklist matches on label text, so paraphrasing a label is how a
    certification field stops looking like one.
    """

    field_id: str
    label: str
    kind: Literal["short_text", "long_text", "number", "date", "select", "file", "checkbox"] = "long_text"
    required: bool = True
    max_chars: int | None = None
    options: list[str] | None = None
    help_text: str = ""
    #: A limit the form states in units other than characters — pages,
    #: minutes, slides. Recorded verbatim because "20 pages, double-spaced"
    #: is not expressible as `max_chars` and rounding it into one would be
    #: inventing a rule the form did not state.
    stated_limit: str = ""
    #: Set when the curator judged this field to be one the agent must never
    #: fill. Advisory and additive only: `guardrails.blocklisted()` decides
    #: independently from the label, and a field it catches is blocked
    #: whatever this flag says.
    protected: bool = False


class ApplicationForm(Frozen):
    """One real application form, transcribed field by field.

    Labels are verbatim from the source page because the Section 10.1
    blocklist matches on label text — a tidied-up label is a certification
    field that stops looking like one. `complete` and `completeness_note`
    exist so a partial transcription cannot be mistaken for a whole form.
    """

    opportunity_id: str
    name: str
    source_url: str
    fields: list[ApplicationField]
    #: When the form was read off the page. A transcription is a claim about
    #: a moment; forms change between cycles.
    retrieved_at: date | None = None
    #: False when the public page states only part of the form — a portal
    #: behind a login, a PDF the page links but does not show. A partial form
    #: must be visibly partial or it reads as the whole application.
    complete: bool = True
    #: What is missing, and why, when `complete` is False.
    completeness_note: str = ""


class DraftField(Mutable):
    """One answered (or deliberately unanswered) form field, with its receipt.

    Merges Section 8's `DraftField` and Section 11.8's `FieldRecord`. They
    describe the same object at two fidelities and splitting them guarantees
    they drift. See DECISIONS.md.
    """

    field_id: str
    question: str
    answer: str | None = None
    status: FieldStatus
    #: Empty on a GENERATED field is a hard failure, not a warning.
    provenance: list[SourceSpan] = Field(default_factory=list)
    #: Exact Bedrock model that produced it. Empty for KNOWN/NEEDS_FOUNDER.
    model_id: str = ""
    #: Git blob hash of the prompt .md that produced it.
    prompt_version: str = ""
    audit_verdict: AuditVerdict | None = None
    audit_note: str = ""
    #: Set when the answer was lifted from a previous application (recall).
    reused_from: str | None = None
    #: How recall found it: "exact" for normalised equality, otherwise the
    #: matcher's name. Stored so a reuse can be explained rather than
    #: asserted — "we reused this because you answered X" needs to name X.
    reuse_match: str | None = None
    #: The question the reused answer was originally written for.
    reuse_source_question: str | None = None
    #: Similarity score for a semantic match. 1.0 for an exact match.
    reuse_score: float | None = None
    created_at: datetime = Field(default_factory=_now)


class Draft(Mutable):
    """One application in progress, field by field.

    The only `Mutable` record that is genuinely rewritten during a run: fields
    are filled, audited, and possibly forced back to NEEDS_FOUNDER by the
    gate. `gate_result` is None until `ship_gate` has run — which is not the
    same as passing, and callers must not read None as "no violations".
    """

    draft_id: str
    founder_id: str
    opportunity_id: str
    form_name: str = ""
    fields: list[DraftField] = Field(default_factory=list)
    status: DraftStatus = "DRAFT"
    gate_result: GateResult | None = None
    created_at: datetime = Field(default_factory=_now)

    @property
    def needs_founder(self) -> list[DraftField]:
        """Fields the founder still has to answer themselves.

        Includes fields the gate forced back to NEEDS_FOUNDER, so this is the
        post-gate truth rather than the Drafter's opinion.
        """
        return [f for f in self.fields if f.status == "NEEDS_FOUNDER"]

    def counts(self) -> dict[str, int]:
        """Field counts by status — the '28 filled, 6 drafted, 3 need you' line.

        Computed in Python. A model is never asked to count anything
        (Section 9, rule 8).
        """
        out = {"KNOWN": 0, "GENERATED": 0, "NEEDS_FOUNDER": 0, "REUSED": 0}
        for f in self.fields:
            out[f.status] += 1
        return out


# ── Audit + gate ─────────────────────────────────────────────────────────────


class FieldAudit(Mutable):
    """Auditor sub-agent output for a single field.

    The Auditor sees the finished draft plus the knowledge base — never the
    Drafter's prompt or reasoning. An auditor that inherits the drafter's
    context inherits its mistakes (Section 11.5).
    """

    field_id: str
    verdict: AuditVerdict
    #: Quoted supporting span. Required when verdict is SUPPORTED.
    supporting_quote: str | None = None
    note: str = ""


class AuditReport(Mutable):
    """Every field's audit verdict for one draft.

    A field with no entry here has not been audited, which the ship gate
    treats differently from one audited and found unsupported. Absence is not
    approval.
    """

    draft_id: str
    fields: list[FieldAudit] = Field(default_factory=list)
    model_id: str = ""
    prompt_version: str = ""
    created_at: datetime = Field(default_factory=_now)

    @property
    def unsupported(self) -> list[FieldAudit]:
        """Fields the Auditor could not tie back to the knowledge base.

        Note this excludes UNVERIFIABLE — a field the Auditor could not judge is
        not the same as one it judged and rejected, and the gate handles the two
        separately.
        """
        return [f for f in self.fields if f.verdict == "UNSUPPORTED"]


class GateViolation(Frozen):
    """One thing the ship gate objected to.

    `field_id` is None for a whole-draft violation, e.g. a check about the
    draft's status rather than about any single answer.
    """

    check: str
    field_id: str | None
    detail: str
    #: BLOCK stops the draft. FORCED_NEEDS_FOUNDER rewrites a field and
    #: continues — the blocklist is a correction, not a failure.
    severity: Literal["BLOCK", "FORCED_NEEDS_FOUNDER"]


class GateResult(Mutable):
    """Outcome of `agent.guardrails.ship_gate`.

    `passed is False` is the only safe default. If the gate itself throws,
    the caller writes a GateResult with `failed_check="GATE_EXCEPTION"` —
    an exception in the safety layer is never read as "passed"
    (Section 11.9).
    """

    passed: bool = False
    checks_run: list[str] = Field(default_factory=list)
    violations: list[GateViolation] = Field(default_factory=list)
    failed_check: str | None = None

    @property
    def blocking(self) -> list[GateViolation]:
        """Only the violations that stop the draft.

        FORCED_NEEDS_FOUNDER violations are excluded on purpose: the blocklist
        rewriting a field is a correction the gate made, not a reason to refuse.
        """
        return [v for v in self.violations if v.severity == "BLOCK"]


# ── Run + surfacing ──────────────────────────────────────────────────────────


class SourceFailure(Frozen):
    """A source that did not answer. Reported, never smoothed over.

    A silent partial run is a lie (Section 9, rule 6).
    """

    source: SourceName
    detail: str
    at: datetime = Field(default_factory=_now)


class SkipRecord(Frozen):
    """The silent path, written down.

    The founder does not see these by default. A judge asking to see them
    gets one click (Section 9, rule 5).
    """

    opportunity_id: str
    opportunity_title: str
    stage: Literal["hard_filter", "assessor", "escalation_policy"]
    reason: str


class InboxItem(Mutable):
    """The ONLY thing the founder sees.

    Idempotency key is `(founder_id, opportunity_id)`. Never notify twice
    about the same opportunity (Section 10.7).
    """

    item_id: str
    founder_id: str
    opportunity_id: str
    kind: Literal[
        "APPLY",
        "MAYBE",
        "UNKNOWN_HIGH_VALUE",
        "DEADLINE_URGENT",
        "COLD_START",
    ]
    headline: str
    summary: str
    assessment: Assessment | None = None
    draft_id: str | None = None
    #: True for overflow past MAX_SURFACED_PER_RUN — visible in the "also
    #: found" list, but it does not generate a notification.
    passive: bool = False
    state: InboxState = "new"
    created_at: datetime = Field(default_factory=_now)

    @property
    def idempotency_key(self) -> str:
        """`founder_id::opportunity_id` — the value the unique index is built on.

        Derived rather than stored, so it cannot drift from the two fields it is
        made of. Note it does not include the run: seeing the same opportunity in
        a later run is deliberately *not* a new item.
        """
        return f"{self.founder_id}::{self.opportunity_id}"


class TokenUsage(Mutable):
    """Token and dollar totals for one run.

    `usd_estimate` is an estimate in the literal sense — it is computed from
    the configured prices, and those default to zero. A run showing $0.00 may
    mean it was cheap or may mean prices were never configured; `/ready`
    reports which in production.
    """

    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    usd_estimate: float = 0.0

    def add(self, other: TokenUsage) -> None:
        """Accumulate another usage into this one, in place.

        Mutates rather than returning a new value because it is called in a hot
        loop per model call. Nothing here checks a cap — that is `agent/budget.py`,
        and adding usage is not the same as being allowed to spend it.
        """
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.total_tokens += other.total_tokens
        self.usd_estimate += other.usd_estimate


class RunReport(Mutable):
    """The four counters are the entire pitch in one line.

    "Scanned 214. Discarded 198. Judged 16. Surfaced 3." The agent's judgment
    is measured by what it throws away silently (Section 2).
    """

    run_id: str
    founder_id: str
    started_at: datetime = Field(default_factory=_now)
    finished_at: datetime | None = None
    duration_s: float = 0.0

    scanned: int = 0
    filtered_out: int = 0
    judged: int = 0
    surfaced: int = 0

    sources_failed: list[SourceFailure] = Field(default_factory=list)
    rejections: list[Rejection] = Field(default_factory=list)
    skips: list[SkipRecord] = Field(default_factory=list)
    usage: TokenUsage = Field(default_factory=TokenUsage)
    #: Set when a cap fired or a dependency died. A halted run surfaces
    #: nothing and says so.
    halted_reason: str | None = None
    #: True when memory was unavailable and the run went stateless.
    stateless: bool = False
    notes: list[str] = Field(default_factory=list)

    def headline(self) -> str:
        """One line, computed in Python, never written by a model."""
        return (
            f"Scanned {self.scanned}. "
            f"Discarded {self.filtered_out}. "
            f"Judged {self.judged}. "
            f"Surfaced {self.surfaced}."
        )


# Draft references GateResult before it is defined.
Draft.model_rebuild()


JobStatus = Literal["queued", "running", "succeeded", "halted", "failed", "cancelled"]


class RunJob(Mutable):
    """One accepted invocation of the pipeline, durable from the moment the
    API says 202.

    The job is the HTTP-visible half of a run: it exists so a request can
    return immediately, so a retry with the same idempotency key resolves to
    the same work, and so a crash mid-run leaves a row that says *failed*
    rather than a connection that says nothing. The `RunReport` it eventually
    points at stays immutable and append-only, exactly as before — a job
    records the lifecycle, never the verdicts.
    """

    job_id: str
    founder_id: str
    #: Scoped per founder before storage; None for callers that sent none.
    idempotency_key: str | None = None
    source: Literal["manual", "scheduled", "eligibility_answer", "unknown"] = "unknown"
    use_demo_catalog: bool = False
    include_grants_gov: bool = True
    #: Set only for a one-opportunity answer-triggered reassessment.
    target_opportunity_id: str | None = None

    status: JobStatus = "queued"
    created_at: datetime = Field(default_factory=_now)
    started_at: datetime | None = None
    finished_at: datetime | None = None

    #: Set when the run produced a report — succeeded and halted both do.
    run_id: str | None = None
    #: Sanitised one-liner for failed/cancelled jobs. Never a stack trace.
    error: str | None = None

    def terminal(self) -> bool:
        """Whether this job has reached a state it will never leave.

        `halted` counts: a run stopped by a budget cap is finished, and it has a
        report. Only `queued` and `running` are non-terminal.
        """
        return self.status in ("succeeded", "halted", "failed", "cancelled")


class KnowledgeBase(Mutable):
    """The closed world the Drafter and Auditor are allowed to draw from.

    Wraps the founder's provenance-tagged chunks plus the structured numbers
    on their profile. Nothing outside this object may appear as a factual
    claim in a draft.
    """

    founder_id: str
    chunks: list[KnowledgeChunk] = Field(default_factory=list)
    #: Structured traction straight off the profile. Kept separate from
    #: `chunks` because these are numbers, and numbers are the most
    #: damaging thing an agent can invent onto a funding application.
    traction: dict[str, float] = Field(default_factory=dict)

    @classmethod
    def from_profile(cls, profile: FounderProfile) -> KnowledgeBase:
        """Build the closed world from a profile.

        Copies both collections rather than aliasing them, so mutating the
        knowledge base during a run cannot write back into the stored profile.
        """
        return cls(
            founder_id=profile.founder_id,
            chunks=list(profile.knowledge_base),
            traction=dict(profile.traction),
        )

    @property
    def text(self) -> str:
        """All chunk text concatenated. Used for substring/number checks."""
        return "\n".join(c.text for c in self.chunks)

    def is_cold(self, min_chunks: int) -> bool:
        """Below the floor the Drafter is disabled entirely (Section 11.10).

        The first run has almost no knowledge base, which is exactly when a
        model is most tempted to fill gaps. A sparse profile must produce
        more NEEDS_FOUNDER fields, not more invention.
        """
        return len(self.chunks) < min_chunks

    def find(self, needle: str) -> KnowledgeChunk | None:
        """First chunk containing `needle`, case-insensitively."""
        low = needle.lower().strip()
        if not low:
            return None
        for chunk in self.chunks:
            if low in chunk.text.lower():
                return chunk
        return None
