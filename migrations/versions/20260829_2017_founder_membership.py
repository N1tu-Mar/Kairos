"""founder membership

Maps identity-provider users to the founders they may act for.

Until now `Principal.founder_ids` had exactly one filler: the shared token,
granting the single seeded demo founder. A shared secret proves somebody holds
it and never which founder they are, so every founder-scoped path was
honour-scoped. This table is what makes the set mean something.

Both columns are the primary key, which is the whole design: a person may hold
several founders, and a founder may have several people. The second case is a
cofounder, and it is unrepresentable in the alternative design where the auth
provider's user id simply *is* the founder id.

Guarded like the initial migration, so it adopts a database created by
`create_all()` in place rather than failing on a table that is already there.

Revision ID: 27e9f8b20767
Revises: 23556375cc0d
Create Date: 2026-08-29 20:17:27.115329+00:00

"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel
from alembic import context, op

revision: str = "27e9f8b20767"
down_revision: Union[str, None] = "23556375cc0d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _tables() -> set[str]:
    """What this database already has, or nothing in offline mode.

    The same guard the initial migration uses, for the same two reasons. A
    database built by `create_all()` before migrations existed must be
    adoptable by `alembic upgrade head` without dropping anything; and `--sql`
    has no connection to inspect, so it reports empty and renders the full
    CREATE for a reviewer.
    """
    if context.is_offline_mode():
        return set()
    return set(sa.inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    if "founder_members" in _tables():
        return

    op.create_table(
        "founder_members",
        sa.Column("auth_user_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("founder_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("can_write", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("auth_user_id", "founder_id"),
    )
    with op.batch_alter_table("founder_members", schema=None) as batch_op:
        # Indexed for "who has access to this founder" — the query an
        # operator runs when revoking, and the one a per-founder member list
        # will need. `auth_user_id` needs no index of its own: it leads the
        # primary key, so the lookup on every request already uses it.
        batch_op.create_index(
            batch_op.f("ix_founder_members_founder_id"), ["founder_id"], unique=False
        )


def downgrade() -> None:
    if "founder_members" not in _tables():
        return

    with op.batch_alter_table("founder_members", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_founder_members_founder_id"))

    op.drop_table("founder_members")
