"""What happens the first time a real person signs in.

`scripts/link_founder.py` opens with a warning that there is deliberately no
signup-creates-a-founder flow, because getting it wrong in the permissive
direction means anyone who reaches the login page creates a tenant. That
warning still stands. This module is the deliberate, flagged exception to it.

Self-serve sign-in — Google, GitHub, email — produces a verified identity with
no membership row, and a dashboard with nothing to render. Two honest answers
exist: refuse and make an operator run a CLI, or provision on first sight.
Kairos takes the second, behind `KAIROS_AUTO_PROVISION_FOUNDER`, and these
tests pin down the parts that make it safe rather than merely convenient:

*   **Only for verified identities.** A shared token or an anonymous local
    principal is not a person, and provisioning one would mint a founder for
    whoever holds a secret.
*   **Exactly once.** The check runs on every request, so a second request
    from the same subject must find the membership rather than make another.
*   **Off is really off.** With the flag disabled the principal comes back
    untouched, owning nothing, and the API's own 404s do the rest.
"""

from __future__ import annotations

import pytest

from api.auth import Principal
from api.provisioning import provision_founder
from api.repository import SqliteRepository


@pytest.fixture
def repo() -> SqliteRepository:
    return SqliteRepository("sqlite:///:memory:")


def signed_in(subject: str = "user-abc", **overrides) -> Principal:
    """A principal as `SupabaseJWTAuthenticator` builds one for a new user."""
    fields = {
        "subject": subject,
        "founder_ids": frozenset(),
        "can_write": True,
        "method": "supabase_jwt",
    }
    fields.update(overrides)
    return Principal(**fields)


# ── The provisioning itself ──────────────────────────────────────────────────


def test_a_new_verified_user_gets_a_founder(repo):
    """The whole point: sign in with Google, land on a dashboard of your own."""
    provisioned = provision_founder(signed_in(), repo, enabled=True)

    assert len(provisioned.founder_ids) == 1
    assert next(iter(provisioned.founder_ids)).startswith("founder_")


def test_the_new_founder_is_persisted_as_a_membership(repo):
    """Provisioning writes the row, not just the in-memory principal.

    A principal that owns a founder the table has never heard of would work
    for exactly one request and 404 on the next.
    """
    provisioned = provision_founder(signed_in(), repo, enabled=True)

    assert repo.founder_ids_for("user-abc") == provisioned.founder_ids


def test_the_provisioned_principal_keeps_its_identity(repo):
    """Same subject, same method. Provisioning grants access; it is not a login."""
    provisioned = provision_founder(signed_in(), repo, enabled=True)

    assert provisioned.subject == "user-abc"
    assert provisioned.method == "supabase_jwt"


def test_a_provisioned_founder_may_write(repo):
    """Their own founder, so read-only would leave them unable to fill intake."""
    assert provision_founder(signed_in(), repo, enabled=True).can_write is True


# ── Exactly once ─────────────────────────────────────────────────────────────


def test_an_existing_member_is_left_alone(repo):
    """Someone an operator already linked keeps precisely what they were granted."""
    repo.link_member("user-abc", "founder_demo")
    linked = signed_in(founder_ids=repo.founder_ids_for("user-abc"))

    provisioned = provision_founder(linked, repo, enabled=True)

    assert provisioned.founder_ids == frozenset({"founder_demo"})


def test_a_stale_principal_does_not_earn_a_second_founder(repo):
    """The membership table decides, not the principal handed in.

    A principal whose `founder_ids` disagrees with the table — built from a
    stale read, or by a caller that skipped the table — must not be read as
    "new here". Trusting it would mint a founder per request.
    """
    repo.link_member("user-abc", "founder_demo")

    provisioned = provision_founder(signed_in(), repo, enabled=True)

    assert repo.founder_ids_for("user-abc") == frozenset({"founder_demo"})
    assert provisioned.founder_ids == frozenset()


def test_provisioning_is_not_repeated_on_the_next_request(repo):
    """The check runs per request; a second founder per request is a leak."""
    first = provision_founder(signed_in(), repo, enabled=True)
    # The second request re-reads membership, exactly as the middleware does.
    returning = signed_in(founder_ids=repo.founder_ids_for("user-abc"))

    second = provision_founder(returning, repo, enabled=True)

    assert second.founder_ids == first.founder_ids
    assert len(repo.founder_ids_for("user-abc")) == 1


def test_two_users_get_two_different_founders(repo):
    """Tenancy. Sharing a founder id between strangers is the worst outcome here."""
    one = provision_founder(signed_in("user-one"), repo, enabled=True)
    two = provision_founder(signed_in("user-two"), repo, enabled=True)

    assert one.founder_ids.isdisjoint(two.founder_ids)


# ── The limits ───────────────────────────────────────────────────────────────


def test_the_flag_off_provisions_nothing(repo):
    """`KAIROS_AUTO_PROVISION_FOUNDER` unset leaves the old refusal in place."""
    provisioned = provision_founder(signed_in(), repo, enabled=False)

    assert provisioned.founder_ids == frozenset()
    assert repo.founder_ids_for("user-abc") == frozenset()


@pytest.mark.parametrize("method", ["shared_token", "token_file", "open"])
def test_only_a_verified_identity_is_provisioned(repo, method):
    """A secret's holder is not a person.

    The shared token already resolves to the seeded demo founder, and the
    anonymous local principal is not anybody at all. Minting a founder for
    either would turn possession of a credential into tenant creation.
    """
    provisioned = provision_founder(signed_in(method=method), repo, enabled=True)

    assert provisioned.founder_ids == frozenset()
    assert repo.founder_ids_for("user-abc") == frozenset()


def test_a_principal_without_a_subject_is_not_provisioned(repo):
    """No subject means no one to attach a membership to."""
    provisioned = provision_founder(signed_in(subject=""), repo, enabled=True)

    assert provisioned.founder_ids == frozenset()


def test_a_read_only_member_is_not_given_a_founder_of_their_own(repo):
    """Read-only is a deliberate grant, and it is not an empty membership.

    Only the genuinely unlinked are provisioned. Someone whose access was
    narrowed on purpose must not get a writable founder by signing in again.
    """
    repo.link_member("user-abc", "founder_demo", can_write=False)
    returning = signed_in(
        founder_ids=repo.founder_ids_for("user-abc"),
        can_write=repo.can_write("user-abc"),
    )

    provisioned = provision_founder(returning, repo, enabled=True)

    assert provisioned.founder_ids == frozenset({"founder_demo"})
    assert provisioned.can_write is False
