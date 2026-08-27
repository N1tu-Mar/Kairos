# Migrations

Alembic. The database URL comes from `KAIROS_DB_URL` at runtime, never from
`alembic.ini` — a URL committed to a config file is a URL somebody eventually
points at production by accident.

```bash
uv run alembic upgrade head        # apply
uv run alembic upgrade head --sql  # render the SQL for review, run nothing
uv run alembic current             # what revision is this database at
uv run alembic heads               # should always print exactly one line
uv run alembic check               # do the models and the migrations agree
```

## Adopting a database that already exists

Every database created before this directory existed was built by
`SQLModel.metadata.create_all()` at startup and has no `alembic_version`
table. `upgrade head` **adopts** it: the initial revision creates only the
tables that are absent, so an existing database gains `jobs` and keeps every
run report, draft and profile it already had. Running it twice is a no-op.

Nothing in a migration drops or rewrites a table that already exists. If a
pre-existing table has a different shape than a revision describes, that is a
divergence a person has to look at — `alembic check` reports it rather than
any migration silently reconciling it.

## Adding a revision

```bash
uv run alembic revision --autogenerate -m "what changed"
```

Then **read it**. Autogenerate is a first draft, and on SQLite it is a first
draft with a specific weakness: SQLite cannot `ALTER` most things in place, so
`render_as_batch` rewrites the whole table instead. A batch rewrite of a table
with rows in it is a data migration wearing a schema migration's clothes.
Check that the `op.` calls it produced do what you meant, and add a data step
explicitly if one is needed.

Two rules for a revision that will run against a database with rows in it:

- **Never drop a column in the same revision that stops writing to it.** Ship
  the code that stops writing, deploy, confirm, then drop in a later revision.
  Otherwise a rollback lands old code on a schema that no longer has the
  column.
- **Never make a nullable column `NOT NULL` without a backfill in the same
  revision.** The migration will fail on the first existing row, halfway
  through, and SQLite gives you no transactional DDL to unwind it.

## Production

The deploy runs `alembic upgrade head` **before** the new task starts, and
the application no longer creates its own schema in production
(`KAIROS_ENV=production` passes `create_schema=False`). A deployment that
skipped its migration therefore fails `/ready` with `schema: unmigrated`
rather than booting and 500ing at the first query against a missing table.

## Backup and restore

SQLite on EFS. Back up with the online backup API, not `cp` — copying a file
while a writer is mid-transaction produces a file that may not open.

```bash
# Back up (safe while the service is running)
sqlite3 /data/kairos.db ".backup '/data/backups/kairos-$(date -u +%Y%m%dT%H%M%SZ).db'"

# Verify the backup opens and is internally consistent, before trusting it
sqlite3 /data/backups/kairos-<stamp>.db "PRAGMA integrity_check;"   # -> ok
sqlite3 /data/backups/kairos-<stamp>.db "SELECT count(*) FROM runs;"

# Restore: stop the service first — a restore under a live writer is a
# corrupt database, not a restore.
aws ecs update-service --cluster kairos --service kairos-backend --desired-count 0
cp /data/backups/kairos-<stamp>.db /data/kairos.db
aws ecs update-service --cluster kairos --service kairos-backend --desired-count 1
```

A backup you have never restored is a hypothesis. `docs/runbooks.md` has the
restore drill, and it is worth running against a scratch volume before you
need it.

The state directory (`KAIROS_STATE_DIR`) holds the spend ledger, the run
leases and the scheduler failure log. Back it up too: losing the ledger means
losing today's proof that you are under the daily cap, and `DailyLedger`
refuses to spend without one rather than resetting to zero.
