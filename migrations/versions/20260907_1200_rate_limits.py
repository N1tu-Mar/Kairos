"""persistent API rate-limit buckets

Revision ID: 3f4b7a91d2c0
Revises: 8d4f5c2a910b
Create Date: 2026-09-07 12:00:00+00:00
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel
from alembic import context, op

revision: str = "3f4b7a91d2c0"
down_revision: Union[str, None] = "8d4f5c2a910b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _tables() -> set[str]:
    if context.is_offline_mode():
        return set()
    return set(sa.inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    if "rate_limits" in _tables():
        return
    op.create_table(
        "rate_limits",
        sa.Column("bucket_key", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("scope", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("bucket_key"),
    )
    with op.batch_alter_table("rate_limits") as batch:
        batch.create_index(batch.f("ix_rate_limits_scope"), ["scope"])
        batch.create_index(batch.f("ix_rate_limits_expires_at"), ["expires_at"])


def downgrade() -> None:
    if "rate_limits" not in _tables():
        return
    with op.batch_alter_table("rate_limits") as batch:
        batch.drop_index(batch.f("ix_rate_limits_expires_at"))
        batch.drop_index(batch.f("ix_rate_limits_scope"))
    op.drop_table("rate_limits")
