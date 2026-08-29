"""Fetching Supabase's signing keys, and surviving a rotation.

Supabase defaults new projects to asymmetric signing (ES256), publishes the
public keys at `{issuer}/.well-known/jwks.json`, and **rotates them**. The
first version of `SupabaseJWTAuthenticator` took a static PEM, which works
right up until the rotation, at which point every login fails and the fix is
a human pasting a new key into a secret store.

So the key is fetched by `kid` instead. Three properties matter, and each is
a way the naive version goes wrong:

*   A token names its signing key in the header (`kid`). The verifier looks up
    *that* key, rather than trying the one key it happens to hold.
*   A rotation is picked up without a deploy: an unknown `kid` refetches.
*   The refetch is bounded. A stream of tokens carrying random `kid` values
    must not become a stream of outbound requests to the auth server — that
    turns a public endpoint into an amplifier pointed at Supabase.

Nothing here touches the network. The JWKS client is injected, which is also
how the production path stays honest: it is one object with one method, so
the thing under test is the same thing that runs.
"""

from __future__ import annotations

import time

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from api.auth import AuthError, SupabaseJWTAuthenticator
from api.repository import SqliteRepository, new_founder_id

ISSUER = "https://abcdefghijklm.supabase.co/auth/v1"
USER_ID = "a3f1c9e2-7b44-4d18-9f2a-1c8e5b0d6a37"


class FakeJWKS:
    """Stands in for `jwt.PyJWKClient`. Counts fetches, so caching is testable."""

    def __init__(self, keys: dict[str, str]) -> None:
        #: kid -> PEM public key
        self.keys = keys
        self.lookups = 0

    def get_public_key(self, kid: str) -> str:
        self.lookups += 1
        try:
            return self.keys[kid]
        except KeyError:
            raise LookupError(f"no signing key for kid {kid}") from None


def keypair() -> tuple[str, str]:
    """An ES256 keypair — the algorithm Supabase gives new projects."""
    private = ec.generate_private_key(ec.SECP256R1())
    private_pem = private.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    public_pem = (
        private.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode()
    )
    return private_pem, public_pem


def sign(private_pem: str, kid: str, **overrides) -> str:
    claims = {
        "sub": USER_ID,
        "aud": "authenticated",
        "iss": ISSUER,
        "exp": int(time.time()) + 3600,
    }
    claims.update(overrides)
    return jwt.encode(claims, private_pem, algorithm="ES256", headers={"kid": kid})


@pytest.fixture
def repo() -> SqliteRepository:
    return SqliteRepository("sqlite:///:memory:")


@pytest.fixture
def founder_id(repo) -> str:
    generated = new_founder_id()
    repo.link_member(USER_ID, generated)
    return generated


# ── The happy path ───────────────────────────────────────────────────────────


def test_a_token_is_verified_against_the_key_its_header_names(repo, founder_id):
    private_pem, public_pem = keypair()
    jwks = FakeJWKS({"key-1": public_pem})
    auth = SupabaseJWTAuthenticator(repository=repo, issuer=ISSUER, jwks=jwks)

    principal = auth.authenticate(f"Bearer {sign(private_pem, 'key-1')}")

    assert principal.owns(founder_id)


def test_the_right_key_is_chosen_when_several_are_published(repo, founder_id):
    """During a rotation both keys are live. Picking the wrong one fails a
    valid token, which is an outage rather than a breach — but still an outage.
    """
    old_private, old_public = keypair()
    new_private, new_public = keypair()
    jwks = FakeJWKS({"old": old_public, "new": new_public})
    auth = SupabaseJWTAuthenticator(repository=repo, issuer=ISSUER, jwks=jwks)

    assert auth.authenticate(f"Bearer {sign(old_private, 'old')}").owns(founder_id)
    assert auth.authenticate(f"Bearer {sign(new_private, 'new')}").owns(founder_id)


def test_a_rotation_is_picked_up_without_a_restart(repo, founder_id):
    """The whole reason this is not a static PEM."""
    old_private, old_public = keypair()
    jwks = FakeJWKS({"old": old_public})
    auth = SupabaseJWTAuthenticator(repository=repo, issuer=ISSUER, jwks=jwks)
    assert auth.authenticate(f"Bearer {sign(old_private, 'old')}").owns(founder_id)

    # Supabase rotates. The new key appears at the JWKS endpoint.
    new_private, new_public = keypair()
    jwks.keys["new"] = new_public

    assert auth.authenticate(f"Bearer {sign(new_private, 'new')}").owns(founder_id)


# ── Refusals ─────────────────────────────────────────────────────────────────


def test_a_token_signed_by_a_key_that_is_not_published_is_refused(repo, founder_id):
    """Correctly formed, correctly signed — by the wrong hands."""
    attacker_private, _ = keypair()
    _, real_public = keypair()
    jwks = FakeJWKS({"key-1": real_public})
    auth = SupabaseJWTAuthenticator(repository=repo, issuer=ISSUER, jwks=jwks)

    with pytest.raises(AuthError):
        auth.authenticate(f"Bearer {sign(attacker_private, 'key-1')}")


def test_a_token_naming_an_unknown_key_is_refused(repo, founder_id):
    private_pem, public_pem = keypair()
    jwks = FakeJWKS({"key-1": public_pem})
    auth = SupabaseJWTAuthenticator(repository=repo, issuer=ISSUER, jwks=jwks)

    with pytest.raises(AuthError):
        auth.authenticate(f"Bearer {sign(private_pem, 'no-such-kid')}")


def test_a_token_with_no_kid_is_refused(repo, founder_id):
    """Every Supabase asymmetric token carries one. A token without is not ours."""
    private_pem, public_pem = keypair()
    jwks = FakeJWKS({"key-1": public_pem})
    auth = SupabaseJWTAuthenticator(repository=repo, issuer=ISSUER, jwks=jwks)
    no_kid = jwt.encode(
        {"sub": USER_ID, "aud": "authenticated", "iss": ISSUER,
         "exp": int(time.time()) + 3600},
        private_pem,
        algorithm="ES256",
    )

    with pytest.raises(AuthError):
        auth.authenticate(f"Bearer {no_kid}")


def test_an_expired_token_is_still_refused_on_the_jwks_path(repo, founder_id):
    """Key resolution must not become a way around claim checking."""
    private_pem, public_pem = keypair()
    jwks = FakeJWKS({"key-1": public_pem})
    auth = SupabaseJWTAuthenticator(repository=repo, issuer=ISSUER, jwks=jwks)

    with pytest.raises(AuthError):
        auth.authenticate(
            f"Bearer {sign(private_pem, 'key-1', exp=int(time.time()) - 3600)}"
        )


def test_another_projects_issuer_is_still_refused_on_the_jwks_path(repo, founder_id):
    private_pem, public_pem = keypair()
    jwks = FakeJWKS({"key-1": public_pem})
    auth = SupabaseJWTAuthenticator(repository=repo, issuer=ISSUER, jwks=jwks)

    with pytest.raises(AuthError):
        auth.authenticate(
            f"Bearer {sign(private_pem, 'key-1', iss='https://other.supabase.co/auth/v1')}"
        )


# ── Bounding the outbound traffic ────────────────────────────────────────────


def test_an_unknown_kid_does_not_refetch_on_every_attempt(repo):
    """A garbage `kid` must not turn into an outbound request each time.

    `/api/...` is reachable by anyone with a session, and before the login
    landed it was reachable by anyone at all. A verifier that refetches on
    every unrecognised `kid` is a way to point this deployment's outbound
    traffic at Supabase, one forged token per request.
    """
    _, public_pem = keypair()
    jwks = FakeJWKS({"key-1": public_pem})
    auth = SupabaseJWTAuthenticator(
        repository=repo, issuer=ISSUER, jwks=jwks, unknown_kid_cooldown_s=60
    )
    private_pem, _ = keypair()

    for _ in range(25):
        with pytest.raises(AuthError):
            auth.authenticate(f"Bearer {sign(private_pem, 'garbage-kid')}")

    assert jwks.lookups <= 2, (
        f"{jwks.lookups} lookups for 25 forged tokens — an unknown kid is "
        "being refetched per request"
    )


def test_the_cooldown_does_not_delay_a_real_rotation(repo, founder_id):
    """Bounding the refetch must not mean missing a genuine new key.

    The cooldown applies to a `kid` that has already been looked up and found
    missing, not to one never seen before — so a rotation is picked up at once
    while a flood of forged ids is not.
    """
    private_pem, public_pem = keypair()
    jwks = FakeJWKS({"key-1": public_pem})
    auth = SupabaseJWTAuthenticator(
        repository=repo, issuer=ISSUER, jwks=jwks, unknown_kid_cooldown_s=300
    )
    with pytest.raises(AuthError):
        auth.authenticate(f"Bearer {sign(private_pem, 'garbage')}")

    rotated_private, rotated_public = keypair()
    jwks.keys["rotated"] = rotated_public

    assert auth.authenticate(f"Bearer {sign(rotated_private, 'rotated')}").owns(
        founder_id
    )


# ── Configuration ────────────────────────────────────────────────────────────


def test_an_issuer_alone_is_enough_to_configure_it(repo):
    """The point of the change: no key material in the deployment's config.

    Constructed without touching the network — the JWKS client is built, not
    called, until a token needs verifying.
    """
    auth = SupabaseJWTAuthenticator(repository=repo, issuer=ISSUER)

    assert auth.jwks is not None


def test_the_jwks_url_is_derived_from_the_issuer(repo):
    auth = SupabaseJWTAuthenticator(repository=repo, issuer=ISSUER)

    assert auth.jwks_url == f"{ISSUER}/.well-known/jwks.json"


def test_a_trailing_slash_on_the_issuer_does_not_double_up(repo):
    """A pasted URL often carries one. It must not produce `//.well-known`."""
    auth = SupabaseJWTAuthenticator(repository=repo, issuer=f"{ISSUER}/")

    assert auth.jwks_url == f"{ISSUER}/.well-known/jwks.json"
