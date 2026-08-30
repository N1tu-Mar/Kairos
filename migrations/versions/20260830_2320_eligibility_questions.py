"""eligibility questions

Persists founder-answerable eligibility clarifications separately from the
application-answer recall table. The latter stores prose for forms; this table
stores yes/no/not-sure decisions tied to one opportunity and must never be
reused implicitly.

Revision ID: a9c821e55b44
Revises: 27e9f8b20767
Create Date: 2026-08-30 23:20:00+00:00
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel
from alembic import context, op

revision: str = "a9c821e55b44"
down_revision: Union[str, None] = "27e9f8b20767"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _tables() -> set[str]:
    if context.is_offline_mode():
        return set()
    return set(sa.inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    if "eligibility_questions" in _tables():
        return

    op.create_table(
        "eligibility_questions",
        sa.Column("question_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("founder_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("opportunity_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("status", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("payload", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("question_id"),
    )
    with op.batch_alter_table("eligibility_questions", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_eligibility_questions_created_at"),
            ["created_at"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_eligibility_questions_founder_id"),
            ["founder_id"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_eligibility_questions_opportunity_id"),
            ["opportunity_id"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_eligibility_questions_status"), ["status"], unique=False
        )


def downgrade() -> None:
    if "eligibility_questions" not in _tables():
        return

    with op.batch_alter_table("eligibility_questions", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_eligibility_questions_status"))
        batch_op.drop_index(batch_op.f("ix_eligibility_questions_opportunity_id"))
        batch_op.drop_index(batch_op.f("ix_eligibility_questions_founder_id"))
        batch_op.drop_index(batch_op.f("ix_eligibility_questions_created_at"))
    op.drop_table("eligibility_questions")
