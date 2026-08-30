"""Migrations run against a fresh database and a representative existing one.

`SQLModel.metadata.create_all()` cannot evolve a schema — it creates what is
missing and silently ignores a table whose shape has changed. Every deployed
database was made that way, so the first migration has to be able to *adopt*
one, not just create one. Both paths are tested here, plus the property that
matters most: an upgrade never destroys rows it did not create.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlmodel import SQLModel

import api.repository  # noqa: F401 — registers every table on the metadata
from agent.models import InboxItem, RunJob
from api.repository import SqliteRepository
from tests.factories import profile

REPO_ROOT = Path(__file__).resolve().parents[1]

EXPECTED_TABLES = {
    "answers",
    "drafts",
    "inbox",
    "jobs",
    "opportunities",
    "profiles",
    "runs",
}

#: The six tables that existed before the async job boundary added `jobs`.
#: A database in production right now looks exactly like this.
PRE_JOBS_TABLES = EXPECTED_TABLES - {"jobs"}


def alembic(*args: str, db_url: str) -> subprocess.CompletedProcess:
    """Run an Alembic command against `db_url`, returning the completed process."""
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "KAIROS_DB_URL": db_url,
            "HOME": str(REPO_ROOT),
        },
    )


def tables(db_url: str) -> set[str]:
    """The table names present in a database."""
    engine = sa.create_engine(db_url)
    try:
        return set(sa.inspect(engine).get_table_names())
    finally:
        engine.dispose()


def indexes(db_url: str, table: str) -> dict[str, bool]:
    """Index name -> unique."""
    engine = sa.create_engine(db_url)
    try:
        return {
            index["name"]: bool(index["unique"])
            for index in sa.inspect(engine).get_indexes(table)
        }
    finally:
        engine.dispose()


@pytest.fixture
def fresh_db(tmp_path) -> str:
    """A database path that does not exist yet, for testing a migration from nothing."""
    return f"sqlite:///{tmp_path}/fresh.db"


@pytest.fixture
def legacy_db(tmp_path) -> str:
    """A database as `create_all()` left it, before `jobs` existed, with rows.

    Built by creating only the six pre-jobs tables and writing real records
    through the repository, so "the upgrade kept the data" is a claim about
    actual rows rather than about an empty file.
    """
    url = f"sqlite:///{tmp_path}/legacy.db"
    engine = sa.create_engine(url)
    SQLModel.metadata.create_all(
        engine,
        tables=[
            SQLModel.metadata.tables[name] for name in sorted(PRE_JOBS_TABLES)
        ],
    )
    engine.dispose()

    repo = SqliteRepository(url)
    # SqliteRepository's own create_all would add `jobs` back; drop it so the
    # fixture really is a pre-jobs database.
    engine = sa.create_engine(url)
    with engine.begin() as conn:
        conn.execute(sa.text("DROP TABLE IF EXISTS jobs"))
    engine.dispose()

    repo.save_profile(profile(founder_id="founder_legacy"))
    repo.save_inbox_item(
        InboxItem(
            item_id="run_old:opp_1",
            founder_id="founder_legacy",
            opportunity_id="opp_1",
            kind="APPLY",
            headline="[DEMO] An old surfaced item",
            summary="Recorded before migrations existed.",
        )
    )
    return url


# ── Fresh database ───────────────────────────────────────────────────────────


def test_upgrade_creates_every_table_on_a_fresh_database(fresh_db):
    result = alembic("upgrade", "head", db_url=fresh_db)
    assert result.returncode == 0, result.stderr

    assert EXPECTED_TABLES <= tables(fresh_db)
    assert "alembic_version" in tables(fresh_db)


def test_the_schema_matches_what_the_application_expects(fresh_db):
    """After `upgrade head`, autogenerate must find nothing left to do."""
    alembic("upgrade", "head", db_url=fresh_db)
    result = alembic(
        "check", db_url=fresh_db
    )
    assert result.returncode == 0, (
        "the models and the migrations have diverged:\n"
        f"{result.stdout}\n{result.stderr}"
    )


def test_uniqueness_constraints_survive_the_migration(fresh_db):
    """The two indexes that enforce real invariants, not just speed."""
    alembic("upgrade", "head", db_url=fresh_db)

    assert indexes(fresh_db, "inbox")["ix_inbox_idempotency_key"] is True
    assert indexes(fresh_db, "jobs")["ix_jobs_idempotency_key"] is True


def test_a_migrated_database_actually_works(fresh_db):
    alembic("upgrade", "head", db_url=fresh_db)

    repo = SqliteRepository(fresh_db)
    repo.save_profile(profile(founder_id="founder_new"))
    repo.save_job(RunJob(job_id="job_1", founder_id="founder_new"))

    assert repo.get_profile("founder_new") is not None
    assert repo.get_job("job_1").status == "queued"


# ── Idempotency ──────────────────────────────────────────────────────────────


def test_upgrade_is_idempotent(fresh_db):
    first = alembic("upgrade", "head", db_url=fresh_db)
    second = alembic("upgrade", "head", db_url=fresh_db)

    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr


def test_upgrading_an_already_migrated_database_keeps_its_rows(fresh_db):
    alembic("upgrade", "head", db_url=fresh_db)
    repo = SqliteRepository(fresh_db)
    repo.save_profile(profile(founder_id="founder_keep"))

    assert alembic("upgrade", "head", db_url=fresh_db).returncode == 0
    assert repo.get_profile("founder_keep") is not None


# ── Existing database ────────────────────────────────────────────────────────


def test_upgrade_adopts_a_database_created_by_create_all(legacy_db):
    """The real migration: no alembic_version table, six tables, live rows."""
    assert "alembic_version" not in tables(legacy_db)
    assert "jobs" not in tables(legacy_db)

    result = alembic("upgrade", "head", db_url=legacy_db)
    assert result.returncode == 0, result.stderr

    assert EXPECTED_TABLES <= tables(legacy_db)


def test_adopting_an_existing_database_destroys_no_data(legacy_db):
    alembic("upgrade", "head", db_url=legacy_db)

    repo = SqliteRepository(legacy_db)
    kept = repo.get_profile("founder_legacy")
    assert kept is not None
    assert kept.founder_id == "founder_legacy"

    inbox = repo.list_inbox("founder_legacy")
    assert [item.item_id for item in inbox] == ["run_old:opp_1"]


def test_the_new_table_is_usable_after_adoption(legacy_db):
    alembic("upgrade", "head", db_url=legacy_db)

    repo = SqliteRepository(legacy_db)
    repo.save_job(
        RunJob(
            job_id="job_after_migration",
            founder_id="founder_legacy",
            idempotency_key="key-1",
        )
    )
    assert repo.get_job_by_key("founder_legacy", "key-1") is not None


def test_adoption_is_itself_idempotent(legacy_db):
    assert alembic("upgrade", "head", db_url=legacy_db).returncode == 0
    assert alembic("upgrade", "head", db_url=legacy_db).returncode == 0

    repo = SqliteRepository(legacy_db)
    assert repo.get_profile("founder_legacy") is not None


# ── Review path ──────────────────────────────────────────────────────────────


def test_offline_mode_emits_sql_without_touching_the_database(fresh_db):
    """`--sql` is how a migration gets reviewed before it runs anywhere."""
    result = alembic("upgrade", "head", "--sql", db_url=fresh_db)

    assert result.returncode == 0, result.stderr
    assert "CREATE TABLE" in result.stdout
    # Nothing was created: the file does not exist yet.
    assert not Path(fresh_db.removeprefix("sqlite:///")).exists()


def test_there_is_exactly_one_head(fresh_db):
    """Two heads means somebody branched the history and did not merge it."""
    result = alembic("heads", db_url=fresh_db)

    assert result.returncode == 0, result.stderr
    heads = [line for line in result.stdout.splitlines() if line.strip()]
    assert len(heads) == 1, result.stdout


def test_downgrade_round_trips(fresh_db):
    alembic("upgrade", "head", db_url=fresh_db)
    result = alembic("downgrade", "base", db_url=fresh_db)

    assert result.returncode == 0, result.stderr
    assert not (EXPECTED_TABLES & tables(fresh_db))


def test_no_migration_hardcodes_a_database_url():
    """A URL in a migration is a URL pointed at production by accident."""
    for path in (REPO_ROOT / "migrations").rglob("*.py"):
        text = path.read_text()
        assert "sqlite:///" not in text or path.name == "env.py", path
    env = (REPO_ROOT / "migrations" / "env.py").read_text()
    assert "KAIROS_DB_URL" in env

    ini = (REPO_ROOT / "alembic.ini").read_text()
    assert "sqlalchemy.url" not in ini


def test_the_alembic_version_table_is_not_a_repository_table():
    """Alembic owns it. The application reads it and never writes to it.

    If it were on `SQLModel.metadata`, `create_all()` would create it empty
    and every database would claim to be at no revision at all.
    """
    assert "alembic_version" not in SQLModel.metadata.tables

    source = (REPO_ROOT / "api" / "repository.py").read_text()
    for statement in ("INSERT INTO alembic_version", "UPDATE alembic_version"):
        assert statement not in source


def test_the_repository_can_report_the_schema_version(fresh_db):
    """Readiness needs to distinguish migrated from never-adopted.

    Compared against whatever `head` currently is rather than a literal
    revision. The literal made this test fail on every new migration for a
    reason unrelated to what it checks — that an upgraded database reports its
    revision and an unmanaged one reports None.
    """
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    unmanaged = SqliteRepository(fresh_db)
    assert unmanaged.schema_version() is None

    alembic("upgrade", "head", db_url=fresh_db)

    head = ScriptDirectory.from_config(Config("alembic.ini")).get_current_head()
    assert SqliteRepository(fresh_db).schema_version() == head


def test_create_schema_false_leaves_an_empty_database_empty(tmp_path):
    """Production relies on this: no table is conjured behind the migration."""
    url = f"sqlite:///{tmp_path}/no_create.db"
    SqliteRepository(url, create_schema=False)

    assert not (EXPECTED_TABLES & tables(url))


def test_migration_json_payloads_stay_readable_after_upgrade(legacy_db):
    """The payload column is where every model lives. Prove it survived."""
    alembic("upgrade", "head", db_url=legacy_db)

    engine = sa.create_engine(legacy_db)
    try:
        with engine.connect() as conn:
            payload = conn.execute(
                sa.text("SELECT payload FROM profiles WHERE founder_id = :id"),
                {"id": "founder_legacy"},
            ).scalar_one()
    finally:
        engine.dispose()

    assert json.loads(payload)["founder_id"] == "founder_legacy"
