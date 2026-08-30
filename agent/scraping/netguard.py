"""Where a fetch is allowed to point.

`agent/scraping/agent.py` takes URLs from a search API. That makes the target
list attacker-influenceable: anything that ranks for `"startup grant" "student
founder"` gets fetched by a process running inside our network, holding our
cloud credentials. The scheme check there answers "is this a web page"; this
module answers the question that actually matters, **"is this address out on
the internet, or is it one of ours"**.

The address that motivates the file is `169.254.170.2`. On Fargate it serves
the task role's temporary credentials to anything in the container that asks
— by design, on the assumption that only our own code asks. A search result
that redirects there turns `PoliteFetcher` into the thing that asks, and
`_archive` then writes the answer to disk. It is link-local, so the general
rule covers it and there is no special case for it below; the same rule
covers `127.0.0.1`, our own API, and every RFC1918 host on the deploy's VPC.

**What this does not close.** The check resolves a name and then httpx
resolves it again to connect, so a record that changes between the two —
public on the check, internal on the connect — still gets through. Closing
that means pinning the resolved address and connecting to it directly with a
`Host` header, which is a transport-level change to how every fetch is made.
This module is the cheap 90%; the rebinding case is written down in
`incomplete.md` rather than silently assumed away.
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlsplit

#: The only two schemes a page fetch may use. Anything else — `file://`,
#: `gopher://`, `data:` — is a way of asking the HTTP client to do something
#: other than fetch a web page.
ALLOWED_SCHEMES = frozenset({"http", "https"})


class BlockedAddress(RuntimeError):
    """The URL resolves somewhere a scrape may not go.

    Carries the host but never the resolved address: this message reaches a
    run's notes, and the notes are served over the API. That a host was
    refused is useful; which internal IP it pointed at is a map of the
    network, handed to whoever supplied the URL.
    """


def _is_public(address: str) -> bool:
    """Whether one resolved address is out on the public internet.

    Everything not-global is refused rather than enumerating what to block:
    loopback, private, link-local, reserved, multicast and unspecified are
    each their own carve-out, and a list of them written by hand acquires a
    gap. `is_global` is the one predicate that already means all of it, and
    it stays right as the address space changes.
    """
    try:
        parsed = ipaddress.ip_address(address)
    except ValueError:
        return False
    return parsed.is_global


def _resolve(host: str) -> list[str]:
    """Every address a host answers with. Empty when it does not resolve."""
    try:
        answers = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except (socket.gaierror, UnicodeError, ValueError):
        return []
    return [answer[4][0] for answer in answers]


def assert_public_url(url: str) -> None:
    """Raise `BlockedAddress` unless `url` is an ordinary public web address.

    Call it on the URL you were given *and on every redirect hop*. Checking
    only the first one is the hole redirects exist to walk through: a public
    URL that 302s to a link-local address passes a one-shot check and fetches
    the target anyway.
    """
    parts = urlsplit(url)

    if parts.scheme.lower() not in ALLOWED_SCHEMES:
        raise BlockedAddress(f"scheme {parts.scheme!r} is not fetchable")

    host = parts.hostname
    if not host:
        raise BlockedAddress("URL has no host")

    # A bracketed IPv6 literal or a dotted quad needs no DNS; anything else
    # does. `hostname` has already stripped the brackets and the port.
    try:
        ipaddress.ip_address(host)
        literal = True
        addresses = [host]
    except ValueError:
        literal = False
        addresses = _resolve(host)

    # What the refusal is allowed to call the target. A hostname came from the
    # search result and naming it back tells no one anything they did not
    # supply; a literal *is* the address, so naming it would put an internal
    # IP into a run note that the API serves.
    label = "the requested address" if literal else host

    if not addresses:
        # Fail closed. "We could not tell" is not "it is safe" — and handing
        # an unresolvable name to httpx to find out just moves the resolution
        # somewhere with no check on it.
        raise BlockedAddress(f"{label} did not resolve")

    # Every answer, not the first. A host that returns one routable address
    # and one internal one is a rebinding setup, and taking the first answer
    # is how it gets used.
    if not all(_is_public(address) for address in addresses):
        raise BlockedAddress(f"{label} resolves to a non-public address")
