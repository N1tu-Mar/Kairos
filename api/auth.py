"""Identity, authorization, and the audit trail of who did what.

One shared bearer token was correct for exactly one founder. It is not an
identity: it says *someone* holds the credential, never *which* founder, so
every founder-scoped path was really honour-scoped. Guessing another
founder's id was enough.

This module introduces the seam that fixes that, without pretending an
identity provider is wired up that is not.

*   **`Principal`** — who is making this request. `founder_ids` is the
    closed set they may touch. `can_write` separates a read-only credential
    from one that may trigger runs and edit profiles.
*   **`Authenticator`** — a Protocol with one method. Two implementations
    ship: `SharedTokenAuthenticator` (the documented local single-founder
    mode) and `StaticTokenFileAuthenticator` (multi-founder, tokens hashed
    at rest, rotation and revocation without a restart). A third —
    an OIDC/JWT adapter for a real identity provider — is a product
    decision, so the interface is here and the adapter is documented rather
    than faked.
*   **`authorize`** — the check every founder-scoped endpoint runs. It
    returns 404, never 403, for a founder the principal does not own: a 403
    confirms the id exists, which is how an attacker enumerates founders.

Nothing here logs a token, a founder's knowledge base, or a model prompt.
Audit events record the actor, the action, the resource id, and nothing
about the contents.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

import jwt

log = logging.getLogger("kairos.auth")
audit_log = logging.getLogger("kairos.audit")

#: The founder id the single-founder demo mode grants access to. Matches the
#: profile `api/main.py` seeds from `data/demo_founder.json`.
DEMO_FOUNDER_ID = "founder_demo"

# ── Scopes ───────────────────────────────────────────────────────────────────
#
# A shared token used to mean "this holder may do anything the founder can".
# EventBridge needs a credential that can start a scheduled run and *only*
# that — otherwise a leaked scheduler secret is a full founder session.
# Scopes are the closed set that makes that distinction checkable per
# endpoint rather than by reading `method` strings at every call site.

SCOPE_FOUNDER_READ = "founder:read"
SCOPE_FOUNDER_WRITE = "founder:write"
SCOPE_RUN_TRIGGER = "run:trigger"
SCOPE_RUN_CANCEL = "run:cancel"
SCOPE_INBOX_WRITE = "inbox:write"
SCOPE_ELIGIBILITY_ANSWER = "eligibility:answer"

#: Everything a signed-in founder (or the local shared token) may do.
USER_SCOPES = frozenset(
    {
        SCOPE_FOUNDER_READ,
        SCOPE_FOUNDER_WRITE,
        SCOPE_RUN_TRIGGER,
        SCOPE_RUN_CANCEL,
        SCOPE_INBOX_WRITE,
        SCOPE_ELIGIBILITY_ANSWER,
    }
)

#: EventBridge may create a scheduled run and nothing else.
SCHEDULER_SCOPES = frozenset({SCOPE_RUN_TRIGGER})


def scopes_for_user(*, can_write: bool) -> frozenset[str]:
    """Read-only memberships keep `founder:read` and lose every write scope."""
    if can_write:
        return USER_SCOPES
    return frozenset({SCOPE_FOUNDER_READ})


class AuthError(Exception):
    """Authentication failed. Never says which part of the credential was wrong."""


@dataclass(frozen=True)
class Principal:
    """Who is making this request, and what they may touch.

    `founder_ids` is a closed set, not a filter applied later. An empty set
    is a principal that may read nothing — a valid state for a revoked
    credential, and safer than the alternative interpretation.

    `scopes` is the closed set of actions this principal may perform even
    on a founder they own. A scheduler principal owns one founder and still
    cannot read the profile: ownership without the matching scope is a 404,
    same as a stranger.
    """

    subject: str
    founder_ids: frozenset[str]
    can_write: bool = True
    #: How the principal was established. Recorded on audit events so
    #: "the shared demo token did this" is distinguishable from a real user.
    method: str = "unknown"
    scopes: frozenset[str] = field(default_factory=lambda: frozenset(USER_SCOPES))

    def owns(self, founder_id: str) -> bool:
        """Whether this principal may touch `founder_id`.

        Membership in a closed set, not a pattern or a prefix match. A principal
        with an empty `founder_ids` owns nothing and every check fails — which is
        the intended reading of a revoked credential, not a bug.
        """
        return founder_id in self.founder_ids

    def has_scope(self, scope: str) -> bool:
        """Whether this principal may perform `scope`, regardless of ownership."""
        return scope in self.scopes

    @property
    def is_scheduler(self) -> bool:
        """EventBridge's service principal. Narrow on purpose — see SCHEDULER_SCOPES."""
        return self.method == "scheduler_token"


#: The principal for a deployment running with no credential configured at
#: all — the localhost demo. Named so it is obvious in an audit log.
ANONYMOUS_LOCAL = Principal(
    subject="anonymous-local",
    founder_ids=frozenset({DEMO_FOUNDER_ID}),
    can_write=True,
    method="open",
    scopes=USER_SCOPES,
)


class Authenticator(Protocol):
    """One method: turn a raw `Authorization` header into a `Principal`.

    Every implementation must fail closed — raise `AuthError` rather than
    return a principal with an empty set — because callers only catch the
    exception. Returning a permissive principal on an unreadable credential
    store would be silently authorizing.
    """

    def authenticate(self, authorization: str | None) -> Principal:
        """Resolve a credential to a principal, or raise `AuthError`."""
        ...


def _bearer(authorization: str | None) -> str:
    """Pull the token out of an `Authorization: Bearer <token>` header.

    Raises `AuthError` for missing, non-bearer and empty-value headers alike.
    The message distinguishes missing from malformed for the operator's logs;
    neither reaches the client, which sees one generic 401.
    """
    if not authorization:
        raise AuthError("missing credential")
    scheme, _, value = authorization.partition(" ")
    if scheme.lower() != "bearer" or not value.strip():
        raise AuthError("malformed credential")
    return value.strip()


class SharedTokenAuthenticator:
    """The documented local single-founder mode.

    One token, one founder. Every holder of the token is the same principal,
    which is honest about what a shared secret can prove.

    An empty token means the API would serve every request as
    `ANONYMOUS_LOCAL` — a principal with write access and no credential
    behind it. That is a real mode, useful on a laptop, and it is reachable
    only when someone asked for it: `allow_open` must be True, and it comes
    from a variable that exists for nothing else.

    It used to be the *fallback* instead, refused only when `KAIROS_ENV` also
    read `production`. Two variables had to be right for the API to be shut,
    and the one that mattered was set by the Terraform rather than by the
    code — so ECS was covered and every other way of running this was not.
    A missing variable now fails closed on any host.
    """

    def __init__(
        self,
        token: str,
        founder_id: str = DEMO_FOUNDER_ID,
        *,
        allow_open: bool = False,
        environment: str = "",
    ) -> None:
        """`token=""` serves open only when `allow_open` is True and the
        environment is not production; otherwise every request is an
        `AuthError`. `environment` is carried for that one check and is
        deliberately not what decides the ordinary case — an authentication
        posture that depends on a deployment-naming string is one a typo can
        widen.
        """
        self.token = token
        self.founder_id = founder_id
        self.allow_open = allow_open
        self.environment = environment

    def authenticate(self, authorization: str | None) -> Principal:
        """Compare the bearer token against the one configured token.

        The comparison is constant-time so the response latency cannot be used to
        recover the token a character at a time. Every holder resolves to the
        same `Principal`, because a shared secret cannot prove more than that.
        """
        if not self.token:
            if not self.allow_open or self.environment == "production":
                # Unconfigured is not a mode. Say nothing about which half is
                # missing — the operator's log has that, the caller does not.
                raise AuthError("no credential is configured")
            return ANONYMOUS_LOCAL
        supplied = _bearer(authorization)
        if not secrets.compare_digest(supplied.encode(), self.token.encode()):
            raise AuthError("invalid credential")
        return Principal(
            subject="shared-token",
            founder_ids=frozenset({self.founder_id}),
            can_write=True,
            method="shared_token",
            scopes=USER_SCOPES,
        )


def hash_token(token: str) -> str:
    """SHA-256 of a token, for storage. Never store the token itself.

    A credential file that leaks should not hand over working credentials.
    Comparison is constant-time against the hash, so a timing signal cannot
    be used to recover one either.
    """
    return hashlib.sha256(token.encode()).hexdigest()


@dataclass
class Credential:
    """One issued credential. The token itself is never held here.

    Revocation is a field rather than a deletion so a revoked credential
    stays auditable — "this token was used, and it had been revoked" is a
    thing you want to be able to see.
    """

    credential_id: str
    token_hash: str
    subject: str
    founder_ids: frozenset[str]
    can_write: bool = True
    revoked: bool = False
    #: Unix seconds. None never expires — acceptable for a service
    #: credential, not for a person's.
    expires_at: float | None = None

    def valid_at(self, now: float) -> bool:
        """Whether this credential is usable at `now` (unix seconds).

        Revoked always loses. `expires_at is None` never expires — deliberate for
        service credentials, and the reason the field is documented as
        inappropriate for a person's token.
        """
        if self.revoked:
            return False
        return self.expires_at is None or self.expires_at > now


class StaticTokenFileAuthenticator:
    """Multi-founder credentials from a JSON file, hashed at rest.

    The file is re-read when its mtime changes, so rotating or revoking a
    credential takes effect without a restart and without a deploy. It is
    read from disk on purpose: it belongs on the same mounted volume as the
    rest of the deployment's state, injected from a secret store, and it is
    never committed.

    Format — note that `token_hash` is a SHA-256 hex digest, never a token:

        {
          "credentials": [
            {
              "credential_id": "founder-a-cli",
              "token_hash": "9f86d0…",
              "subject": "founder_a",
              "founder_ids": ["founder_a"],
              "can_write": true,
              "revoked": false,
              "expires_at": null
            }
          ]
        }

    This is deliberately not an identity provider. It has no login, no
    refresh, and no per-request revocation check against a remote. It is the
    smallest thing that gives real per-founder authorization, and the
    `Authenticator` protocol is the seam a real provider replaces it at —
    see the OIDC note in `infra/README.md`.
    """

    def __init__(self, path: Path | str) -> None:
        """Nothing is read here. The first `authenticate` triggers the initial load, so constructing this against a missing file is not an error — it becomes a fail-closed empty credential set at request time."""
        self.path = Path(path)
        self._credentials: dict[str, Credential] = {}
        self._mtime: float | None = None

    def _reload_if_changed(self) -> None:
        """Re-read the credential file when its mtime has moved.

        Every failure path here empties `_credentials`, so an unreadable or
        corrupt file denies everyone rather than keeping the last-known-good set.
        That is the safe direction, and it is also a self-inflicted outage worth
        knowing about before editing the file in production.

        Detection is mtime-only. A write that lands within the same filesystem
        mtime granularity as the previous one is not noticed — on a filesystem
        with one-second timestamps, two rotations in the same second leave the
        second one unloaded until something else touches the file.
        """
        try:
            mtime = self.path.stat().st_mtime
        except OSError:
            # A missing or unreadable file means no credentials, which means
            # every request fails closed. It never means "allow everyone".
            self._credentials = {}
            self._mtime = None
            return
        if self._mtime == mtime:
            return
        try:
            raw = json.loads(self.path.read_text())
        except (json.JSONDecodeError, OSError) as exc:
            log.error("credential_file_unreadable", extra={"error": str(exc)})
            self._credentials = {}
            self._mtime = mtime
            return

        loaded: dict[str, Credential] = {}
        for entry in raw.get("credentials", []):
            try:
                credential = Credential(
                    credential_id=str(entry["credential_id"]),
                    token_hash=str(entry["token_hash"]),
                    subject=str(entry["subject"]),
                    founder_ids=frozenset(entry.get("founder_ids", [])),
                    can_write=bool(entry.get("can_write", True)),
                    revoked=bool(entry.get("revoked", False)),
                    expires_at=(
                        float(entry["expires_at"])
                        if entry.get("expires_at") is not None
                        else None
                    ),
                )
            except (KeyError, TypeError, ValueError):
                # One malformed entry must not silently widen or narrow the
                # rest of the file. Skip it and say so.
                log.error("credential_entry_malformed")
                continue
            loaded[credential.token_hash] = credential

        self._credentials = loaded
        self._mtime = mtime

    def authenticate(self, authorization: str | None) -> Principal:
        """Resolve a bearer token to a principal via its SHA-256 digest.

        The file is re-checked on every call, so revocation takes effect on the
        next request rather than on the next restart.
        """
        self._reload_if_changed()
        supplied = _bearer(authorization)
        digest = hash_token(supplied)

        # Constant-time comparison against every candidate: a dict lookup on
        # the digest is already constant-time with respect to the token, but
        # comparing the stored hash explicitly keeps that true if the lookup
        # is ever replaced with a scan.
        credential = self._credentials.get(digest)
        if credential is None or not hmac.compare_digest(
            credential.token_hash, digest
        ):
            raise AuthError("invalid credential")
        if not credential.valid_at(time.time()):
            # Revoked and expired are the same answer to the caller. Which
            # one it was goes to the audit log, not to the response.
            audit_event(
                actor=credential.subject,
                action="auth.rejected",
                resource=credential.credential_id,
                outcome="revoked" if credential.revoked else "expired",
            )
            raise AuthError("invalid credential")

        return Principal(
            subject=credential.subject,
            founder_ids=credential.founder_ids,
            can_write=credential.can_write,
            method="token_file",
            scopes=scopes_for_user(can_write=credential.can_write),
        )


class _RemoteJWKS:
    """Supabase's published signing keys, fetched by `kid` and cached.

    A thin wrapper over `jwt.PyJWKClient` for one reason: it gives the
    authenticator a one-method dependency, so a test injects a fake instead
    of reaching the network, and the object under test is the same shape as
    the one that runs.

    `PyJWKClient` does the caching and the refetch-on-unknown-kid. The cache
    is per instance, so it lives as long as the authenticator does — which is
    the process, since the app builds one at startup.
    """

    def __init__(self, url: str) -> None:
        self.url = url
        self._client = None

    def get_public_key(self, kid: str):
        """The signing key for `kid`. Raises if the auth server has no such key."""
        if self._client is None:
            # Constructed lazily so a deployment starts even when the auth
            # server is unreachable, and so no network call happens at import.
            self._client = jwt.PyJWKClient(self.url, cache_keys=True)
        return self._client.get_signing_key(kid).key


class SupabaseJWTAuthenticator:
    """Verify a Supabase session token; read authorization from the database.

    The adapter the `Authenticator` protocol was left open for. It answers
    *who* — the `sub` claim of a signed token — and then asks
    `founder_members` *what they may touch*. The token never says which
    founders it owns, and that is the point: a claim inside a token is
    something the token's holder can be given by mistake, while a membership
    row is something an operator granted.

    Membership is read on every request rather than cached against the token.
    A Supabase access token lives about an hour; caching would mean a revoked
    person kept their access until it expired, which is not what revocation
    means.

    **Key material.** Exactly one of `jwt_secret` (HS256, Supabase's shared
    JWT secret) or `public_key` (RS256/ES256, the asymmetric signing keys)
    must be supplied, and whichever it is fixes the accepted algorithm list.
    That list comes from configuration and never from the token's own header,
    which is what makes algorithm confusion — an HS256 token signed with the
    deployment's RSA *public* key as its HMAC secret — a refusal instead of a
    valid session. `alg: none` is unreachable for the same reason.

    `iss` and `aud` are both verified. A correctly signed token from another
    Supabase project is still a correctly signed token; the issuer is what
    makes it not ours, and the audience separates a user session from a
    service-role key.
    """

    #: The `aud` claim Supabase puts on a signed-in user's access token. A
    #: `service_role` token is not a person and must never resolve to one.
    AUDIENCE = "authenticated"

    #: Algorithms accepted on the asymmetric path. ES256 is what Supabase
    #: gives a new project; RS256 covers older ones and other providers.
    ASYMMETRIC_ALGORITHMS = ["RS256", "ES256"]

    def __init__(
        self,
        *,
        repository,
        issuer: str,
        jwt_secret: str = "",
        public_key: str = "",
        jwks=None,
        leeway_s: int = 10,
        unknown_kid_cooldown_s: float = 300.0,
    ) -> None:
        """Pick a way to obtain the signing key. Exactly one, never two.

        In order of preference:

        *   **JWKS** (the default, and what an issuer alone gives you) —
            public keys fetched from the auth server and selected by the
            token's `kid`. The only option that survives a rotation without a
            human pasting a new key into a secret store.
        *   **`public_key`** — one static PEM. Works offline; goes stale the
            moment Supabase rotates.
        *   **`jwt_secret`** — the legacy shared HS256 secret. Supported
            because older projects still have one; Supabase itself recommends
            against it, since verifying means holding a value that can also
            *mint* tokens.

        `leeway_s` tolerates small clock skew on `exp`/`iat`, nothing else.
        `unknown_kid_cooldown_s` bounds refetching — see `_resolve_key`.
        """
        if jwt_secret and public_key:
            # Which algorithm family is trusted would be ambiguous, and that
            # ambiguity is precisely what algorithm confusion exploits.
            raise ValueError("supply either jwt_secret or public_key, not both")

        self.repository = repository
        self.issuer = issuer.rstrip("/")
        self.leeway_s = leeway_s
        self.unknown_kid_cooldown_s = unknown_kid_cooldown_s
        self.jwt_secret = jwt_secret
        self.public_key = public_key
        #: kid -> when it was last looked up and found missing.
        self._missing_kids: dict[str, float] = {}

        if jwt_secret:
            self.algorithms = ["HS256"]
            self.jwks = None
            self.jwks_url = ""
        elif public_key:
            self.algorithms = list(self.ASYMMETRIC_ALGORITHMS)
            self.jwks = None
            self.jwks_url = ""
        else:
            if not self.issuer:
                raise ValueError(
                    "an issuer is required to discover signing keys"
                )
            self.algorithms = list(self.ASYMMETRIC_ALGORITHMS)
            self.jwks_url = f"{self.issuer}/.well-known/jwks.json"
            # Built, not called: construction must not touch the network, so
            # a deployment starts even while the auth server is unreachable.
            self.jwks = jwks if jwks is not None else _RemoteJWKS(self.jwks_url)

    def _resolve_key(self, token: str) -> str:
        """The key this token says it was signed with.

        A `kid` that has already been looked up and found missing is refused
        without another fetch until `unknown_kid_cooldown_s` has passed. That
        bound is the point: without it, a stream of tokens carrying random
        `kid` values becomes a stream of outbound requests, which turns this
        deployment into an amplifier aimed at the auth server. A `kid` never
        seen before is always fetched, so a genuine rotation is picked up at
        once.
        """
        if self.jwks is None:
            return self.jwt_secret or self.public_key

        try:
            kid = jwt.get_unverified_header(token).get("kid")
        except jwt.InvalidTokenError:
            raise AuthError("invalid credential") from None
        if not kid:
            # Every Supabase asymmetric token carries one.
            raise AuthError("invalid credential")

        missed_at = self._missing_kids.get(kid)
        if missed_at is not None and time.time() - missed_at < self.unknown_kid_cooldown_s:
            raise AuthError("invalid credential")

        try:
            return self.jwks.get_public_key(kid)
        except Exception:  # noqa: BLE001 — a lookup failure is a refusal
            self._missing_kids[kid] = time.time()
            raise AuthError("invalid credential") from None

    def authenticate(self, authorization: str | None) -> Principal:
        """Resolve a bearer token to a principal, or raise `AuthError`.

        Every failure is the same `AuthError`. Which claim was wrong, whether
        the signature failed or the token merely expired, and whether the
        subject has any membership at all are distinctions the caller does not
        get — they are a probe oracle. They go to the audit log instead.
        """
        supplied = _bearer(authorization)
        key = self._resolve_key(supplied)

        try:
            claims = jwt.decode(
                supplied,
                key,
                algorithms=self.algorithms,
                audience=self.AUDIENCE,
                issuer=self.issuer,
                leeway=self.leeway_s,
                options={"require": ["exp", "sub", "aud", "iss"]},
            )
        except jwt.InvalidTokenError as exc:
            # Expiry, bad signature, wrong issuer, wrong audience, a missing
            # required claim, and anything that is not a JWT at all.
            audit_event(
                actor="unknown",
                action="auth.rejected",
                resource="supabase_jwt",
                outcome=type(exc).__name__,
            )
            raise AuthError("invalid credential") from None

        subject = str(claims.get("sub") or "").strip()
        if not subject:
            raise AuthError("invalid credential")

        # Authorization is a database fact, re-read per request so revocation
        # does not wait for the token to expire.
        can_write = self.repository.can_write(subject)
        return Principal(
            subject=subject,
            founder_ids=self.repository.founder_ids_for(subject),
            can_write=can_write,
            method="supabase_jwt",
            scopes=scopes_for_user(can_write=can_write),
        )


class SchedulerTokenAuthenticator:
    """EventBridge's service credential. Matches or declines, never falls through.

    Distinct from `SharedTokenAuthenticator` on purpose: that principal is a
    founder. This one may start a scheduled run for one configured founder
    and is refused by every other endpoint through `SCHEDULER_SCOPES`.

    `try_authenticate` returns `None` when the header is not this secret, so
    a user JWT is not rejected just because a scheduler token is also
    configured. A match is constant-time.
    """

    def __init__(self, token: str, founder_id: str = DEMO_FOUNDER_ID) -> None:
        self.token = token
        self.founder_id = founder_id

    def try_authenticate(self, authorization: str | None) -> Principal | None:
        """Return the scheduler principal, or None if this is not that secret."""
        if not self.token:
            return None
        try:
            supplied = _bearer(authorization)
        except AuthError:
            return None
        if not secrets.compare_digest(supplied.encode(), self.token.encode()):
            return None
        return Principal(
            subject="scheduler",
            founder_ids=frozenset({self.founder_id}),
            can_write=True,
            method="scheduler_token",
            scopes=SCHEDULER_SCOPES,
        )

    def authenticate(self, authorization: str | None) -> Principal:
        """Protocol adapter: match or raise, for tests that use this alone."""
        principal = self.try_authenticate(authorization)
        if principal is None:
            raise AuthError("invalid credential")
        return principal


class GateAuthenticator:
    """Scheduler secret first (if configured), then the human authenticator.

    Trying the scheduler token with `compare_digest` and skipping on mismatch
    keeps a user JWT from being treated as a failed scheduler login. The
    human authenticator then does its own verification.
    """

    def __init__(
        self,
        user: Authenticator,
        scheduler: SchedulerTokenAuthenticator | None = None,
    ) -> None:
        self.user = user
        self.scheduler = scheduler

    def authenticate(self, authorization: str | None) -> Principal:
        """Resolve a credential, preferring the scheduler secret when it matches."""
        if self.scheduler is not None:
            principal = self.scheduler.try_authenticate(authorization)
            if principal is not None:
                return principal
        return self.user.authenticate(authorization)


def build_authenticator(config, repository=None) -> Authenticator:
    """Pick an authenticator from configuration, strongest identity first.

    `KAIROS_AUTH_MODE` chooses the human identity story:

    1.  **`supabase`** — user JWTs verified through JWKS (or a static key).
        Authorization still comes from `founder_members`, never from a
        claim inside the token.
    2.  **`local_shared`** — credential file if set, otherwise the shared
        token (or the explicit open-API opt-in).

    A configured `KAIROS_SCHEDULER_TOKEN` is wrapped in front of whichever
    of those is chosen, so EventBridge does not need a user session.

    Configured-but-unusable is a startup failure, not a silent demotion: a
    deployment that set `auth_mode=supabase` and no issuer has made a
    mistake, and falling back to a shared token would hide it.
    """
    if getattr(config, "auth_mode", "local_shared") == "supabase":
        if not config.supabase_issuer:
            raise ValueError(
                "KAIROS_AUTH_MODE=supabase requires KAIROS_SUPABASE_ISSUER"
            )
        if repository is None:
            raise ValueError(
                "Supabase authentication needs a repository to read memberships from"
            )
        user: Authenticator = SupabaseJWTAuthenticator(
            repository=repository,
            issuer=config.supabase_issuer,
            jwt_secret=config.supabase_jwt_secret,
            public_key=config.supabase_public_key,
        )
    elif config.supabase_issuer:
        # Issuer set without supabase mode: still honour it, so an operator
        # who configured JWTs but forgot the mode flag is not silently
        # running on a shared token. Production boot still requires the
        # mode flag via `validate_runtime_posture`.
        if repository is None:
            raise ValueError(
                "Supabase authentication needs a repository to read memberships from"
            )
        user = SupabaseJWTAuthenticator(
            repository=repository,
            issuer=config.supabase_issuer,
            jwt_secret=config.supabase_jwt_secret,
            public_key=config.supabase_public_key,
        )
    elif config.credentials_file:
        user = StaticTokenFileAuthenticator(config.credentials_file)
    else:
        user = SharedTokenAuthenticator(
            config.api_token,
            allow_open=config.allow_open_api,
            environment=config.environment,
        )

    scheduler_token = getattr(config, "scheduler_token", "") or ""
    if not scheduler_token:
        return user
    return GateAuthenticator(
        user,
        SchedulerTokenAuthenticator(
            scheduler_token,
            getattr(config, "scheduler_founder_id", "") or DEMO_FOUNDER_ID,
        ),
    )


# ── Authorization ────────────────────────────────────────────────────────────


class Forbidden(Exception):
    """The principal may not touch this resource.

    Callers translate this to **404**, not 403. A 403 on a founder id
    confirms that the id exists, which turns id-guessing into founder
    enumeration. Not-found and not-yours are indistinguishable from outside.
    """


def authorize(
    principal: Principal,
    founder_id: str,
    *,
    write: bool = False,
    scope: str | None = None,
) -> None:
    """Ownership and scope check. Every founder-scoped path calls this.

    `scope` defaults to `founder:write` on writes and `founder:read`
    otherwise, so a call site that forgets to name a narrower action is
    still not an open write for a scheduler principal. Named scopes
    (`run:trigger`, `inbox:write`, …) override that default.
    """
    needed = scope or (SCOPE_FOUNDER_WRITE if write else SCOPE_FOUNDER_READ)
    if not principal.owns(founder_id):
        raise Forbidden(f"{principal.subject} may not access {founder_id}")
    if write and not principal.can_write:
        raise Forbidden(f"{principal.subject} holds a read-only credential")
    if not principal.has_scope(needed):
        raise Forbidden(f"{principal.subject} lacks scope {needed}")


# ── Audit ────────────────────────────────────────────────────────────────────

#: Actions worth a security audit event: anything that changes state a
#: founder would notice, plus rejected authentication.
#:
#: Documentation only — `audit_event` does not check its `action` against
#: this set, and nothing else imports it. A call site that passes an action
#: not listed here is still recorded, and adding an audited action without
#: adding it here goes unnoticed. If that guarantee is wanted, it has to be
#: an assertion in `audit_event`, not this constant.
AUDITED_ACTIONS = frozenset(
    {
        "profile.write",
        "eligibility.answer",
        "run.trigger",
        "run.cancel",
        "inbox.state_change",
        "auth.rejected",
        "rate_limit.rejected",
    }
)


def audit_event(
    *,
    actor: str,
    action: str,
    resource: str,
    outcome: str = "ok",
    **extra: object,
) -> None:
    """Record one security-relevant action.

    Deliberately narrow. It records *that* a profile was replaced, never the
    profile; *that* a run was triggered, never the prompt. Nothing passed in
    `extra` should be founder content — the call sites pass ids and counts.
    """
    audit_log.info(
        "audit",
        extra={
            "actor": actor,
            "action": action,
            "resource": resource,
            "outcome": outcome,
            **extra,
        },
    )


@dataclass
class InMemoryAuditSink:
    """Captures audit events, for tests and for a local `/audit` view.

    Production sends these to CloudWatch through the structured logger; this
    exists so a test can assert an event was emitted without parsing logs.

    Currently unreferenced: `audit_event` writes to the `kairos.audit`
    logger directly and nothing wires a sink in front of it, so this class
    is a seam that was never connected rather than one in use.
    """

    events: list[dict] = field(default_factory=list)

    def record(self, **event: object) -> None:
        """Append one event. No filtering, no redaction — callers pass ids and counts only."""
        self.events.append(dict(event))
