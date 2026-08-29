"""Alembic environment.

The URL comes from `KAIROS_DB_URL`, never from `alembic.ini`. The deployed
database is on a mounted volume whose path only the container knows, and a
URL committed to a config file is a URL somebody eventually points at
production by accident.

`target_metadata` is SQLModel's, imported through `api.repository` so every
table this application defines is registered before autogenerate looks. Import
that module and nothing else: importing `api.main` would pull in the whole
agent runtime and require model IDs to be present just to run a migration.
"""

from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool
from sqlmodel import SQLModel

# Registers every table on SQLModel.metadata as a side effect of import.
import api.repository  # noqa: F401

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = SQLModel.metadata


def database_url() -> str:
    """The URL Alembic should migrate, preferring the app's own configuration.

    Reading it from the same place the app does is what stops migrations
    running against a different database than the one being served.
    """
    url = os.getenv("KAIROS_DB_URL", "").strip()
    if not url:
        # Same default as agent/config.py. Stated here rather than imported
        # so a migration never needs a populated .env.
        return "sqlite:///./kairos.db"
    return url


def run_migrations_offline() -> None:
    """Emit SQL to stdout instead of running it.

    `alembic upgrade head --sql` is how you get a migration reviewed before
    it touches a database that matters.
    """
    context.configure(
        url=database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against a live connection — the normal path for `alembic upgrade`."""
    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = database_url()
    connectable = engine_from_config(
        section, prefix="sqlalchemy.", poolclass=pool.NullPool
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            # SQLite cannot ALTER most things in place. Batch mode rewrites
            # the table instead, which is the only way an ALTER lands on
            # SQLite at all — and it is what makes the upgrade path real
            # rather than theoretical.
            render_as_batch=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
