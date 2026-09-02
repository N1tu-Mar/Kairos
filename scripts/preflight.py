#!/usr/bin/env python
"""Preflight: is this configuration safe to deploy?

Run before a deploy, and again against the deployed backend once it is up.
Everything here is a *local* check plus, optionally, HTTP reads against a
running instance. Nothing calls Bedrock, nothing spends money, and nothing
mutates anything — a preflight that changes state is not a preflight.

    uv run scripts/preflight.py                       # check local config
    uv run scripts/preflight.py --env production      # check it as production
    uv run scripts/preflight.py --url https://api...  # also probe a deployment

Exit code is 0 when every check passes, 1 when any FAIL is present. WARN
does not fail the run: a warning is something to have decided about, not
something that is wrong.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

PASS = "PASS"
WARN = "WARN"
FAIL = "FAIL"


@dataclass
class Check:
    """One preflight result: what was checked, the verdict, and a line explaining it.

    `status` is OK, WARN or FAIL. WARN exists so the local posture — an open
    API, zero prices — can be reported honestly without failing a check that
    is correct for localhost.
    """

    name: str
    status: str
    detail: str


def check(name: str, status: str, detail: str = "") -> Check:
    """Build a `Check`. A shorthand so the check functions read as a list of findings."""
    return Check(name, status, detail)


# ── Local configuration ──────────────────────────────────────────────────────


def check_config(production: bool) -> list[Check]:
    """Everything `agent.config.settings()` can tell us, judged by mode."""
    from agent import config

    results: list[Check] = []
    try:
        settings = config.settings()
    except config.ConfigError as exc:
        return [
            check(
                "configuration",
                FAIL,
                # The message names the missing key and the discovery command.
                str(exc).splitlines()[0],
            )
        ]

    results.append(check("configuration", PASS, "settings() resolves"))

    mode = "production" if production else settings.environment
    results.append(check("environment", PASS, mode))

    # Model IDs. Present is all we can check offline — whether they are the
    # *right* IDs for the region is what scripts/smoke_bedrock.py answers,
    # and that one costs money.
    for label, tier in (("reasoning", settings.reasoning), ("classify", settings.classify)):
        if tier.model_id.startswith("[DEMO]"):
            results.append(
                check(
                    f"model.{label}",
                    FAIL if production else WARN,
                    "placeholder model ID — no model call will work",
                )
            )
        else:
            results.append(check(f"model.{label}", PASS, "set"))

    # Credentials.
    if settings.auth_mode == "supabase":
        if settings.supabase_issuer:
            results.append(check("auth.mode", PASS, "supabase user JWTs"))
        else:
            results.append(
                check(
                    "auth.mode",
                    FAIL,
                    "KAIROS_AUTH_MODE=supabase but KAIROS_SUPABASE_ISSUER is empty",
                )
            )
    elif production:
        results.append(
            check(
                "auth.mode",
                FAIL,
                "production requires KAIROS_AUTH_MODE=supabase; local_shared is a laptop",
            )
        )
    else:
        results.append(check("auth.mode", PASS, "local_shared"))

    if settings.enable_browser and production:
        results.append(
            check(
                "browser",
                FAIL,
                "production refuses KAIROS_ENABLE_BROWSER — Playwright is not isolated",
            )
        )

    if settings.scheduler_token:
        results.append(check("auth.scheduler", PASS, "scheduler token configured"))
    elif production:
        results.append(
            check(
                "auth.scheduler",
                WARN,
                "no KAIROS_SCHEDULER_TOKEN — EventBridge cannot trigger a run",
            )
        )

    if settings.credentials_file:
        path = Path(settings.credentials_file)
        if not path.exists():
            results.append(
                check("auth", FAIL, f"KAIROS_CREDENTIALS_FILE does not exist: {path}")
            )
        else:
            try:
                data = json.loads(path.read_text())
                count = len(data.get("credentials", []))
            except (json.JSONDecodeError, OSError) as exc:
                results.append(check("auth", FAIL, f"credential file unreadable: {exc}"))
            else:
                if count == 0:
                    results.append(
                        check("auth", FAIL, "credential file has no credentials — nobody can authenticate")
                    )
                else:
                    results.append(
                        check("auth", PASS, f"{count} credential(s) from a file")
                    )
                if _looks_like_a_raw_token(data):
                    results.append(
                        check(
                            "auth.hashing",
                            FAIL,
                            "a token_hash is not a SHA-256 digest — raw tokens must never be stored",
                        )
                    )
    elif settings.api_token:
        results.append(check("auth", PASS, "shared token, single founder"))
    elif settings.allow_open_api:
        results.append(
            check(
                "auth",
                FAIL if production else WARN,
                "no credential configured and KAIROS_ALLOW_OPEN_API is on — "
                "the API runs open",
            )
        )
    else:
        # Fails closed rather than open, so this is a refusal to serve, not a
        # hole. Still worth naming: the operator meant to configure something.
        results.append(
            check(
                "auth",
                FAIL,
                "no credential configured — every request will 401; set "
                "KAIROS_API_TOKEN, or KAIROS_ALLOW_OPEN_API=1 for a local demo",
            )
        )

    # The dollar cap, and whether it can actually fire.
    if settings.daily_usd_cap <= 0:
        results.append(
            check("spend.daily_cap", WARN, "off; only the per-run token ceiling applies")
        )
    elif not settings.prices.configured:
        results.append(
            check(
                "spend.daily_cap",
                FAIL if production else WARN,
                f"${settings.daily_usd_cap:.2f}/day is set but token prices are 0, "
                f"so every call costs $0.00 and the cap can never trip",
            )
        )
    else:
        results.append(
            check("spend.daily_cap", PASS, f"${settings.daily_usd_cap:.2f}/day, priced")
        )

    results.append(
        check("spend.token_ceiling", PASS, f"{settings.max_run_tokens:,} tokens/run")
    )

    # Storage.
    state_dir = Path(settings.state_dir)
    try:
        state_dir.mkdir(parents=True, exist_ok=True)
        probe = state_dir / ".preflight"
        probe.write_text("ok")
        probe.unlink()
        results.append(check("storage.state_dir", PASS, "writable"))
    except OSError as exc:
        results.append(check("storage.state_dir", FAIL, f"not writable: {exc}"))

    results.extend(_check_schema(settings, production))
    results.extend(check_supabase(settings))

    # Run timeout vs lease TTL. The lease must outlive the run it protects.
    results.append(
        check(
            "runs.timeout",
            PASS,
            f"{settings.run_timeout_s:.0f}s, lease TTL {settings.run_timeout_s * 2:.0f}s",
        )
    )

    return results


def _looks_like_a_raw_token(data: dict) -> bool:
    """A token_hash that is not 64 hex characters is not a SHA-256 digest."""
    import re

    for entry in data.get("credentials", []):
        value = str(entry.get("token_hash", ""))
        if not re.fullmatch(r"[0-9a-f]{64}", value):
            return True
    return False


def _check_schema(settings, production: bool) -> list[Check]:
    """Whether the database exists and is at a migration revision.

    Opens the repository with `create_schema=False` on purpose: this must
    report a missing schema, not create one. An unmigrated database is a WARN
    locally and a FAIL in production, where it means the deploy skipped
    `alembic upgrade head`.
    """
    from api.repository import SqliteRepository

    try:
        repo = SqliteRepository(settings.db_url, create_schema=False)
        version = repo.schema_version()
    except Exception as exc:  # noqa: BLE001
        return [check("storage.database", FAIL, f"unreachable: {type(exc).__name__}")]

    if version is None:
        return [
            check(
                "storage.schema",
                FAIL if production else WARN,
                "no alembic_version — run `alembic upgrade head` before deploying",
            )
        ]
    return [check("storage.schema", PASS, f"at revision {version}")]


# ── Repository hygiene ───────────────────────────────────────────────────────


def check_supabase(settings) -> list[Check]:
    """Whether Supabase identity is wired up, and whether it can actually verify.

    Runs only when an issuer is configured; otherwise the deployment is on the
    shared token or the credential file and there is nothing here to say.

    Everything checked is public. The JWKS endpoint is published for exactly
    this purpose, so this makes one unauthenticated GET and holds no secret —
    which is what makes it safe to run anywhere, including CI.
    """
    results: list[Check] = []
    issuer = settings.supabase_issuer
    if not issuer:
        return results

    # Shape first: a pasted URL very often carries /rest/v1 or a trailing
    # slash, and either makes every `iss` comparison fail with a 401 that
    # looks like a credential problem rather than a typo.
    if issuer.endswith("/"):
        results.append(
            check("supabase.issuer", FAIL, "ends with a slash; Supabase's `iss` does not")
        )
    elif "/rest/v1" in issuer:
        results.append(
            check(
                "supabase.issuer",
                FAIL,
                "points at the REST API; the issuer is the project URL + /auth/v1",
            )
        )
    elif not issuer.endswith("/auth/v1"):
        results.append(
            check("supabase.issuer", FAIL, "must end with /auth/v1")
        )
    else:
        results.append(check("supabase.issuer", PASS, "well-formed"))

    if settings.supabase_jwt_secret and settings.supabase_public_key:
        results.append(
            check(
                "supabase.keys",
                FAIL,
                "both a JWT secret and a public key are set; which algorithm "
                "family is trusted would be ambiguous",
            )
        )
    elif settings.supabase_jwt_secret:
        results.append(
            check(
                "supabase.keys",
                WARN,
                "using a shared HS256 secret — the value that verifies a token "
                "can also mint one. Prefer asymmetric keys (JWKS).",
            )
        )
    elif settings.supabase_public_key:
        results.append(
            check(
                "supabase.keys",
                WARN,
                "using one static public key — logins break at the next "
                "rotation. Clear it to use JWKS instead.",
            )
        )
    else:
        results.append(check("supabase.keys", PASS, "JWKS; keys are fetched and rotate"))

        # Only worth probing on the JWKS path: it is the one with a remote
        # dependency, and a broken endpoint means nobody can sign in.
        jwks_url = f"{issuer.rstrip('/')}/.well-known/jwks.json"
        payload = _get(jwks_url)
        if payload is None:
            results.append(
                check(
                    "supabase.jwks",
                    FAIL,
                    "signing keys are unreachable — check the issuer and that "
                    "the project is awake",
                )
            )
        else:
            keys = payload.get("keys") or []
            algorithms = sorted({k.get("alg", "?") for k in keys})
            if not keys:
                results.append(
                    check(
                        "supabase.jwks",
                        FAIL,
                        "endpoint responded but published no keys — the project "
                        "may still be on a shared HS256 secret",
                    )
                )
            elif "HS256" in algorithms:
                results.append(
                    check(
                        "supabase.jwks",
                        WARN,
                        f"published keys include HS256 ({', '.join(algorithms)})",
                    )
                )
            else:
                results.append(
                    check(
                        "supabase.jwks",
                        PASS,
                        f"{len(keys)} key(s), {', '.join(algorithms)}",
                    )
                )

    return results


def check_repository() -> list[Check]:
    """Things that would be a credential leak rather than a misconfiguration."""
    import subprocess

    results: list[Check] = []
    try:
        tracked = subprocess.run(
            ["git", "ls-files"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.splitlines()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return [check("repo.tracked_files", WARN, "not a git checkout; skipped")]

    dangerous = [
        name
        for name in tracked
        if Path(name).name in {".env", ".env.local", "credentials.json"}
        or name.endswith((".tfvars", ".tfstate"))
        or ".tfstate." in name
    ]
    if dangerous:
        results.append(
            check("repo.tracked_files", FAIL, f"credential/state files tracked: {dangerous}")
        )
    else:
        results.append(check("repo.tracked_files", PASS, "no credential or state files tracked"))

    return results


# ── Deployed instance ────────────────────────────────────────────────────────


def check_deployment(url: str, token: str | None) -> list[Check]:
    """Read-only probes against a running backend."""
    results: list[Check] = []
    base = url.rstrip("/")

    if base.startswith("http://") and not base.startswith("http://127.0.0.1"):
        results.append(
            check(
                "deploy.transport",
                FAIL,
                "plain HTTP — the bearer token would cross the wire in the clear",
            )
        )
    else:
        results.append(check("deploy.transport", PASS, base.split("://")[0]))

    live = _get(f"{base}/health")
    if live is None:
        results.append(check("deploy.health", FAIL, "unreachable"))
        return results
    results.append(check("deploy.health", PASS, "serving"))

    ready = _get(f"{base}/ready")
    if ready is None:
        results.append(check("deploy.ready", FAIL, "not ready (503) or unreachable"))
    else:
        checks = ready.get("checks", {})
        bad = {k: v for k, v in checks.items() if v != "ok"}
        if bad:
            results.append(check("deploy.ready", FAIL, json.dumps(bad)))
        else:
            results.append(check("deploy.ready", PASS, "every dependency ok"))

    # The gate itself: an unauthenticated read must be refused.
    code = _status(f"{base}/founders/founder_demo")
    if code == 401:
        results.append(check("deploy.auth", PASS, "unauthenticated reads are refused"))
    elif code is None:
        results.append(check("deploy.auth", WARN, "could not probe"))
    else:
        results.append(
            check(
                "deploy.auth",
                FAIL,
                f"an unauthenticated read returned {code} — the API is open",
            )
        )

    if token:
        code = _status(f"{base}/founders/founder_demo", token=token)
        if code == 200:
            results.append(check("deploy.credential", PASS, "the supplied token works"))
        else:
            results.append(
                check("deploy.credential", FAIL, f"the supplied token returned {code}")
            )

    return results


def _get(url: str, token: str | None = None) -> dict | None:
    """GET and parse JSON, or None on any failure.

    None is deliberately ambiguous — unreachable, timed out, or not JSON —
    because every caller treats all three the same: the check could not be
    performed. Never raises, so one dead endpoint cannot end the preflight.
    """
    request = urllib.request.Request(url)
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.loads(response.read())
    except (urllib.error.URLError, json.JSONDecodeError, TimeoutError):
        return None


def _status(url: str, token: str | None = None) -> int | None:
    """GET and return the HTTP status, or None if the request never completed.

    An HTTP error status is a result, not a failure: 401 and 403 are exactly
    what a credential check is looking for, so `HTTPError` is unwrapped to
    its code rather than swallowed.
    """
    request = urllib.request.Request(url)
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status
    except urllib.error.HTTPError as exc:
        return exc.code
    except (urllib.error.URLError, TimeoutError):
        return None


# ── Entry point ──────────────────────────────────────────────────────────────


def main() -> int:
    """CLI entry. 0 when nothing FAILed, 1 otherwise. WARNs are printed and do not fail.

    `--env` overrides `KAIROS_ENV`, so the production posture can be checked
    from a laptop without setting the variable and changing how the rest of
    the process behaves.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--env",
        choices=["local", "production"],
        help="Judge the configuration as this environment. Defaults to KAIROS_ENV.",
    )
    parser.add_argument("--url", help="Also probe a running backend at this base URL.")
    parser.add_argument(
        "--token",
        help="Bearer token for the --url probe. Defaults to KAIROS_API_TOKEN. "
        "Prefer the environment variable: an argument lands in your shell history.",
    )
    args = parser.parse_args()

    if args.env:
        os.environ["KAIROS_ENV"] = args.env
        from agent import config

        config.settings.cache_clear()

    production = os.getenv("KAIROS_ENV", "local").strip().lower() == "production"

    results = check_config(production) + check_repository()
    if args.url:
        results += check_deployment(args.url, args.token or os.getenv("KAIROS_API_TOKEN"))

    width = max(len(r.name) for r in results)
    for result in results:
        print(f"{result.status:<4}  {result.name:<{width}}  {result.detail}")

    failures = [r for r in results if r.status == FAIL]
    warnings = [r for r in results if r.status == WARN]
    print()
    print(
        f"{len(results) - len(failures) - len(warnings)} passed, "
        f"{len(warnings)} warning(s), {len(failures)} failure(s)"
    )
    if failures:
        print("\nNot safe to deploy. Fix the failures above.")
        return 1
    if warnings:
        print("\nSafe to deploy, with the warnings above understood.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
