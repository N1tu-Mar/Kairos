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

log = logging.getLogger("kairos.auth")
audit_log = logging.getLogger("kairos.audit")

#: The founder id the single-founder demo mode grants access to. Matches the
#: profile `api/main.py` seeds from `data/demo_founder.json`.
DEMO_FOUNDER_ID = "founder_demo"


class AuthError(Exception):
    """Authentication failed. Never says which part of the credential was wrong."""


@dataclass(frozen=True)
class Principal:
    """Who is making this request, and what they may touch.

    `founder_ids` is a closed set, not a filter applied later. An empty set
    is a principal that may read nothing — a valid state for a revoked
    credential, and safer than the alternative interpretation.
    """

    subject: str
    founder_ids: frozenset[str]
    can_write: bool = True
    #: How the principal was established. Recorded on audit events so
    #: "the shared demo token did this" is distinguishable from a real user.
    method: str = "unknown"

    def owns(self, founder_id: str) -> bool:
        return founder_id in self.founder_ids


#: The principal for a deployment running with no credential configured at
#: all — the localhost demo. Named so it is obvious in an audit log.
ANONYMOUS_LOCAL = Principal(
    subject="anonymous-local",
    founder_ids=frozenset({DEMO_FOUNDER_ID}),
    can_write=True,
    method="open",
)


class Authenticator(Protocol):
    def authenticate(self, authorization: str | None) -> Principal:
        """Resolve a credential to a principal, or raise `AuthError`."""
        ...


def _bearer(authorization: str | None) -> str:
    if not authorization:
        raise AuthError("missing credential")
    scheme, _, value = authorization.partition(" ")
    if scheme.lower() != "bearer" or not value.strip():
        raise AuthError("malformed credential")
    return value.strip()


class SharedTokenAuthenticator:
    """The documented local single-founder mode.

    One token, one founder. Every holder of the token is the same principal,
    which is honest about what a shared secret can prove. When the token is
    empty the API runs open and every request is `ANONYMOUS_LOCAL` — but
    only outside production mode, where an empty token is refused instead.
    """

    def __init__(
        self,
        token: str,
        founder_id: str = DEMO_FOUNDER_ID,
        *,
        production: bool = False,
    ) -> None:
        self.token = token
        self.founder_id = founder_id
        self.production = production

    def authenticate(self, authorization: str | None) -> Principal:
        if not self.token:
            if self.production:
                # An open API in production is a misconfiguration, not a mode.
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
        self.path = Path(path)
        self._credentials: dict[str, Credential] = {}
        self._mtime: float | None = None

    def _reload_if_changed(self) -> None:
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
        )


def build_authenticator(config) -> Authenticator:
    """Pick an authenticator from configuration.

    A credential file, when configured, wins: it is the only one of the two
    that can tell founders apart. Otherwise the shared token, which is the
    documented single-founder mode.
    """
    if config.credentials_file:
        return StaticTokenFileAuthenticator(config.credentials_file)
    return SharedTokenAuthenticator(
        config.api_token, production=config.production
    )


# ── Authorization ────────────────────────────────────────────────────────────


class Forbidden(Exception):
    """The principal may not touch this resource.

    Callers translate this to **404**, not 403. A 403 on a founder id
    confirms that the id exists, which turns id-guessing into founder
    enumeration. Not-found and not-yours are indistinguishable from outside.
    """


def authorize(principal: Principal, founder_id: str, *, write: bool = False) -> None:
    """Ownership check. Every founder-scoped path calls this."""
    if not principal.owns(founder_id):
        raise Forbidden(f"{principal.subject} may not access {founder_id}")
    if write and not principal.can_write:
        raise Forbidden(f"{principal.subject} holds a read-only credential")


# ── Audit ────────────────────────────────────────────────────────────────────

#: Actions worth a security audit event: anything that changes state a
#: founder would notice, plus rejected authentication.
AUDITED_ACTIONS = frozenset(
    {
        "profile.write",
        "run.trigger",
        "run.cancel",
        "inbox.state_change",
        "auth.rejected",
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
    """

    events: list[dict] = field(default_factory=list)

    def record(self, **event: object) -> None:
        self.events.append(dict(event))
