"""Run leases and scheduler-failure visibility.

Two production concerns that the demo could ignore and a deployment cannot:

*   **`RunLock`** — a durable lease keyed by `(founder_id, run_kind)`. Two
    invocations of the same run — a scheduler retry landing while the first
    attempt is still working, or a person double-clicking the manual button —
    must not both execute. The lease lives in SQLite, not in process memory,
    so it holds across processes and across restarts. Acquisition is a single
    `BEGIN IMMEDIATE` transaction, which is what makes it atomic: SQLite
    serialises writers at the file level, so two processes cannot both see
    "free" and both insert.

*   **`ScheduledRunFailureLog`** — an append-only, bounded record of
    invocations that failed to start or finish. CloudWatch keeps the full
    logs; this file exists so the API and the dashboard can answer "did last
    night's run fail?" without an AWS console.

Ownership: every acquired lease carries a random token, and release requires
it. A process cannot release a lease it does not hold — a slow first run
finishing after its lease expired cannot yank the lease out from under the
second run that legitimately took over.

Expiry: a lease older than its TTL is abandoned — the holder crashed without
releasing — and the next acquirer takes it over. The TTL therefore must be
comfortably longer than any legitimate run; it defaults to one hour, twice
the longest observed run.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from agent.sanitize import redact

#: A lease that has not been released after this long belongs to a crashed
#: holder. Must exceed the run timeout so a live run is never stolen from.
DEFAULT_LEASE_TTL_S = 3600

#: The failure log keeps this many entries per file. Older entries are
#: dropped on write — CloudWatch is the archive, this is the recent view.
FAILURE_HISTORY_LIMIT = 200

#: A failure detail longer than this is a stack trace, and stack traces
#: belong in logs, not in an API response.
MAX_DETAIL_CHARS = 500


# ── Lease ────────────────────────────────────────────────────────────────────


@dataclass
class Lease:
    """The result of one `acquire()` call, held or not.

    When `acquired` is False, `held_since` / `held_until` describe the
    existing holder so the caller can report *why* it was refused.
    """

    founder_id: str
    run_kind: str
    acquired: bool
    token: str | None = None
    held_since: float | None = None
    held_until: float | None = None
    _lock: "RunLock | None" = None
    _released: bool = False

    def release(self) -> bool:
        """Release, if this lease was acquired and still owns the row."""
        if not self.acquired or self._released or self._lock is None:
            return False
        self._released = True
        return self._lock.release(
            founder_id=self.founder_id, run_kind=self.run_kind, token=self.token
        )

    def __enter__(self) -> "Lease":
        return self

    def __exit__(self, *exc) -> None:
        self.release()


class RunLock:
    """Durable, cross-process run lease backed by SQLite.

    The database lives under the directory given to the constructor, so every
    process pointed at the same state directory shares one lock table. All
    mutation happens inside `BEGIN IMMEDIATE`, which takes SQLite's writer
    lock up front — the check and the insert are one atomic step.
    """

    def __init__(self, root: Path | str, ttl_seconds: int = DEFAULT_LEASE_TTL_S) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "leases.sqlite3"
        self.ttl_seconds = ttl_seconds
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS leases (
                    founder_id  TEXT NOT NULL,
                    run_kind    TEXT NOT NULL,
                    token       TEXT NOT NULL,
                    acquired_at REAL NOT NULL,
                    expires_at  REAL NOT NULL,
                    PRIMARY KEY (founder_id, run_kind)
                )
                """
            )

    def _connect(self) -> sqlite3.Connection:
        # The busy timeout makes concurrent acquirers queue on the writer
        # lock instead of failing with SQLITE_BUSY.
        return sqlite3.connect(self.path, timeout=10)

    def acquire(
        self, *, founder_id: str, run_kind: str, ttl_seconds: int | None = None
    ) -> Lease:
        """Try to take the lease. Never blocks on a held lease.

        Returns an acquired lease, or a not-acquired one describing the
        current holder. An expired lease is taken over in the same atomic
        step — recovery from a crashed holder needs no janitor process.
        """
        ttl = ttl_seconds if ttl_seconds is not None else self.ttl_seconds
        token = uuid.uuid4().hex
        now = time.time()
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT acquired_at, expires_at FROM leases"
                " WHERE founder_id = ? AND run_kind = ?",
                (founder_id, run_kind),
            ).fetchone()
            if row is not None and row[1] > now:
                conn.rollback()
                return Lease(
                    founder_id=founder_id,
                    run_kind=run_kind,
                    acquired=False,
                    held_since=row[0],
                    held_until=row[1],
                )
            conn.execute(
                """
                INSERT INTO leases (founder_id, run_kind, token, acquired_at, expires_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT (founder_id, run_kind) DO UPDATE SET
                    token = excluded.token,
                    acquired_at = excluded.acquired_at,
                    expires_at = excluded.expires_at
                """,
                (founder_id, run_kind, token, now, now + ttl),
            )
            conn.commit()
            return Lease(
                founder_id=founder_id,
                run_kind=run_kind,
                acquired=True,
                token=token,
                held_since=now,
                held_until=now + ttl,
                _lock=self,
            )
        finally:
            conn.close()

    def release(self, *, founder_id: str, run_kind: str, token: str | None) -> bool:
        """Release only if `token` still owns the lease.

        A holder whose lease expired and was taken over holds a stale token;
        its release deletes nothing and returns False, leaving the new
        holder's lease intact.
        """
        if not token:
            return False
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            cursor = conn.execute(
                "DELETE FROM leases WHERE founder_id = ? AND run_kind = ? AND token = ?",
                (founder_id, run_kind, token),
            )
            conn.commit()
            return cursor.rowcount == 1
        finally:
            conn.close()

    def holder(self, *, founder_id: str, run_kind: str) -> Lease | None:
        """The current unexpired holder, if any. Never exposes the token."""
        now = time.time()
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT acquired_at, expires_at FROM leases"
                " WHERE founder_id = ? AND run_kind = ? AND expires_at > ?",
                (founder_id, run_kind, now),
            ).fetchone()
            if row is None:
                return None
            return Lease(
                founder_id=founder_id,
                run_kind=run_kind,
                acquired=False,
                held_since=row[0],
                held_until=row[1],
            )
        finally:
            conn.close()


# ── Failure log ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class SchedulerFailure:
    """One failed invocation, sanitised for API exposure."""

    founder_id: str
    at: str
    source: str
    retry_count: int
    failure_class: str
    detail: str


def _sanitize_detail(detail: str) -> str:
    """Strip credentials and PII, then cap the length.

    The detail string may carry an exception message, and exception messages
    have a habit of embedding whatever was nearby — an Authorization header,
    a URL with a token in it. Redaction is not optional here.
    """
    cleaned = re.sub(r"(?i)bearer\s+[a-z0-9._\-]+", "Bearer [REDACTED]", detail)
    cleaned = re.sub(
        r"(?i)(kairos_api_token|authorization)\s*[=:]\s*\S+",
        r"\1=[REDACTED]",
        cleaned,
    )
    cleaned = redact(cleaned)
    return cleaned[:MAX_DETAIL_CHARS]


class ScheduledRunFailureLog:
    """Append-only JSONL of invocation failures, bounded to the recent past.

    One line per failure. Writes are a single appended line; the bound is
    enforced by rewriting the file through an atomic replace when it grows
    past the limit. Best-effort under concurrency by design — the ledger of
    record is CloudWatch, this is the dashboard's view.
    """

    def __init__(self, path: Path | str, limit: int = FAILURE_HISTORY_LIMIT) -> None:
        self.path = Path(path)
        self.limit = limit

    def record(
        self,
        *,
        founder_id: str,
        detail: str,
        source: str = "unknown",
        retry_count: int = 0,
        failure_class: str = "unknown",
    ) -> SchedulerFailure:
        failure = SchedulerFailure(
            founder_id=founder_id,
            at=datetime.now(timezone.utc).isoformat(),
            source=source,
            retry_count=retry_count,
            failure_class=failure_class,
            detail=_sanitize_detail(detail),
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(failure.__dict__, sort_keys=True) + "\n")
        self._trim()
        return failure

    def _trim(self) -> None:
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return
        if len(lines) <= self.limit:
            return
        keep = lines[-self.limit :]
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text("\n".join(keep) + "\n", encoding="utf-8")
        os.replace(tmp, self.path)

    def _load(self) -> list[SchedulerFailure]:
        if not self.path.exists():
            return []
        failures: list[SchedulerFailure] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                failures.append(SchedulerFailure(**json.loads(line)))
            except (json.JSONDecodeError, TypeError):
                # A torn line from a concurrent write loses one entry, not
                # the whole history.
                continue
        return failures

    def recent(self, founder_id: str, limit: int = 20) -> list[SchedulerFailure]:
        """Newest first, scoped to one founder."""
        mine = [f for f in self._load() if f.founder_id == founder_id]
        return list(reversed(mine))[:limit]

    def latest(self, founder_id: str) -> SchedulerFailure | None:
        failures = self.recent(founder_id, limit=1)
        return failures[0] if failures else None
