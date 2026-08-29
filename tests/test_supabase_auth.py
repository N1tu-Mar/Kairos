"""Verifying a Supabase session token, and refusing everything else.

This is the adapter the `Authenticator` protocol was left open for. It does
one thing: turn a signed JWT into a `Principal` whose `founder_ids` come from
`founder_members`, so authorization keeps being a database fact rather than a
claim the token makes about itself.

Most of this file is refusals, because a JWT verifier is one of the few places
where the interesting cases are all attacks. Three in particular are not
obvious and are the reason the verification options are pinned explicitly
rather than left to PyJWT's defaults:

*   **`alg: none`.** A token that declares it needs no signature. Accepting it
    means anyone can mint any identity.
*   **Algorithm confusion.** A deployment configured with an RSA *public* key
    is handed an HS256 token signed *with that public key as the HMAC secret*.
    A verifier that takes the algorithm from the token's own header validates
    it happily. The fix is that the algorithm list comes from configuration,
    never from the token.
*   **A token from somewhere else.** A valid, correctly-signed Supabase JWT
    from a different project is still a valid JWT. `iss` and `aud` are what
    make it not ours, and both have to be checked rather than assumed.
"""

from __future__ import annotations

import time

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from api.auth import AuthError, SupabaseJWTAuthenticator
from api.repository import SqliteRepository, new_founder_id

SECRET = "a-test-jwt-secret-that-is-long-enough-to-be-realistic"
PROJECT_URL = "https://abcdefghijklm.supabase.co"
ISSUER = f"{PROJECT_URL}/auth/v1"
USER_ID = "a3f1c9e2-7b44-4d18-9f2a-1c8e5b0d6a37"


@pytest.fixture
def repo() -> SqliteRepository:
    return SqliteRepository("sqlite:///:memory:")


@pytest.fixture
def founder_id(repo) -> str:
    generated = new_founder_id()
    repo.link_member(USER_ID, generated)
    return generated


@pytest.fixture
def auth(repo) -> SupabaseJWTAuthenticator:
    return SupabaseJWTAuthenticator(
        repository=repo, jwt_secret=SECRET, issuer=ISSUER
    )


def token(**overrides) -> str:
    """A well-formed Supabase access token, before any tampering."""
    claims = {
        "sub": USER_ID,
        "aud": "authenticated",
        "iss": ISSUER,
        "role": "authenticated",
        "exp": int(time.time()) + 3600,
        "iat": int(time.time()),
    }
    claims.update(overrides)
    algorithm = overrides.pop("_alg", "HS256")
    return jwt.encode(claims, SECRET, algorithm=algorithm)


def bearer(value: str) -> str:
    return f"Bearer {value}"


def _hs256_signed_with(secret: bytes, claims: dict) -> str:
    """Build an HS256 JWT using arbitrary bytes as the HMAC secret.

    Hand-rolled because PyJWT will not encode HMAC with a PEM key — a guard on
    the *signing* side, which an attacker simply does not use. The algorithm
    confusion attack needs the token to exist before the verifier can refuse
    it, so it is assembled here from base64url parts.
    """
    import base64
    import hashlib
    import hmac
    import json as _json

    def b64(raw: bytes) -> bytes:
        return base64.urlsafe_b64encode(raw).rstrip(b"=")

    header = b64(_json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    payload = b64(_json.dumps(claims).encode())
    signing_input = header + b"." + payload
    signature = b64(hmac.new(secret, signing_input, hashlib.sha256).digest())
    return (signing_input + b"." + signature).decode()


# ── The happy path ───────────────────────────────────────────────────────────


def test_a_valid_token_resolves_to_its_memberships(auth, founder_id):
    principal = auth.authenticate(bearer(token()))

    assert principal.founder_ids == frozenset({founder_id})
    assert principal.subject == USER_ID
    assert principal.method == "supabase_jwt"


def test_the_principal_owns_the_founder_it_is_linked_to(auth, founder_id):
    assert auth.authenticate(bearer(token())).owns(founder_id)


def test_a_valid_token_with_no_membership_owns_nothing(auth):
    """Authenticated is not authorized.

    A real person who has signed up and has no founder account yet. They are
    who they say they are and may touch nothing, which every `authorize` call
    turns into a 404.
    """
    principal = auth.authenticate(bearer(token()))

    assert principal.founder_ids == frozenset()
    assert not principal.can_write


def test_membership_is_read_per_request_not_cached(auth, repo):
    """Revocation has to take effect without waiting for a token to expire.

    The token is valid for an hour. If the founder set were read once and
    cached against it, removing someone's access would not take hold until
    then — which is not what "revoked" means.
    """
    granted = new_founder_id()
    repo.link_member(USER_ID, granted)
    issued = bearer(token())
    assert auth.authenticate(issued).owns(granted)

    repo.unlink_member(USER_ID, granted)

    assert not auth.authenticate(issued).owns(granted)


def test_a_read_only_membership_produces_a_read_only_principal(auth, repo):
    repo.link_member(USER_ID, new_founder_id(), can_write=False)

    assert auth.authenticate(bearer(token())).can_write is False


# ── Refusals: the signature ──────────────────────────────────────────────────


def test_a_token_signed_with_the_wrong_secret_is_refused(auth):
    forged = jwt.encode(
        {"sub": USER_ID, "aud": "authenticated", "iss": ISSUER,
         "exp": int(time.time()) + 3600},
        "not-the-secret",
        algorithm="HS256",
    )

    with pytest.raises(AuthError):
        auth.authenticate(bearer(forged))


def test_an_unsigned_token_is_refused(auth):
    """`alg: none` — a token asserting it needs no signature."""
    unsigned = jwt.encode(
        {"sub": USER_ID, "aud": "authenticated", "iss": ISSUER,
         "exp": int(time.time()) + 3600},
        key="",
        algorithm="none",
    )

    with pytest.raises(AuthError):
        auth.authenticate(bearer(unsigned))


def test_algorithm_confusion_is_refused(repo, founder_id):
    """An RSA-configured verifier must not accept an HMAC token.

    The attack: sign HS256 using the deployment's RSA *public* key as the
    shared secret. A verifier that reads the algorithm out of the token's own
    header treats a public value as a secret one and validates it. The
    algorithm list has to come from configuration.
    """
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    rsa_auth = SupabaseJWTAuthenticator(
        repository=repo, public_key=public_pem.decode(), issuer=ISSUER
    )
    # Assembled by hand. PyJWT refuses to *encode* HMAC with a PEM key, which
    # is a guard on the signing side — an attacker is not using PyJWT to build
    # this. What is under test is whether our verifier accepts it.
    forged = _hs256_signed_with(
        public_pem,
        {"sub": USER_ID, "aud": "authenticated", "iss": ISSUER,
         "exp": int(time.time()) + 3600},
    )

    with pytest.raises(AuthError):
        rsa_auth.authenticate(bearer(forged))


def test_a_properly_signed_rsa_token_is_accepted(repo, founder_id):
    """The asymmetric path has to actually work, not merely refuse things."""
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    public_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    rsa_auth = SupabaseJWTAuthenticator(
        repository=repo, public_key=public_pem, issuer=ISSUER
    )
    signed = jwt.encode(
        {"sub": USER_ID, "aud": "authenticated", "iss": ISSUER,
         "exp": int(time.time()) + 3600},
        private_pem,
        algorithm="RS256",
    )

    assert rsa_auth.authenticate(bearer(signed)).owns(founder_id)


# ── Refusals: the claims ─────────────────────────────────────────────────────


def test_an_expired_token_is_refused(auth, founder_id):
    # An hour stale, not a second: the verifier allows a small leeway for
    # clock skew, so `now - 1` is still valid and would test nothing.
    with pytest.raises(AuthError):
        auth.authenticate(bearer(token(exp=int(time.time()) - 3600)))


def test_a_token_for_another_project_is_refused(auth, founder_id):
    """Correctly signed, genuinely Supabase, and not ours."""
    with pytest.raises(AuthError):
        auth.authenticate(bearer(token(iss="https://someoneelse.supabase.co/auth/v1")))


def test_a_token_with_the_wrong_audience_is_refused(auth, founder_id):
    """`aud` separates a user session from a service-role token."""
    with pytest.raises(AuthError):
        auth.authenticate(bearer(token(aud="service_role")))


def test_a_token_with_no_subject_is_refused(auth):
    """No `sub` is no identity, whatever else the token says."""
    with pytest.raises(AuthError):
        auth.authenticate(bearer(token(sub="")))


# ── Refusals: the header ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "header",
    [None, "", "Basic abc", "Bearer", "Bearer    ", "token abc", "abc"],
)
def test_a_malformed_authorization_header_is_refused(auth, header):
    with pytest.raises(AuthError):
        auth.authenticate(header)


def test_garbage_in_place_of_a_token_is_refused(auth):
    with pytest.raises(AuthError):
        auth.authenticate(bearer("not-a-jwt-at-all"))


# ── Configuration ────────────────────────────────────────────────────────────


def test_configuring_neither_key_selects_the_jwks_path(repo):
    """Neither key plus an issuer is the *default*, not an error.

    This assertion used to be `pytest.raises(ValueError)`, from when the only
    asymmetric option was a static PEM someone pasted in. Supabase publishes
    its signing keys and rotates them, so an issuer alone is now the complete
    configuration and the keys are fetched — see `test_supabase_jwks.py`.
    """
    auth = SupabaseJWTAuthenticator(repository=repo, issuer=ISSUER)

    assert auth.jwks is not None


def test_configuring_no_issuer_and_no_key_is_refused_at_construction(repo):
    """With neither a key nor somewhere to fetch one, it cannot verify anything.

    Failing here rather than at the first request means a deployment missing
    its configuration does not start and then refuse everything — it does not
    start.
    """
    with pytest.raises(ValueError):
        SupabaseJWTAuthenticator(repository=repo, issuer="")


def test_supplying_both_key_kinds_is_refused(repo):
    """Which algorithm family is trusted must never be ambiguous.

    That ambiguity is exactly what an algorithm-confusion attack needs.
    """
    with pytest.raises(ValueError):
        SupabaseJWTAuthenticator(
            repository=repo, issuer=ISSUER, jwt_secret=SECRET, public_key="pem"
        )
