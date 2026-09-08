"""Validation for externally sourced links stored by Kairos."""

from __future__ import annotations

import unicodedata
from urllib.parse import urlsplit


def validate_external_url(value: str, *, allow_empty: bool = False) -> str:
    """Return an absolute HTTP(S) URL or raise ``ValueError``.

    Browser link handling is deliberately narrower than general URI parsing:
    no credentials, protocol-relative values, control characters, or custom
    schemes are useful for a public funding source.
    """
    if not isinstance(value, str):
        raise ValueError("external URL must be text")
    if allow_empty and value == "":
        return value
    if not value or value != value.strip():
        raise ValueError("external URL must be a non-empty absolute URL")
    if value.startswith("//"):
        raise ValueError("protocol-relative external URLs are not allowed")
    if "\\" in value or any(
        character.isspace()
        or unicodedata.category(character) in {"Cc", "Cf"}
        for character in value
    ):
        raise ValueError("external URLs may not contain whitespace or control characters")
    try:
        parts = urlsplit(value)
        # Accessing the port forces malformed values such as ':not-a-port'
        # through urllib's validation rather than deferring the error.
        _ = parts.port
    except (UnicodeError, ValueError) as exc:
        raise ValueError("external URL is malformed") from exc
    if parts.scheme.lower() not in {"http", "https"}:
        raise ValueError("external URL scheme must be http or https")
    if not parts.hostname:
        raise ValueError("external URL must include a hostname")
    if parts.username is not None or parts.password is not None:
        raise ValueError("external URLs may not contain credentials")
    return value
