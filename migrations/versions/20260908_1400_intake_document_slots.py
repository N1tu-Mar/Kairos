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


def _offline_shape(*, with_slot: bool) -> sa.Table:
    """Describe the pre/post table so SQLite can render batch SQL offline."""
    columns: list[sa.Column] = [
        sa.Column("document_id", sa.String(), primary_key=True),
        sa.Column("session_id", sa.String(), nullable=False),
        sa.Column("founder_id", sa.String(), nullable=False),
    ]
    if with_slot:
        columns.append(sa.Column("slot", sa.Integer(), nullable=True))
    columns.extend(
        [
            sa.Column("status", sa.String(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("payload", sa.Text(), nullable=True),
        ]
    )
    constraints = (
        [sa.UniqueConstraint("session_id", "slot", name="uq_intake_documents_session_slot")]
        if with_slot
        else []
    )
    return sa.Table("intake_documents", sa.MetaData(), *columns, *constraints)


def upgrade() -> None:
    if "slot" in _columns():
        return
    kwargs = (
        {"copy_from": _offline_shape(with_slot=False)}
        if context.is_offline_mode()
        else {}
    )
    with op.batch_alter_table("intake_documents", **kwargs) as batch:
        batch.add_column(sa.Column("slot", sa.Integer(), nullable=True))
        batch.create_unique_constraint("uq_intake_documents_session_slot", ["session_id", "slot"])


def downgrade() -> None:
    if "slot" not in _columns():
        return
    kwargs = (
        {"copy_from": _offline_shape(with_slot=True)}
        if context.is_offline_mode()
        else {}
    )
    with op.batch_alter_table("intake_documents", **kwargs) as batch:
        batch.drop_constraint("uq_intake_documents_session_slot", type_="unique")
        batch.drop_column("slot")
