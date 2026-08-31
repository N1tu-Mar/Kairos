"""Persistence behind one interface.

SQLite locally, DynamoDB when deployed, and **no SQL anywhere in `agent/`**.
The agent talks to the `Repository` protocol and nothing else.

Every table is the same shape: a primary key, a couple of indexed columns
worth querying on, and the full Pydantic model serialised into a JSON
payload. That is a deliberate choice, not laziness — it is exactly the shape
DynamoDB wants (partition key, sort key, document), so the second
implementation is a port rather than a rewrite. The cost is that you cannot
query inside a payload from SQL, which we never need to do.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Literal, Protocol, TypeVar

from sqlalchemy import Column, Text
from sqlmodel import Field, Session, SQLModel, create_engine, select

from agent.models import (
    Draft,
    DraftField,
    EligibilityAnswerValue,
    EligibilityQuestion,
    EligibilityQuestionStatus,
    FounderProfile,
    InboxItem,
    InboxState,
    Opportunity,
    RunJob,
    RunReport,
)
from agent.sanitize import redact_json
from agent.semantic import (
    DEFAULT_MATCHER,
    DEFAULT_THRESHOLD,
    SemanticMatcher,
    is_reusable,
)

T = TypeVar("T")


def _now() -> datetime:
    """Current UTC instant. Every timestamp written here is timezone-aware; a naive datetime compared against an aware one raises, and that has been the source of more than one ordering bug."""
    return datetime.now(timezone.utc)


# ── Tables ───────────────────────────────────────────────────────────────────


class ProfileRow(SQLModel, table=True):
    """One founder profile, latest version only.

    Unlike runs and drafts this row is overwritten in place: the profile is
    current state, not history. If you ever need "what did the profile say
    when this run happened", it is not recoverable from here — the run report
    is where that has to be captured.
    """

    __tablename__ = "profiles"
    founder_id: str = Field(primary_key=True)
    updated_at: datetime = Field(default_factory=_now)
    payload: str = Field(sa_column=Column(Text))


class RunRow(SQLModel, table=True):
    """One completed run report.

    `started_at` is indexed because every read of this table is "most recent
    first, capped" — see `list_runs`. `founder_id` is indexed for the same
    reason: no query here is ever cross-founder.
    """

    __tablename__ = "runs"
    run_id: str = Field(primary_key=True)
    founder_id: str = Field(index=True)
    started_at: datetime = Field(index=True)
    payload: str = Field(sa_column=Column(Text))


class InboxRow(SQLModel, table=True):
    """One surfaced opportunity for one founder.

    The write path only ever creates these with `state == "new"`; every other
    state transition comes from a person via `set_inbox_state`.
    """

    __tablename__ = "inbox"
    item_id: str = Field(primary_key=True)
    #: `founder_id::opportunity_id`. Unique, so a double-notify is a
    #: constraint violation rather than a bug you find in the demo.
    idempotency_key: str = Field(index=True, unique=True)
    founder_id: str = Field(index=True)
    opportunity_id: str = Field(index=True)
    created_at: datetime = Field(index=True)
    payload: str = Field(sa_column=Column(Text))


class OpportunityRow(SQLModel, table=True):
    """The opportunities a run actually looked at.

    Written so a `Rejection` or `SkipRecord` can be resolved back to the row
    it was made about. Without this, award, deadline and eligibility exist
    only inside the headline string the run happened to compose, and nothing
    downstream can sort or filter on them.

    Keyed by opportunity id and upserted, so re-seeing an opportunity in a
    later run refreshes it rather than duplicating it.
    """

    __tablename__ = "opportunities"
    opportunity_id: str = Field(primary_key=True)
    source: str = Field(index=True)
    first_seen_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now, index=True)
    payload: str = Field(sa_column=Column(Text))


class JobRow(SQLModel, table=True):
    """One accepted run invocation — the durable half of the async boundary.

    `idempotency_key` is stored as `founder_id::key` and unique, so a retry
    that races the original request loses at the database, not in Python.
    """

    __tablename__ = "jobs"
    job_id: str = Field(primary_key=True)
    founder_id: str = Field(index=True)
    idempotency_key: str | None = Field(default=None, index=True, unique=True)
    status: str = Field(index=True)
    created_at: datetime = Field(index=True)
    payload: str = Field(sa_column=Column(Text))


class DraftRow(SQLModel, table=True):
    """One draft application.

    Both `founder_id` and `opportunity_id` are indexed because `list_drafts`
    filters on either or both. The draft body lives in `payload`, so nothing
    about a field's status or provenance is queryable from SQL — that is the
    deliberate document-store tradeoff described in the module docstring.
    """

    __tablename__ = "drafts"
    draft_id: str = Field(primary_key=True)
    founder_id: str = Field(index=True)
    opportunity_id: str = Field(index=True)
    payload: str = Field(sa_column=Column(Text))


class FounderMemberRow(SQLModel, table=True):
    """Which identity-provider users may act for which founder.

    The seam between "who signed in" and "whose data this is". Both halves are
    the primary key, so a person may hold several founders and a founder may
    have several people — the cofounder case, which is why
    `Principal.founder_ids` is a set and why the founder id is not simply the
    auth user's id.

    `auth_user_id` is opaque here on purpose. It is a Supabase `sub` claim
    today; nothing in this table or its queries knows that, so swapping
    identity providers is a change to `api/auth.py` and a backfill of one
    column rather than a migration of the tenancy key across six tables.
    """

    __tablename__ = "founder_members"
    auth_user_id: str = Field(primary_key=True)
    founder_id: str = Field(primary_key=True, index=True)
    #: Read-only credentials are a real thing to want (an advisor, an
    #: auditor). Collapsed conservatively into `Principal.can_write` — see
    #: `SqliteRepository.can_write`.
    can_write: bool = Field(default=True)
    created_at: datetime = Field(default_factory=_now)


class AnswerRow(SQLModel, table=True):
    """Answers the founder has already given, for `recall`.

    Application 1 needs 15 answers. Application 2 needs 3. That drop is the
    product, and this table is what produces it.
    """

    __tablename__ = "answers"
    answer_id: str = Field(primary_key=True)
    founder_id: str = Field(index=True)
    #: Lowercased, punctuation-stripped question text.
    question_key: str = Field(index=True)
    created_at: datetime = Field(default_factory=_now)
    payload: str = Field(sa_column=Column(Text))


class EligibilityQuestionRow(SQLModel, table=True):
    """Founder-answerable eligibility uncertainty, upserted by stable id."""

    __tablename__ = "eligibility_questions"
    question_id: str = Field(primary_key=True)
    founder_id: str = Field(index=True)
    opportunity_id: str = Field(index=True)
    status: str = Field(index=True)
    created_at: datetime = Field(index=True)
    payload: str = Field(sa_column=Column(Text))


# ── Interface ────────────────────────────────────────────────────────────────


class Repository(Protocol):
    """The only persistence surface `agent/` is allowed to see.

    Everything below is grouped by record type. Two properties hold across
    every method and are worth stating once rather than repeating:

    *   **No authorization happens here.** `get_draft(draft_id)` will hand
        back any founder's draft. Checking that the caller owns the record
        is `api/auth.py`'s job. A new endpoint that reads by bare id and
        skips that check is a cross-founder data leak.
    *   **Reads deserialise the JSON payload, not the indexed columns.**
        The columns exist only for ordering and filtering. If a payload and
        its extracted column ever disagree, queries follow the column and
        the returned object follows the payload.
    """

    def save_profile(self, profile: FounderProfile) -> None: ...
    def get_profile(self, founder_id: str) -> FounderProfile | None: ...

    # Runs: append-only history. `latest_run`/`list_runs` are capped,
    # `get_run` is the only way back to an old one.
    def save_run(self, report: RunReport) -> None: ...
    def latest_run(self, founder_id: str) -> RunReport | None: ...
    def get_run(self, run_id: str) -> RunReport | None: ...
    def list_runs(self, founder_id: str, limit: int = 20) -> list[RunReport]: ...

    # Opportunities: upserted by id, shared across founders.
    def save_opportunity(self, opportunity: Opportunity) -> None: ...
    def get_opportunity(self, opportunity_id: str) -> Opportunity | None: ...

    # Eligibility clarifications: founder-owned, editable current state.
    def save_eligibility_question(self, question: EligibilityQuestion) -> None: ...
    def get_eligibility_question(self, question_id: str) -> EligibilityQuestion | None: ...
    def list_eligibility_questions(
        self,
        founder_id: str,
        status: EligibilityQuestionStatus | Literal["all"] = "pending",
    ) -> list[EligibilityQuestion]: ...
    def answer_eligibility_question(
        self, question_id: str, answer: EligibilityAnswerValue
    ) -> EligibilityQuestion | None: ...
    def mark_eligibility_reassessed(
        self, founder_id: str, opportunity_id: str, *, before: datetime
    ) -> int: ...

    # Inbox: `has_surfaced` + the unique index on `save_inbox_item` are
    # the two halves of never notifying the same founder twice.
    def has_surfaced(self, founder_id: str, opportunity_id: str) -> bool: ...
    def save_inbox_item(self, item: InboxItem) -> bool: ...
    def list_inbox(self, founder_id: str, limit: int = 50) -> list[InboxItem]: ...
    def get_inbox_item(self, item_id: str) -> InboxItem | None: ...
    def set_inbox_state(self, item_id: str, state: InboxState) -> InboxItem | None: ...

    # Drafts: mutable during a run, keyed by `draft_id`.
    def save_draft(self, draft: Draft) -> None: ...
    def get_draft(self, draft_id: str) -> Draft | None: ...
    def list_drafts(
        self, founder_id: str, opportunity_id: str | None = None
    ) -> list[Draft]: ...

    # Recall: what makes application 2 shorter than application 1.
    def remember_answer(self, founder_id: str, field: DraftField) -> None: ...
    def recall(self, founder_id: str, question: str) -> DraftField | None: ...

    # Membership: which auth users may act for which founders. The only
    # thing that turns "somebody signed in" into "may touch this founder".
    def founder_ids_for(self, auth_user_id: str) -> frozenset[str]: ...
    def can_write(self, auth_user_id: str) -> bool: ...
    def link_member(
        self, auth_user_id: str, founder_id: str, can_write: bool = True
    ) -> None: ...
    def unlink_member(self, auth_user_id: str, founder_id: str) -> None: ...

    # Jobs: the durable half of the async run boundary.
    def save_job(self, job: RunJob) -> None: ...
    def get_job(self, job_id: str) -> RunJob | None: ...
    def get_job_by_key(self, founder_id: str, idempotency_key: str) -> RunJob | None: ...
    def list_jobs(self, founder_id: str, limit: int = 20) -> list[RunJob]: ...
    def fail_orphaned_jobs(self, reason: str) -> list[RunJob]: ...


def new_founder_id() -> str:
    """A fresh founder id: `founder_` plus 12 hex characters.

    Same shape as `job_` and `run_` ids, and random for the same reason the
    others are. `authorize` answers 404 rather than 403 so a founder id cannot
    be probed for existence; a sequential or name-derived id would make that
    the only thing standing between a stranger and an enumerated customer
    list. `founder_demo` remains what the seeded demo profile uses and is not
    a template for anything real.
    """
    import uuid

    return f"founder_{uuid.uuid4().hex[:12]}"


def question_key(question: str) -> str:
    """Normalise a form question for the exact-match tier of recall.

    Lowercase, punctuation-stripped equality — the highest-confidence path,
    tried before anything probabilistic. Section 6's *semantically*
    equivalent questions are handled by the second tier in
    `agent/semantic.py`, which only runs when this one finds nothing.
    """
    import re

    return re.sub(r"[^a-z0-9]+", " ", question.lower()).strip()


# ── SQLite ───────────────────────────────────────────────────────────────────


class SqliteRepository:
    """Local implementation. Interchangeable with the DynamoDB one."""

    def __init__(
        self,
        url: str = "sqlite:///./kairos.db",
        echo: bool = False,
        *,
        matcher: SemanticMatcher | None = DEFAULT_MATCHER,
        similarity_threshold: float = DEFAULT_THRESHOLD,
        create_schema: bool = True,
    ) -> None:
        """`matcher=None` disables semantic recall and leaves exact matching
        only — which is what the pre-semantic behaviour was, still reachable
        for anyone who wants the strictest possible reuse policy.

        `create_schema=False` skips `create_all()`. That call cannot *evolve*
        a schema — it creates what is missing and silently ignores a table
        whose shape has changed — which is fine for a fresh local database
        and for tests, and exactly wrong for a deployment. In production the
        schema is owned by `alembic upgrade head` at deploy time, so a
        missing table should be a loud readiness failure rather than a table
        quietly conjured with whatever shape this build happens to expect.
        """
        self.engine = create_engine(url, echo=echo)
        self.matcher = matcher
        self.similarity_threshold = similarity_threshold
        if create_schema:
            SQLModel.metadata.create_all(self.engine)

    def schema_version(self) -> str | None:
        """The Alembic revision this database is at, or None if unmanaged.

        None means the database predates migrations — created by
        `create_all()` and never adopted. In production that is a readiness
        failure, and the fix is `alembic upgrade head`, which adopts an
        existing database in place without dropping anything.
        """
        from sqlalchemy import inspect, text

        if "alembic_version" not in inspect(self.engine).get_table_names():
            return None
        with self.engine.connect() as conn:
            row = conn.execute(text("SELECT version_num FROM alembic_version")).first()
        return row[0] if row else None

    # -- profiles --

    def save_profile(self, profile: FounderProfile) -> None:
        """Upsert the profile, redacting secrets on the way in."""
        with Session(self.engine) as session:
            row = session.get(ProfileRow, profile.founder_id) or ProfileRow(
                founder_id=profile.founder_id, payload=""
            )
            # Redact at the persistence boundary, not at display time
            # (Section 10.4).
            row.payload = redact_json(profile.model_dump_json())
            row.updated_at = _now()
            session.add(row)
            session.commit()

    def get_profile(self, founder_id: str) -> FounderProfile | None:
        """Load a profile by founder id, or None if this founder has never saved one."""
        with Session(self.engine) as session:
            row = session.get(ProfileRow, founder_id)
            return FounderProfile.model_validate_json(row.payload) if row else None

    # -- membership --

    def founder_ids_for(self, auth_user_id: str) -> frozenset[str]:
        """Every founder this auth user may act for. Empty when none.

        An empty set is the fail-closed answer and the only one an unknown
        user ever gets. It is never "the demo founder" — a lookup miss must
        not resolve to access.
        """
        with Session(self.engine) as session:
            rows = session.exec(
                select(FounderMemberRow).where(
                    FounderMemberRow.auth_user_id == auth_user_id
                )
            ).all()
            return frozenset(row.founder_id for row in rows)

    def can_write(self, auth_user_id: str) -> bool:
        """Whether this user's memberships permit writes.

        `Principal` carries one flag for the whole set, so a person holding a
        writable membership and a read-only one has to collapse to something.
        It collapses to read-only: granting writes because *one* founder said
        yes would write to the founder that said no. A user with no membership
        at all is False, like every other question about them.

        The finer-grained answer is a per-founder flag on `Principal`, which
        changes the authorization signature and is not worth making until a
        read-only membership actually exists.
        """
        with Session(self.engine) as session:
            rows = session.exec(
                select(FounderMemberRow).where(
                    FounderMemberRow.auth_user_id == auth_user_id
                )
            ).all()
            return bool(rows) and all(row.can_write for row in rows)

    def link_member(
        self, auth_user_id: str, founder_id: str, can_write: bool = True
    ) -> None:
        """Grant a user access to a founder. Idempotent.

        Upserted rather than inserted so a retried signup — or a second click
        on an invitation link — updates the membership instead of failing on
        the primary key.
        """
        with Session(self.engine) as session:
            row = session.get(FounderMemberRow, (auth_user_id, founder_id))
            if row is None:
                row = FounderMemberRow(
                    auth_user_id=auth_user_id,
                    founder_id=founder_id,
                    can_write=can_write,
                )
            else:
                row.can_write = can_write
            session.add(row)
            session.commit()

    def unlink_member(self, auth_user_id: str, founder_id: str) -> None:
        """Revoke access. A no-op when the membership is already gone.

        A real delete, unlike a credential's `revoked` flag: the record of
        what this person did lives in the audit log, which is not this table's
        job to preserve.
        """
        with Session(self.engine) as session:
            row = session.get(FounderMemberRow, (auth_user_id, founder_id))
            if row is not None:
                session.delete(row)
                session.commit()

    # -- runs --

    def save_run(self, report: RunReport) -> None:
        """Upsert a run report keyed by `run_id`.

        `started_at` and `founder_id` are copied out of the report into indexed
        columns on insert only. If a report is ever re-saved with a different
        `started_at`, the column keeps the original value and the payload keeps
        the new one — they would disagree, and list ordering follows the column.
        """
        with Session(self.engine) as session:
            row = session.get(RunRow, report.run_id) or RunRow(
                run_id=report.run_id,
                founder_id=report.founder_id,
                started_at=report.started_at,
                payload="",
            )
            row.payload = redact_json(report.model_dump_json())
            session.add(row)
            session.commit()

    def list_runs(self, founder_id: str, limit: int = 20) -> list[RunReport]:
        """Most recent runs for one founder, newest first, capped at `limit`."""
        with Session(self.engine) as session:
            rows = session.exec(
                select(RunRow)
                .where(RunRow.founder_id == founder_id)
                .order_by(RunRow.started_at.desc())
                .limit(limit)
            ).all()
            return [RunReport.model_validate_json(r.payload) for r in rows]

    def latest_run(self, founder_id: str) -> RunReport | None:
        """The most recent run for a founder, or None if they have never run one."""
        runs = self.list_runs(founder_id, limit=1)
        return runs[0] if runs else None

    def get_run(self, run_id: str) -> RunReport | None:
        """One run by id, however old.

        `list_runs` is capped, so a link to a run from six months ago has to
        resolve through the primary key or not at all.
        """
        with Session(self.engine) as session:
            row = session.get(RunRow, run_id)
            return RunReport.model_validate_json(row.payload) if row else None

    # -- opportunities --

    def save_opportunity(self, opportunity: Opportunity) -> None:
        """Upsert. `first_seen_at` is written once and never moved."""
        with Session(self.engine) as session:
            row = session.get(OpportunityRow, opportunity.id)
            if row is None:
                row = OpportunityRow(
                    opportunity_id=opportunity.id,
                    source=opportunity.source,
                    payload="",
                )
            # `description_excerpt` is untrusted text from the open web. It was
            # sanitised at ingestion; redact again here because this is the
            # persistence boundary and the boundary is where it belongs.
            row.payload = redact_json(opportunity.model_dump_json())
            row.source = opportunity.source
            row.updated_at = _now()
            session.add(row)
            session.commit()

    def get_opportunity(self, opportunity_id: str) -> Opportunity | None:
        """One opportunity by id, or None if no run has ever recorded it."""
        with Session(self.engine) as session:
            row = session.get(OpportunityRow, opportunity_id)
            return Opportunity.model_validate_json(row.payload) if row else None

    # -- eligibility clarifications --

    def save_eligibility_question(self, question: EligibilityQuestion) -> None:
        """Upsert a stable question while preserving its original creation time."""
        with Session(self.engine) as session:
            row = session.get(EligibilityQuestionRow, question.question_id)
            if row is None:
                row = EligibilityQuestionRow(
                    question_id=question.question_id,
                    founder_id=question.founder_id,
                    opportunity_id=question.opportunity_id,
                    status=question.status,
                    created_at=question.created_at,
                    payload="",
                )
            row.founder_id = question.founder_id
            row.opportunity_id = question.opportunity_id
            row.status = question.status
            row.payload = redact_json(question.model_dump_json())
            session.add(row)
            session.commit()

    def get_eligibility_question(self, question_id: str) -> EligibilityQuestion | None:
        """Load one clarification by id, or None."""
        with Session(self.engine) as session:
            row = session.get(EligibilityQuestionRow, question_id)
            return EligibilityQuestion.model_validate_json(row.payload) if row else None

    def list_eligibility_questions(
        self,
        founder_id: str,
        status: EligibilityQuestionStatus | Literal["all"] = "pending",
    ) -> list[EligibilityQuestion]:
        """Newest clarifications for one founder, optionally filtered by state."""
        with Session(self.engine) as session:
            statement = select(EligibilityQuestionRow).where(
                EligibilityQuestionRow.founder_id == founder_id
            )
            if status != "all":
                statement = statement.where(EligibilityQuestionRow.status == status)
            rows = session.exec(
                statement.order_by(EligibilityQuestionRow.created_at.desc())
            ).all()
            return [EligibilityQuestion.model_validate_json(row.payload) for row in rows]

    def answer_eligibility_question(
        self, question_id: str, answer: EligibilityAnswerValue
    ) -> EligibilityQuestion | None:
        """Edit an answer; `not_sure` deliberately leaves the question pending."""
        with Session(self.engine) as session:
            row = session.get(EligibilityQuestionRow, question_id)
            if row is None:
                return None
            question = EligibilityQuestion.model_validate_json(row.payload)
            question.answer = answer
            question.answer_updated_at = _now()
            question.updated_at = question.answer_updated_at
            question.reassessment_pending = answer in {"yes", "no"}
            question.align_status_with_answer()
            row.status = question.status
            row.payload = redact_json(question.model_dump_json())
            session.add(row)
            session.commit()
            return question

    def mark_eligibility_reassessed(
        self, founder_id: str, opportunity_id: str, *, before: datetime
    ) -> int:
        """Clear answers consumed by a run, without racing a newer edit."""
        with Session(self.engine) as session:
            rows = session.exec(
                select(EligibilityQuestionRow).where(
                    EligibilityQuestionRow.founder_id == founder_id,
                    EligibilityQuestionRow.opportunity_id == opportunity_id,
                )
            ).all()
            changed = 0
            for row in rows:
                question = EligibilityQuestion.model_validate_json(row.payload)
                if (
                    question.reassessment_pending
                    and question.answer_updated_at is not None
                    and question.answer_updated_at <= before
                ):
                    question.reassessment_pending = False
                    question.updated_at = _now()
                    row.payload = redact_json(question.model_dump_json())
                    session.add(row)
                    changed += 1
            if changed:
                session.commit()
            return changed

    # -- inbox --

    def has_surfaced(self, founder_id: str, opportunity_id: str) -> bool:
        """Idempotency check. Never notify twice (Section 10.7)."""
        with Session(self.engine) as session:
            found = session.exec(
                select(InboxRow.item_id).where(
                    InboxRow.idempotency_key == f"{founder_id}::{opportunity_id}"
                )
            ).first()
            return found is not None

    def save_inbox_item(self, item: InboxItem) -> bool:
        """Returns False when this opportunity was already surfaced.

        The uniqueness is enforced by the database, not by a check-then-act
        in Python. Double-notifying is the fastest way to make an agent feel
        broken, so it should be impossible rather than unlikely.
        """
        with Session(self.engine) as session:
            existing = session.exec(
                select(InboxRow).where(InboxRow.idempotency_key == item.idempotency_key)
            ).first()
            if existing is not None:
                return False
            session.add(
                InboxRow(
                    item_id=item.item_id,
                    idempotency_key=item.idempotency_key,
                    founder_id=item.founder_id,
                    opportunity_id=item.opportunity_id,
                    created_at=item.created_at,
                    payload=redact_json(item.model_dump_json()),
                )
            )
            session.commit()
            return True

    def list_inbox(self, founder_id: str, limit: int = 50) -> list[InboxItem]:
        """Inbox items for one founder, newest first, capped at `limit`.

        No state filter: dismissed and applied items come back too, and the
        caller (or the dashboard) decides what to show.
        """
        with Session(self.engine) as session:
            rows = session.exec(
                select(InboxRow)
                .where(InboxRow.founder_id == founder_id)
                .order_by(InboxRow.created_at.desc())
                .limit(limit)
            ).all()
            return [InboxItem.model_validate_json(r.payload) for r in rows]

    def get_inbox_item(self, item_id: str) -> InboxItem | None:
        """One inbox item by id, or None.

        Note this does not take a `founder_id` — authorization that the caller
        owns this item is the API layer's job, not the repository's.
        """
        with Session(self.engine) as session:
            row = session.get(InboxRow, item_id)
            return InboxItem.model_validate_json(row.payload) if row else None

    def set_inbox_state(self, item_id: str, state: InboxState) -> InboxItem | None:
        """Record what the founder did with an item.

        The only field a person is allowed to change. Everything else about an
        inbox item is what the run decided, and rewriting that would make the
        audit trail a record of the last edit rather than of the decision.
        """
        with Session(self.engine) as session:
            row = session.get(InboxRow, item_id)
            if row is None:
                return None
            item = InboxItem.model_validate_json(row.payload)
            item.state = state
            row.payload = redact_json(item.model_dump_json())
            session.add(row)
            session.commit()
            return item

    # -- drafts --

    def save_draft(self, draft: Draft) -> None:
        """Upsert a draft keyed by `draft_id`.

        Drafts are the one record type that legitimately mutates during a run,
        so re-saving the same id is the normal path, not a conflict.
        """
        with Session(self.engine) as session:
            row = session.get(DraftRow, draft.draft_id) or DraftRow(
                draft_id=draft.draft_id,
                founder_id=draft.founder_id,
                opportunity_id=draft.opportunity_id,
                payload="",
            )
            row.payload = redact_json(draft.model_dump_json())
            session.add(row)
            session.commit()

    def get_draft(self, draft_id: str) -> Draft | None:
        """One draft by id, or None."""
        with Session(self.engine) as session:
            row = session.get(DraftRow, draft_id)
            return Draft.model_validate_json(row.payload) if row else None

    def list_drafts(
        self, founder_id: str, opportunity_id: str | None = None
    ) -> list[Draft]:
        """Every draft for a founder, optionally narrowed to one opportunity.

        Without this a draft is reachable only through the inbox item that
        happened to link it, so a draft whose item was never created is
        invisible.
        """
        with Session(self.engine) as session:
            statement = select(DraftRow).where(DraftRow.founder_id == founder_id)
            if opportunity_id is not None:
                statement = statement.where(
                    DraftRow.opportunity_id == opportunity_id
                )
            rows = session.exec(statement.order_by(DraftRow.draft_id)).all()
            return [Draft.model_validate_json(r.payload) for r in rows]

    # -- jobs --

    def save_job(self, job: RunJob) -> None:
        """Insert or update one job.

        On first insert a duplicate idempotency key violates the unique
        index and raises — the caller re-reads the existing job and returns
        it. Check-then-insert in Python would leave a race window; the
        database does not.
        """
        with Session(self.engine) as session:
            row = session.get(JobRow, job.job_id) or JobRow(
                job_id=job.job_id,
                founder_id=job.founder_id,
                idempotency_key=(
                    f"{job.founder_id}::{job.idempotency_key}"
                    if job.idempotency_key
                    else None
                ),
                status=job.status,
                created_at=job.created_at,
                payload="",
            )
            row.status = job.status
            row.payload = redact_json(job.model_dump_json())
            session.add(row)
            session.commit()

    def get_job(self, job_id: str) -> RunJob | None:
        """One job by id, or None. The dashboard polls this while a run is in flight."""
        with Session(self.engine) as session:
            row = session.get(JobRow, job_id)
            return RunJob.model_validate_json(row.payload) if row else None

    def get_job_by_key(self, founder_id: str, idempotency_key: str) -> RunJob | None:
        """The job a retry should resolve to, if the original ever landed."""
        with Session(self.engine) as session:
            row = session.exec(
                select(JobRow).where(
                    JobRow.idempotency_key == f"{founder_id}::{idempotency_key}"
                )
            ).first()
            return RunJob.model_validate_json(row.payload) if row else None

    def list_jobs(self, founder_id: str, limit: int = 20) -> list[RunJob]:
        """Jobs for one founder, newest first, capped at `limit`."""
        with Session(self.engine) as session:
            rows = session.exec(
                select(JobRow)
                .where(JobRow.founder_id == founder_id)
                .order_by(JobRow.created_at.desc())
                .limit(limit)
            ).all()
            return [RunJob.model_validate_json(r.payload) for r in rows]

    def fail_orphaned_jobs(self, reason: str) -> list[RunJob]:
        """Mark every queued/running job failed. Called once, at startup.

        A crash mid-run leaves rows that claim to be running with no process
        behind them. This is the recovery: nothing may stay "running"
        forever, and a lie that says failed-when-crashed is the honest kind.
        """
        with Session(self.engine) as session:
            rows = session.exec(
                select(JobRow).where(JobRow.status.in_(["queued", "running"]))  # type: ignore[attr-defined]
            ).all()
            orphaned = []
            for row in rows:
                job = RunJob.model_validate_json(row.payload)
                job.status = "failed"
                job.error = reason
                job.finished_at = _now()
                row.status = job.status
                row.payload = redact_json(job.model_dump_json())
                session.add(row)
                orphaned.append(job)
            session.commit()
            return orphaned

    # -- recall --

    def remember_answer(self, founder_id: str, field: DraftField) -> None:
        """Store an answer the founder gave, so it is never asked again."""
        if not (field.answer or "").strip():
            return
        key = question_key(field.question)
        with Session(self.engine) as session:
            existing = session.exec(
                select(AnswerRow).where(
                    AnswerRow.founder_id == founder_id, AnswerRow.question_key == key
                )
            ).first()
            row = existing or AnswerRow(
                answer_id=f"{founder_id}::{key}"[:255],
                founder_id=founder_id,
                question_key=key,
                payload="",
            )
            row.payload = redact_json(field.model_dump_json())
            session.add(row)
            session.commit()

    def recall(self, founder_id: str, question: str) -> DraftField | None:
        """Has the founder answered a semantically equivalent question before?

        Two tiers, in this order:

        1.  **Exact after normalisation.** The highest-confidence path, and
            the only one that ran before semantic matching existed. It is
            tried first and it always wins.
        2.  **Semantic.** Only reached when tier 1 found nothing. Every
            candidate is filtered through `is_reusable` — protected field
            families (certification, signature, tax, payment, disclosure,
            authorization) and answers that were blocked, unaudited or
            unsupported are never offered — and the best remaining candidate
            must clear `self.similarity_threshold`.

        Candidates are always scoped to `founder_id`, so an answer can never
        cross from one founder to another regardless of how similar the
        questions are.

        A miss returns `None` and the founder answers one question. That is
        the error this method is tuned to make.
        """
        with Session(self.engine) as session:
            rows = session.exec(
                select(AnswerRow).where(AnswerRow.founder_id == founder_id)
            ).all()

            key = question_key(question)
            for row in rows:
                if row.question_key == key:
                    return self._as_reused(
                        row, match="exact", score=1.0, question=question
                    )

            if self.matcher is None:
                return None

            candidates: dict[str, AnswerRow] = {}
            for row in rows:
                field = DraftField.model_validate_json(row.payload)
                ok, _reason = is_reusable(field, question)
                if ok:
                    candidates[field.question] = row

            match = self.matcher.best_match(
                question,
                list(candidates),
                threshold=self.similarity_threshold,
            )
            if match is None:
                return None
            return self._as_reused(
                candidates[match.question],
                match=match.backend,
                score=match.score,
                question=question,
            )

    def _as_reused(
        self, row: AnswerRow, *, match: str, score: float, question: str
    ) -> DraftField | None:
        """Stamp a stored answer as REUSED, or refuse to.

        The reuse check runs here as well as in the candidate filter, so the
        exact-match path is governed by exactly the same rules as the
        semantic one. A protected field is re-asked even when its question
        matches character for character.
        """
        field = DraftField.model_validate_json(row.payload)
        ok, _reason = is_reusable(field, question)
        if not ok:
            return None
        field.status = "REUSED"
        field.reused_from = row.answer_id
        field.reuse_match = match
        field.reuse_source_question = field.question
        field.reuse_score = score
        return field
