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
from typing import Protocol, TypeVar

from sqlalchemy import Column, Text
from sqlmodel import Field, Session, SQLModel, create_engine, select

from agent.models import (
    Draft,
    DraftField,
    FounderProfile,
    InboxItem,
    InboxState,
    Opportunity,
    RunJob,
    RunReport,
)
from agent.sanitize import redact
from agent.semantic import (
    DEFAULT_MATCHER,
    DEFAULT_THRESHOLD,
    SemanticMatcher,
    is_reusable,
)

T = TypeVar("T")


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ── Tables ───────────────────────────────────────────────────────────────────


class ProfileRow(SQLModel, table=True):
    __tablename__ = "profiles"
    founder_id: str = Field(primary_key=True)
    updated_at: datetime = Field(default_factory=_now)
    payload: str = Field(sa_column=Column(Text))


class RunRow(SQLModel, table=True):
    __tablename__ = "runs"
    run_id: str = Field(primary_key=True)
    founder_id: str = Field(index=True)
    started_at: datetime = Field(index=True)
    payload: str = Field(sa_column=Column(Text))


class InboxRow(SQLModel, table=True):
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
    __tablename__ = "drafts"
    draft_id: str = Field(primary_key=True)
    founder_id: str = Field(index=True)
    opportunity_id: str = Field(index=True)
    payload: str = Field(sa_column=Column(Text))


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


# ── Interface ────────────────────────────────────────────────────────────────


class Repository(Protocol):
    def save_profile(self, profile: FounderProfile) -> None: ...
    def get_profile(self, founder_id: str) -> FounderProfile | None: ...

    def save_run(self, report: RunReport) -> None: ...
    def latest_run(self, founder_id: str) -> RunReport | None: ...
    def get_run(self, run_id: str) -> RunReport | None: ...
    def list_runs(self, founder_id: str, limit: int = 20) -> list[RunReport]: ...

    def save_opportunity(self, opportunity: Opportunity) -> None: ...
    def get_opportunity(self, opportunity_id: str) -> Opportunity | None: ...

    def has_surfaced(self, founder_id: str, opportunity_id: str) -> bool: ...
    def save_inbox_item(self, item: InboxItem) -> bool: ...
    def list_inbox(self, founder_id: str, limit: int = 50) -> list[InboxItem]: ...
    def get_inbox_item(self, item_id: str) -> InboxItem | None: ...
    def set_inbox_state(self, item_id: str, state: InboxState) -> InboxItem | None: ...

    def save_draft(self, draft: Draft) -> None: ...
    def get_draft(self, draft_id: str) -> Draft | None: ...
    def list_drafts(
        self, founder_id: str, opportunity_id: str | None = None
    ) -> list[Draft]: ...

    def remember_answer(self, founder_id: str, field: DraftField) -> None: ...
    def recall(self, founder_id: str, question: str) -> DraftField | None: ...

    def save_job(self, job: RunJob) -> None: ...
    def get_job(self, job_id: str) -> RunJob | None: ...
    def get_job_by_key(self, founder_id: str, idempotency_key: str) -> RunJob | None: ...
    def list_jobs(self, founder_id: str, limit: int = 20) -> list[RunJob]: ...
    def fail_orphaned_jobs(self, reason: str) -> list[RunJob]: ...


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
    ) -> None:
        """`matcher=None` disables semantic recall and leaves exact matching
        only — which is what the pre-semantic behaviour was, still reachable
        for anyone who wants the strictest possible reuse policy."""
        self.engine = create_engine(url, echo=echo)
        self.matcher = matcher
        self.similarity_threshold = similarity_threshold
        SQLModel.metadata.create_all(self.engine)

    # -- profiles --

    def save_profile(self, profile: FounderProfile) -> None:
        with Session(self.engine) as session:
            row = session.get(ProfileRow, profile.founder_id) or ProfileRow(
                founder_id=profile.founder_id, payload=""
            )
            # Redact at the persistence boundary, not at display time
            # (Section 10.4).
            row.payload = redact(profile.model_dump_json())
            row.updated_at = _now()
            session.add(row)
            session.commit()

    def get_profile(self, founder_id: str) -> FounderProfile | None:
        with Session(self.engine) as session:
            row = session.get(ProfileRow, founder_id)
            return FounderProfile.model_validate_json(row.payload) if row else None

    # -- runs --

    def save_run(self, report: RunReport) -> None:
        with Session(self.engine) as session:
            row = session.get(RunRow, report.run_id) or RunRow(
                run_id=report.run_id,
                founder_id=report.founder_id,
                started_at=report.started_at,
                payload="",
            )
            row.payload = redact(report.model_dump_json())
            session.add(row)
            session.commit()

    def list_runs(self, founder_id: str, limit: int = 20) -> list[RunReport]:
        with Session(self.engine) as session:
            rows = session.exec(
                select(RunRow)
                .where(RunRow.founder_id == founder_id)
                .order_by(RunRow.started_at.desc())
                .limit(limit)
            ).all()
            return [RunReport.model_validate_json(r.payload) for r in rows]

    def latest_run(self, founder_id: str) -> RunReport | None:
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
            row.payload = redact(opportunity.model_dump_json())
            row.source = opportunity.source
            row.updated_at = _now()
            session.add(row)
            session.commit()

    def get_opportunity(self, opportunity_id: str) -> Opportunity | None:
        with Session(self.engine) as session:
            row = session.get(OpportunityRow, opportunity_id)
            return Opportunity.model_validate_json(row.payload) if row else None

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
                    payload=item.model_dump_json(),
                )
            )
            session.commit()
            return True

    def list_inbox(self, founder_id: str, limit: int = 50) -> list[InboxItem]:
        with Session(self.engine) as session:
            rows = session.exec(
                select(InboxRow)
                .where(InboxRow.founder_id == founder_id)
                .order_by(InboxRow.created_at.desc())
                .limit(limit)
            ).all()
            return [InboxItem.model_validate_json(r.payload) for r in rows]

    def get_inbox_item(self, item_id: str) -> InboxItem | None:
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
            row.payload = item.model_dump_json()
            session.add(row)
            session.commit()
            return item

    # -- drafts --

    def save_draft(self, draft: Draft) -> None:
        with Session(self.engine) as session:
            row = session.get(DraftRow, draft.draft_id) or DraftRow(
                draft_id=draft.draft_id,
                founder_id=draft.founder_id,
                opportunity_id=draft.opportunity_id,
                payload="",
            )
            row.payload = draft.model_dump_json()
            session.add(row)
            session.commit()

    def get_draft(self, draft_id: str) -> Draft | None:
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
            row.payload = job.model_dump_json()
            session.add(row)
            session.commit()

    def get_job(self, job_id: str) -> RunJob | None:
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
                row.payload = job.model_dump_json()
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
            row.payload = field.model_dump_json()
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
