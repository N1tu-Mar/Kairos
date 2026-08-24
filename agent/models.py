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
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# ── Vocabularies ─────────────────────────────────────────────────────────────

DegreeLevel = Literal["undergrad", "masters", "phd", "postdoc"]
EntityType = Literal["none", "llc", "c_corp", "s_corp", "nonprofit"]
Stage = Literal["idea", "prototype", "mvp", "pilot", "revenue"]

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
AuditVerdict = Literal["SUPPORTED", "UNSUPPORTED", "UNVERIFIABLE"]
DraftStatus = Literal["DRAFT", "READY", "BLOCKED"]
SourceName = Literal["seed", "grants_gov", "browser"]


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Frozen(BaseModel):
    """Base for records that are written once and then read.

    Drafts mutate during a run; opportunities and knowledge chunks do not.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")


class Mutable(BaseModel):
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

    chunk_id: str
    text: str
    source: str
    confidence: float = Field(ge=0.0, le=1.0, default=1.0)
    created_at: datetime = Field(default_factory=_now)


# ── Founder ──────────────────────────────────────────────────────────────────


class FounderProfile(Frozen):
    """Structured facts about the founder.

    Only these structured fields reach the hard eligibility filter. Free text
    the model summarised never does — that is the load-bearing defense against
    prompt injection (Section 10.6).
    """

    founder_id: str
    degree_level: DegreeLevel
    institution: str
    citizenship: str = Field(description='ISO-ish token, e.g. "us_citizen", "f1_visa"')
    entity_type: EntityType
    team_size: int = Field(ge=1)
    stage: Stage
    #: Numbers only. Prose belongs in `knowledge_base` where it carries a source.
    traction: dict[str, float] = Field(default_factory=dict)
    funding_range: tuple[int, int]
    equity_ok: bool
    has_faculty_advisor: bool
    max_application_hours: int = Field(gt=0)
    #: US state / country tokens the founder can claim residency or study in.
    geographies: list[str] = Field(default_factory=list)
    knowledge_base: list[KnowledgeChunk] = Field(default_factory=list)

    @property
    def min_award(self) -> int:
        return self.funding_range[0]

    @property
    def max_award(self) -> int:
        return self.funding_range[1]


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
    """One question on a real application form, modelled as structured JSON."""

    field_id: str
    label: str
    kind: Literal["short_text", "long_text", "number", "date", "select", "file", "checkbox"] = "long_text"
    required: bool = True
    max_chars: int | None = None
    options: list[str] | None = None
    help_text: str = ""


class ApplicationForm(Frozen):
    opportunity_id: str
    name: str
    source_url: str
    fields: list[ApplicationField]


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
    created_at: datetime = Field(default_factory=_now)


class Draft(Mutable):
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
    draft_id: str
    fields: list[FieldAudit] = Field(default_factory=list)
    model_id: str = ""
    prompt_version: str = ""
    created_at: datetime = Field(default_factory=_now)

    @property
    def unsupported(self) -> list[FieldAudit]:
        return [f for f in self.fields if f.verdict == "UNSUPPORTED"]


class GateViolation(Frozen):
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
        return f"{self.founder_id}::{self.opportunity_id}"


class TokenUsage(Mutable):
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    usd_estimate: float = 0.0

    def add(self, other: TokenUsage) -> None:
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
