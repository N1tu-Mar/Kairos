"""Grant a Supabase user access to a founder.

There is deliberately no signup-creates-a-founder flow. Who gets an account,
and whether signing up should conjure a founder record, is a product decision
— and getting it wrong in the permissive direction means anyone who can reach
the sign-up page can create tenants in your database. So the first membership
is granted on purpose, by an operator, with this.

    # link an existing founder to a Supabase user
    uv run scripts/link_founder.py --user <supabase-user-id> --founder founder_demo

    # create a fresh founder id and link it in one step
    uv run scripts/link_founder.py --user <supabase-user-id> --new

    # look at what exists
    uv run scripts/link_founder.py --list

    # revoke
    uv run scripts/link_founder.py --user <supabase-user-id> \\
        --founder founder_abc123 --unlink

The user id is the `sub` claim of that person's token — the UUID shown in the
Supabase dashboard under Authentication → Users. It is not an email address
and not a key; nothing here needs a secret.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Same as scripts/run_scout.py: running this file directly puts `scripts/` on
# sys.path, not the repo root, so `agent` and `api` are not importable without
# this.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.config import settings  # noqa: E402
from api.repository import SqliteRepository, new_founder_id  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Link a Supabase user to a founder, or list and revoke.",
    )
    parser.add_argument(
        "--user",
        help="Supabase user id (the UUID under Authentication → Users).",
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--founder", help="An existing founder id.")
    group.add_argument(
        "--new",
        action="store_true",
        help="Generate a new founder id and link it.",
    )
    parser.add_argument(
        "--read-only",
        action="store_true",
        help=(
            "Grant read access only. Note this collapses conservatively: one "
            "read-only membership makes that person read-only everywhere."
        ),
    )
    parser.add_argument("--unlink", action="store_true", help="Revoke instead of grant.")
    parser.add_argument("--list", action="store_true", help="Show every membership.")
    args = parser.parse_args()

    repo = SqliteRepository(settings().db_url)

    if args.list:
        from sqlmodel import Session, select

        from api.repository import FounderMemberRow, ProfileRow

        with Session(repo.engine) as session:
            rows = session.exec(select(FounderMemberRow)).all()
            founders = session.exec(select(ProfileRow.founder_id)).all()

        if not rows:
            print("No memberships. Nobody can sign in and see anything yet.")
        else:
            print(f"{len(rows)} membership(s):")
            for row in rows:
                access = "read-write" if row.can_write else "read-only"
                print(f"  {row.auth_user_id}  →  {row.founder_id}  ({access})")
        print(f"\nFounder profiles that exist: {', '.join(founders) or 'none'}")
        return 0

    if not args.user:
        parser.error("--user is required unless you passed --list")

    if args.unlink:
        if not args.founder:
            parser.error("--unlink needs --founder")
        repo.unlink_member(args.user, args.founder)
        print(f"Revoked: {args.user} no longer has access to {args.founder}")
        return 0

    if args.new:
        founder_id = new_founder_id()
    elif args.founder:
        founder_id = args.founder
    else:
        parser.error("pass --founder <id> or --new")

    repo.link_member(args.user, founder_id, can_write=not args.read_only)

    access = "read-only" if args.read_only else "read-write"
    print(f"Linked {args.user} → {founder_id} ({access})")

    if repo.get_profile(founder_id) is None:
        # Worth saying plainly: the membership is real, the founder is not.
        # They will sign in successfully and find an empty dashboard.
        print(
            f"\nNote: no profile exists for {founder_id} yet. Sign in and save "
            "one from the dashboard's profile page."
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
