"""atomically bounded intake document slots

Revision ID: f6d8a13c4e20
Revises: 7c2e90a1b4d8
Create Date: 2026-09-08 14:00:00+00:00
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import context, op

revision: str = "f6d8a13c4e20"
down_revision: Union[str, None] = "7c2e90a1b4d8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _columns() -> set[str]:
    if context.is_offline_mode():
        return set()
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns("intake_documents")}


def upgrade() -> None:
    if "slot" in _columns():
        return
    with op.batch_alter_table("intake_documents") as batch:
        batch.add_column(sa.Column("slot", sa.Integer(), nullable=True))
        batch.create_unique_constraint("uq_intake_documents_session_slot", ["session_id", "slot"])


def downgrade() -> None:
    if "slot" not in _columns():
        return
    with op.batch_alter_table("intake_documents") as batch:
        batch.drop_constraint("uq_intake_documents_session_slot", type_="unique")
        batch.drop_column("slot")
