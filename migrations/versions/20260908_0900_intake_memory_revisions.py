"""append-only founder working-memory revisions

Revision ID: 7c2e90a1b4d8
Revises: 3f4b7a91d2c0
Create Date: 2026-09-08 09:00:00+00:00
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel
from alembic import context, op

revision: str = "7c2e90a1b4d8"
down_revision: Union[str, None] = "3f4b7a91d2c0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _tables() -> set[str]:
    if context.is_offline_mode():
        return set()
    return set(sa.inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    if "intake_memory_revisions" in _tables():
        return
    op.create_table(
        "intake_memory_revisions",
        sa.Column("snapshot_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("session_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("founder_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("payload", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("snapshot_id"),
    )
    with op.batch_alter_table("intake_memory_revisions") as batch:
        for column in ("created_at", "founder_id", "revision", "session_id"):
            batch.create_index(
                batch.f(f"ix_intake_memory_revisions_{column}"), [column]
            )


def downgrade() -> None:
    if "intake_memory_revisions" not in _tables():
        return
    with op.batch_alter_table("intake_memory_revisions") as batch:
        for column in ("session_id", "revision", "founder_id", "created_at"):
            batch.drop_index(batch.f(f"ix_intake_memory_revisions_{column}"))
    op.drop_table("intake_memory_revisions")
