"""Open mode is a decision, never a default.

The gap this closes: `SharedTokenAuthenticator` used to read an empty
`KAIROS_API_TOKEN` as "run open" and only refuse when `KAIROS_ENV` *also*
said `production`. Two variables had to be right for the API to be shut, and
the failure was silent — a deployment missing both came up green, served
every request as `ANONYMOUS_LOCAL`, and said so only in a startup log line.

That guard lived in the Terraform, not in the code. `infra/main.tf` sets both
variables, so ECS was covered; a laptop, a `docker run`, a PaaS host or a
managed-Postgres deployment inherits neither.

So open mode now needs its own explicit opt-in, and an unconfigured
deployment fails closed regardless of what `KAIROS_ENV` says.
"""

from __future__ import annotations

import pytest

from agent import config
from api.auth import (
    ANONYMOUS_LOCAL,
    AuthError,
    SharedTokenAuthenticator,
    build_authenticator,
)


# ── The authenticator itself ─────────────────────────────────────────────────


def test_empty_token_without_the_opt_in_is_refused():
    """The default posture. No token, no flag, no service."""
    auth = SharedTokenAuthenticator("", allow_open=False)

    with pytest.raises(AuthError):
        auth.authenticate(None)


def test_empty_token_is_refused_even_with_a_credential_supplied():
    """A caller cannot talk an unconfigured deployment into a principal.

    Guards the tempting-but-wrong reading of "no token configured" as "any
    token accepted".
    """
    auth = SharedTokenAuthenticator("", allow_open=False)

    with pytest.raises(AuthError):
        auth.authenticate("Bearer anything-at-all")


def test_the_opt_in_restores_open_mode():
    """Explicitly asked for, explicitly granted — the localhost demo."""
    auth = SharedTokenAuthenticator("", allow_open=True)

    assert auth.authenticate(None) is ANONYMOUS_LOCAL


@pytest.mark.parametrize("environment", ["local", "demo", "staging", "production", ""])
def test_the_refusal_does_not_depend_on_the_environment_name(environment):
    """`KAIROS_ENV` no longer gates authentication.

    This is the actual regression: `environment="demo"` used to be enough to
    reopen the API, and so did a typo, and so did leaving it unset.
    """
    auth = SharedTokenAuthenticator("", allow_open=False, environment=environment)

    with pytest.raises(AuthError):
        auth.authenticate(None)


def test_a_configured_token_still_authenticates():
    """The opt-in changes nothing for a deployment that set a token."""
    auth = SharedTokenAuthenticator("real-token", allow_open=False)

    principal = auth.authenticate("Bearer real-token")

    assert principal.method == "shared_token"
    assert principal.can_write


def test_a_wrong_token_is_still_refused_when_open_mode_is_allowed():
    """The flag permits *no* credential. It never weakens a supplied one."""
    auth = SharedTokenAuthenticator("real-token", allow_open=True)

    with pytest.raises(AuthError):
        auth.authenticate("Bearer wrong-token")


# ── Wiring it up from configuration ──────────────────────────────────────────


def _settings(monkeypatch, **env: str):
    for key in (
        "KAIROS_API_TOKEN",
        "KAIROS_ALLOW_OPEN_API",
        "KAIROS_ENV",
        "KAIROS_CREDENTIALS_FILE",
    ):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    config.settings.cache_clear()
    return config.settings()


def test_config_defaults_to_closed(monkeypatch):
    """A clean environment produces an authenticator that refuses."""
    auth = build_authenticator(_settings(monkeypatch))

    with pytest.raises(AuthError):
        auth.authenticate(None)


def test_config_opt_in_is_honoured(monkeypatch):
    auth = build_authenticator(_settings(monkeypatch, KAIROS_ALLOW_OPEN_API="1"))

    assert auth.authenticate(None) is ANONYMOUS_LOCAL


@pytest.mark.parametrize("raw", ["0", "false", "no", "off", "", "treu"])
def test_only_a_real_truthy_value_opens_the_api(monkeypatch, raw):
    """`_bool` semantics: anything unrecognised reads as off, the safe way.

    A typo in the flag that opens an API must fail closed, not open.
    """
    auth = build_authenticator(_settings(monkeypatch, KAIROS_ALLOW_OPEN_API=raw))

    with pytest.raises(AuthError):
        auth.authenticate(None)


def test_production_still_refuses_even_with_the_opt_in(monkeypatch):
    """Belt and braces: the flag is not an escape hatch out of production.

    Someone will eventually copy a working local `.env` onto a real host. The
    flag makes open mode explicit; production makes it unavailable.
    """
    auth = build_authenticator(
        _settings(monkeypatch, KAIROS_ALLOW_OPEN_API="1", KAIROS_ENV="production")
    )

    with pytest.raises(AuthError):
        auth.authenticate(None)
