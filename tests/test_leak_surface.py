"""Nothing we own leaks through a boundary a stranger can read.

Two different kinds of secret meet here and they are not the same problem:

*   The **founder's** identifiers — SSN, EIN, bank number, email, address.
    `redact()` handles those, and it has always run at the persistence
    boundary. What it did not do was run at *every* persistence boundary.
*   **Ours** — a bearer token, an AWS key, the absolute path of the spend
    ledger. `scrub_secrets()` handles those. They arrive by a different
    route: interpolated into an exception message, which is then stringified
    into a field the API serves.

Each test below corresponds to a path that was actually open.
"""

from __future__ import annotations

import json

import pytest

from agent.sanitize import redact, safe_detail, scrub_secrets


# ── The scrubber itself ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw, must_not_contain",
    [
        ("failed with Authorization: Bearer sk-live-abc123def456", "abc123def456"),
        ("KAIROS_API_TOKEN=super-secret-value here", "super-secret-value"),
        ("creds AKIAIOSFODNN7EXAMPLE rejected", "AKIAIOSFODNN7EXAMPLE"),
        ("key sk-ant-api03-abcdefghijklmnop rejected", "sk-ant-api03"),
        ("password: hunter2hunter2", "hunter2hunter2"),
        ("api_key=abcdef123456", "abcdef123456"),
    ],
)
def test_credentials_are_scrubbed(raw, must_not_contain):
    assert must_not_contain not in scrub_secrets(raw)


def test_absolute_paths_are_reduced_to_their_basename():
    """An operator still learns which file. A stranger learns no layout."""
    out = scrub_secrets("spend ledger at /data/state/daily_spend.sqlite3 is unreadable")

    assert "/data/state" not in out
    assert "daily_spend.sqlite3" in out, "the actionable half must survive"


def test_the_scrubber_leaves_ordinary_prose_alone():
    text = "Scanned 214. Discarded 198. Judged 16. Surfaced 3."
    assert scrub_secrets(text) == text


def test_redaction_does_not_corrupt_the_json_it_runs_over():
    """`redact()` runs on serialised JSON, so over-matching breaks a row.

    The UEI lookahead used to be `.*\\d`, which scanned the whole remaining
    document: any 12-character uppercase token matched as long as a digit
    appeared later. `UNVERIFIABLE` is exactly 12 characters, so a draft field
    with an audit verdict next to any number stopped parsing.
    """
    payload = json.dumps(
        {"audit_verdict": "UNVERIFIABLE", "status": "UNSUPPORTED", "count": 7}
    )

    assert json.loads(redact(payload)) == json.loads(payload)


def test_a_real_uei_is_still_redacted():
    assert "ABC123DEF456" not in redact("Our UEI is ABC123DEF456 on file.")


def test_safe_detail_does_both_jobs_and_caps_length():
    out = safe_detail(
        "Bearer sk-live-xyzxyzxyz for founder@example.com at /a/b/c/x.db " + "z" * 900
    )
    assert "sk-live-xyzxyzxyz" not in out
    assert "founder@example.com" not in out  # redact() ran too
    assert len(out) <= 500


# ── Persistence: every row, not just three of them ───────────────────────────

SSN = "123-45-6789"


def test_every_persistence_call_redacts(tmp_path):
    """Six of nine rows used to store the founder's identifiers verbatim.

    A draft is the knowledge base rendered into prose — the most sensitive
    object in the system — and it was the least protected.
    """
    from agent.models import Draft, DraftField, InboxItem, RunJob, SourceSpan
    from api.repository import SqliteRepository

    repo = SqliteRepository(f"sqlite:///{tmp_path}/leak.db")

    field = DraftField(
        field_id="f1",
        question="What is your EIN?",
        answer=f"our number is {SSN}",
        status="GENERATED",
        provenance=[SourceSpan(chunk_id="c0", source="kb", text=f"SSN {SSN}")],
    )
    repo.save_draft(
        Draft(
            draft_id="d1",
            founder_id="founder_demo",
            opportunity_id="opp_1",
            fields=[field],
        )
    )
    repo.remember_answer("founder_demo", field)
    repo.save_inbox_item(
        InboxItem(
            item_id="run_1:opp_1",
            founder_id="founder_demo",
            opportunity_id="opp_1",
            kind="APPLY",
            headline="[DEMO] Fit",
            summary=f"mentions {SSN}",
        )
    )
    repo.save_job(
        RunJob(job_id="j1", founder_id="founder_demo", error=f"boom {SSN}")
    )

    # Read the raw rows, not the models — this is about what is on disk.
    import sqlalchemy as sa

    engine = sa.create_engine(f"sqlite:///{tmp_path}/leak.db")
    with engine.connect() as conn:
        for table in ("drafts", "answers", "inbox", "jobs"):
            blob = " ".join(
                str(r[0])
                for r in conn.execute(sa.text(f"SELECT payload FROM {table}"))
            )
            assert SSN not in blob, f"{table} stored the SSN verbatim"
            assert "[REDACTED_SSN]" in blob, f"{table} was not redacted at all"
    engine.dispose()


def test_inbox_state_change_does_not_undo_redaction(tmp_path):
    """`set_inbox_state` rewrites the whole payload on every state change."""
    from agent.models import InboxItem
    from api.repository import SqliteRepository

    repo = SqliteRepository(f"sqlite:///{tmp_path}/leak2.db")
    repo.save_inbox_item(
        InboxItem(
            item_id="run_1:opp_1",
            founder_id="founder_demo",
            opportunity_id="opp_1",
            kind="APPLY",
            headline="[DEMO] Fit",
            summary=f"mentions {SSN}",
        )
    )
    repo.set_inbox_state("run_1:opp_1", "dismissed")

    import sqlalchemy as sa

    engine = sa.create_engine(f"sqlite:///{tmp_path}/leak2.db")
    with engine.connect() as conn:
        blob = str(conn.execute(sa.text("SELECT payload FROM inbox")).scalar_one())
    engine.dispose()
    assert SSN not in blob


# ── Exception strings that reach an API response ─────────────────────────────


def test_a_ledger_path_never_reaches_halted_reason():
    """BudgetExceeded interpolates the ledger's absolute path."""
    from agent.budget import BudgetExceeded

    exc = BudgetExceeded(
        "DAILY_USD_CAP",
        "spend ledger at /data/state/daily_spend.sqlite3 is unreadable",
    )
    detail = safe_detail(f"{exc.cap}: {exc.detail}")

    assert "/data/state" not in detail
    assert "DAILY_USD_CAP" in detail


def test_raw_model_output_is_capped_before_it_reaches_a_report():
    """A pydantic ValidationError quotes input_value — the model's prose."""
    invented = "The founder has forty users and a patent. " * 40
    detail = safe_detail(f"validation failed: input_value={invented}")

    assert len(detail) <= 500


def test_job_error_is_sanitised_on_the_row_the_api_serves(tmp_path):
    import asyncio

    from agent.scheduler import RunLock, ScheduledRunFailureLog
    from api.jobs import execute_job, new_job
    from api.repository import SqliteRepository

    class ExplodingRepo(SqliteRepository):
        def get_profile(self, founder_id):
            raise RuntimeError(
                "db at /data/state/kairos.db died; "
                "Authorization: Bearer sk-live-topsecret999"
            )

    repo = ExplodingRepo(f"sqlite:///{tmp_path}/jobs.db")
    lock = RunLock(tmp_path / "locks")
    lease = lock.acquire(founder_id="founder_demo", run_kind="pipeline")
    job = new_job(
        founder_id="founder_demo",
        idempotency_key=None,
        source="scheduled",
        use_demo_catalog=True,
        include_grants_gov=False,
    )
    asyncio.run(
        execute_job(job, repo, lease, ScheduledRunFailureLog(tmp_path / "f.jsonl"))
    )

    error = repo.get_job(job.job_id).error
    assert "sk-live-topsecret999" not in error
    assert "/data/state" not in error


# ── Filesystem write paths ───────────────────────────────────────────────────


@pytest.mark.parametrize(
    "url",
    [
        "https://../evil",
        "https://.../evil",
        "https://..%2f..%2fetc/evil",
        "http://../../../../tmp/evil",
    ],
)
def test_a_hostile_host_cannot_escape_the_archive_directory(url):
    """A search API can hand us any URL. urlsplit accepts `..` as a netloc."""
    from agent.scraping.fetch import _slug

    slug = _slug(url)
    assert ".." not in slug
    assert not slug.startswith("/")
    assert slug.count("/") == 1, "a slug names exactly one directory"


@pytest.mark.parametrize("host", ["..", "...", "../..", "/etc/passwd"])
def test_a_hostile_host_cannot_escape_the_robots_cache(host):
    from agent.scraping.robots import RobotsCache

    name = RobotsCache._cache_name(host)
    assert ".." not in name
    assert "/" not in name
    assert name.endswith(".robots.txt")
