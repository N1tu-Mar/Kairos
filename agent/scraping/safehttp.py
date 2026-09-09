"""Bounded HTTP retrieval for attacker-influenced scraper destinations.

The guard is deliberately kept below robots and page fetching so both paths
share the same redirect, destination, and response-size rules. DNS is checked
before each connection. Network isolation is still required in production to
close the DNS-rebinding gap between validation and the client's own lookup.
"""

from __future__ import annotations

from collections.abc import Mapping

import httpx

from agent.scraping.netguard import assert_public_url

DEFAULT_MAX_BYTES = 4_000_000
DEFAULT_MAX_REDIRECTS = 5


class ResponseTooLarge(httpx.RequestError):
    """The remote response exceeded the configured byte ceiling."""


def _declared_size(response: httpx.Response) -> int | None:
    value = response.headers.get("content-length")
    if value is None:
        return None
    try:
        size = int(value)
    except ValueError:
        return None
    return size if size >= 0 else None


def _read_bounded(response: httpx.Response, max_bytes: int) -> bytes:
    declared = _declared_size(response)
    if declared is not None and declared > max_bytes:
        raise ResponseTooLarge(
            f"response exceeds {max_bytes} bytes", request=response.request
        )

    chunks: list[bytes] = []
    received = 0
    for chunk in response.iter_bytes():
        received += len(chunk)
        if received > max_bytes:
            raise ResponseTooLarge(
                f"response exceeds {max_bytes} bytes", request=response.request
            )
        chunks.append(chunk)
    return b"".join(chunks)


def guarded_get(
    url: str,
    *,
    client: httpx.Client | None = None,
    timeout: float | httpx.Timeout = 30.0,
    headers: Mapping[str, str] | None = None,
    max_bytes: int = DEFAULT_MAX_BYTES,
    max_redirects: int = DEFAULT_MAX_REDIRECTS,
) -> httpx.Response:
    """GET a public URL while validating redirects and bounding its body.

    An injected client lets callers reuse connections and lets tests provide a
    deterministic transport. The returned response owns an in-memory body no
    larger than ``max_bytes``; the network stream has already been closed.
    """
    if max_bytes < 0:
        raise ValueError("max_bytes must be non-negative")
    if max_redirects < 0:
        raise ValueError("max_redirects must be non-negative")

    owns_client = client is None
    http = client or httpx.Client()
    current = url
    last_request: httpx.Request | None = None

    try:
        for hop in range(max_redirects + 1):
            assert_public_url(current)
            with http.stream(
                "GET",
                current,
                timeout=timeout,
                follow_redirects=False,
                headers=headers,
            ) as streamed:
                last_request = streamed.request
                if streamed.has_redirect_location:
                    if hop == max_redirects:
                        break
                    current = str(streamed.url.join(streamed.headers["location"]))
                    continue

                content = _read_bounded(streamed, max_bytes)
                return httpx.Response(
                    status_code=streamed.status_code,
                    headers=streamed.headers,
                    content=content,
                    request=streamed.request,
                    extensions=streamed.extensions,
                )

        request = last_request or httpx.Request("GET", current)
        raise httpx.TooManyRedirects(
            f"more than {max_redirects} redirects", request=request
        )
    finally:
        if owns_client:
            http.close()
