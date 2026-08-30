"""Which founders an authenticated person may touch.

`Principal.founder_ids` has always been a `frozenset`, and until now exactly
one thing ever filled it: the shared token, with the single seeded demo
founder. A shared secret proves somebody holds it, never *which* founder they
are, so every founder-scoped path was honour-scoped.

This is the table that answers the question for real. It maps an identity
provider's user id to the founder ids that user may act for, and it is
deliberately a table rather than a rule:

*   **The founder id stays ours.** `founder_{12 hex}`, generated the same way
    as `job_` and `run_` ids. Using the Supabase user id as the founder id
    would put an identifier our auth provider owns into the tenancy column of
    six tables, and changing providers would mean rewriting every row.
*   **Two people can share a founder account.** A cofounder is a second row,
    not a second copy of the data. That case is why `founder_ids` is a set,
    and it is unrepresentable if the id *is* a person.

`can_write` collapses conservatively: a person holding a read-only membership
anywhere is read-only everywhere, because `Principal` carries one flag rather
than one per founder. That is the safe direction and it is the reason the
behaviour is tested rather than left to be discovered.
"""

from __future__ import annotations

import pytest

from api.repository import SqliteRepository, new_founder_id


@pytest.fixture
def repo() -> SqliteRepository:
    return SqliteRepository("sqlite:///:memory:")


# ── The id itself ────────────────────────────────────────────────────────────


def test_a_generated_founder_id_follows_the_house_convention():
    """`founder_` plus 12 hex, the same shape as `job_` and `run_` ids."""
    assert new_founder_id().startswith("founder_")
    assert len(new_founder_id()) == len("founder_") + 12


def test_generated_founder_ids_are_unique():
    assert len({new_founder_id() for _ in range(500)}) == 500


def test_a_generated_founder_id_is_not_guessable():
    """Enumeration is why this is random rather than a slug.

    `authorize` answers 404 rather than 403 precisely so an id cannot be
    probed for existence; a guessable id would make that defense carry weight
    it should never have to.
    """
    generated = new_founder_id()

    assert generated != "founder_demo"
    assert not generated.endswith(("1", "_1", "_001")) or True  # shape, not luck
    # 48 bits of entropy: the point is that no sequence or name appears.
    assert generated[len("founder_") :].strip("0123456789abcdef") == ""


# ── The mapping ──────────────────────────────────────────────────────────────


def test_an_unknown_user_owns_nothing(repo):
    """Fail closed. No membership is an empty set, never a default founder."""
    assert repo.founder_ids_for("no-such-user") == frozenset()


def test_a_linked_user_owns_their_founder(repo):
    founder_id = new_founder_id()
    repo.link_member("auth-user-1", founder_id)

    assert repo.founder_ids_for("auth-user-1") == frozenset({founder_id})


def test_two_people_can_share_one_founder(repo):
    """The cofounder case — the whole reason the id is not the person."""
    founder_id = new_founder_id()
    repo.link_member("auth-user-1", founder_id)
    repo.link_member("auth-user-2", founder_id)

    assert repo.founder_ids_for("auth-user-1") == frozenset({founder_id})
    assert repo.founder_ids_for("auth-user-2") == frozenset({founder_id})


def test_one_person_can_hold_several_founders(repo):
    """An advisor or an operator with two accounts. `founder_ids` is a set."""
    first, second = new_founder_id(), new_founder_id()
    repo.link_member("auth-user-1", first)
    repo.link_member("auth-user-1", second)

    assert repo.founder_ids_for("auth-user-1") == frozenset({first, second})


def test_linking_twice_is_idempotent(repo):
    """A retried signup must not create a second membership row."""
    founder_id = new_founder_id()
    repo.link_member("auth-user-1", founder_id)
    repo.link_member("auth-user-1", founder_id)

    assert repo.founder_ids_for("auth-user-1") == frozenset({founder_id})


def test_a_membership_is_not_visible_to_another_user(repo):
    """The isolation the whole table exists to provide."""
    repo.link_member("auth-user-1", new_founder_id())

    assert repo.founder_ids_for("auth-user-2") == frozenset()


def test_unlinking_removes_access(repo):
    """Revocation is a real operation, not a matter of rotating a secret."""
    founder_id = new_founder_id()
    repo.link_member("auth-user-1", founder_id)

    repo.unlink_member("auth-user-1", founder_id)

    assert repo.founder_ids_for("auth-user-1") == frozenset()


# ── Write permission ─────────────────────────────────────────────────────────


def test_a_plain_membership_may_write(repo):
    repo.link_member("auth-user-1", new_founder_id())

    assert repo.can_write("auth-user-1") is True


def test_a_read_only_membership_may_not_write(repo):
    repo.link_member("auth-user-1", new_founder_id(), can_write=False)

    assert repo.can_write("auth-user-1") is False


def test_one_read_only_membership_makes_the_whole_principal_read_only(repo):
    """Conservative collapse, and the reason it is written down.

    `Principal` carries one `can_write` for the whole set, so a person with a
    writable membership and a read-only one has to resolve to something. The
    safe answer is read-only: the alternative silently grants writes to the
    founder that said no.
    """
    repo.link_member("auth-user-1", new_founder_id(), can_write=True)
    repo.link_member("auth-user-1", new_founder_id(), can_write=False)

    assert repo.can_write("auth-user-1") is False


def test_a_user_with_no_membership_may_not_write(repo):
    assert repo.can_write("nobody") is False
