"""Giving a newly signed-in person a founder of their own.

`api/auth.py` answers *who you are* and *what you may touch*, and it reads the
second from a table an operator controls. That is the right shape, and it
leaves a gap the moment sign-in becomes self-serve: someone who authenticates
with Google has a verified identity, no membership row, and a dashboard with
nothing on it. The old answer was `scripts/link_founder.py` — an operator runs
a command. That does not survive contact with a sign-up button.

This module is the deliberate exception to the warning at the top of
`link_founder.py`, and it is kept apart from `api/auth.py` on purpose.
Verification is a pure function of a token and a key; provisioning writes to
the database. Folding a write into the authenticator would mean every future
reader of that module has to ask which of its paths mutate state.

**The posture.** Auto-provisioning is on by default and is a *demo* posture,
the way `KAIROS_ALLOW_OPEN_API` is: anyone who reaches the sign-in page and
completes an OAuth flow creates a tenant. That is fine for a private
deployment and for a demo. Before this is public, restrict who may sign in —
at the identity provider, where the decision belongs — or set
`KAIROS_AUTO_PROVISION_FOUNDER=false` and grant memberships by hand.

**What is deliberately not here.** No `FounderProfile` row is created. A
founder id with no profile is the state the dashboard already knows how to
render — it is what sends someone into intake — so inventing a placeholder
profile would replace a real onboarding flow with a fake one.
"""

from __future__ import annotations

import logging

from api.auth import Principal, audit_event
from api.repository import new_founder_id

log = logging.getLogger("kairos.provisioning")

#: Principal methods that represent a person an identity provider vouched for.
#:
#: `shared_token` and `token_file` prove possession of a secret, and `open` is
#: nobody at all. Provisioning any of them would turn holding a credential
#: into the power to create tenants, which is the failure this whole module
#: has to avoid being.
VERIFIED_METHODS = frozenset({"supabase_jwt"})


def provision_founder(
    principal: Principal, repository, *, enabled: bool
) -> Principal:
    """Return `principal`, giving it a fresh founder first if it has none.

    Called once per request, from the authentication middleware, so the
    common path — a returning user who already owns a founder — must be a
    cheap no-op. It is: the principal arrives with a non-empty `founder_ids`
    read from the membership table and is returned untouched.

    Every guard here is a refusal to provision, and each rules out a distinct
    way this could hand out a tenant to something that is not a new person:

    *   `enabled` off — the operator-grants-access posture, unchanged.
    *   A method outside `VERIFIED_METHODS` — a secret's holder, not a person.
    *   A principal that already owns something — including a deliberately
        read-only membership, which is a grant that was narrowed on purpose
        and must not be widened by signing in again.
    *   A missing subject — nothing to attach a membership to.

    The ownership check asks the membership table rather than reading
    `principal.founder_ids`. The two agree on every path that exists today,
    and where they could ever disagree — a principal built from a stale read,
    or one constructed by a caller that skipped the table — the table is the
    authority, as it is for authorization itself. Getting that backwards here
    means minting a second founder for someone who already has one.
    """
    if not enabled:
        return principal
    if principal.method not in VERIFIED_METHODS:
        return principal
    if not principal.subject:
        return principal
    if repository.founder_ids_for(principal.subject):
        return principal

    founder_id = new_founder_id()
    repository.link_member(principal.subject, founder_id, can_write=True)

    # The one event that explains where a founder row came from when nobody
    # ran the CLI. The subject is an opaque provider id, not an email.
    audit_event(
        actor=principal.subject,
        action="founder.provisioned",
        resource=founder_id,
        outcome="ok",
    )
    log.info(
        "provisioned founder for new user",
        extra={"founder_id": founder_id, "method": principal.method},
    )

    # Re-read rather than trusting the write: the membership table is the
    # authority on what this principal owns, here as everywhere else.
    return Principal(
        subject=principal.subject,
        founder_ids=repository.founder_ids_for(principal.subject),
        can_write=repository.can_write(principal.subject),
        method=principal.method,
    )
