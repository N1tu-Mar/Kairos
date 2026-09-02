"""conversational founder intake

Revision ID: 8d4f5c2a910b
Revises: a9c821e55b44
Create Date: 2026-09-01 12:00:00+00:00
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel
from alembic import context, op

revision: str = "8d4f5c2a910b"
down_revision: Union[str, None] = "a9c821e55b44"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _tables() -> set[str]:
    if context.is_offline_mode():
        return set()
    return set(sa.inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    existing = _tables()
    if "intake_sessions" not in existing:
        op.create_table(
            "intake_sessions",
            sa.Column("session_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
            sa.Column("founder_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
            sa.Column("active_founder_id", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
            sa.Column("status", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
            sa.Column("revision", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.Column("payload", sa.Text(), nullable=True),
            sa.PrimaryKeyConstraint("session_id"),
            sa.UniqueConstraint("active_founder_id"),
        )
        with op.batch_alter_table("intake_sessions") as batch:
            for column in (
                "created_at",
                "founder_id",
                "revision",
                "status",
                "updated_at",
            ):
                batch.create_index(batch.f(f"ix_intake_sessions_{column}"), [column])

    if "intake_messages" not in existing:
        op.create_table(
            "intake_messages",
            sa.Column("message_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
            sa.Column("session_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
            sa.Column("founder_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
            sa.Column("role", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
            sa.Column("idempotency_key", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("payload", sa.Text(), nullable=True),
            sa.PrimaryKeyConstraint("message_id"),
            sa.UniqueConstraint("idempotency_key"),
        )
        with op.batch_alter_table("intake_messages") as batch:
            for column in ("created_at", "founder_id", "role", "session_id"):
                batch.create_index(batch.f(f"ix_intake_messages_{column}"), [column])

    if "intake_documents" not in existing:
        op.create_table(
            "intake_documents",
            sa.Column("document_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
            sa.Column("session_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
            sa.Column("founder_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
            sa.Column("status", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("payload", sa.Text(), nullable=True),
            sa.PrimaryKeyConstraint("document_id"),
        )
        with op.batch_alter_table("intake_documents") as batch:
            for column in ("created_at", "founder_id", "session_id", "status"):
                batch.create_index(batch.f(f"ix_intake_documents_{column}"), [column])


def downgrade() -> None:
    existing = _tables()
    for table, columns in (
        ("intake_documents", ("status", "session_id", "founder_id", "created_at")),
        ("intake_messages", ("session_id", "role", "founder_id", "created_at")),
        (
            "intake_sessions",
            ("updated_at", "status", "revision", "founder_id", "created_at"),
        ),
    ):
        if table not in existing:
            continue
        with op.batch_alter_table(table) as batch:
            for column in columns:
                batch.drop_index(batch.f(f"ix_{table}_{column}"))
        op.drop_table(table)
