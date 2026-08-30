"""Nothing we fetch may point back inside the network we fetch from.

The scraper's URLs come from a search API, which means they are chosen by
whoever ranks for our queries rather than by us. `agent/scraping/agent.py`
already checks the scheme. That is not enough on its own: `http://` is a
perfectly good way to spell `http://169.254.170.2/v2/credentials/...`, which
on Fargate returns the task role's temporary credentials to anything inside
the container that asks — and `PoliteFetcher` then writes the response into
`data/raw/pages/`.

Two properties are tested here, and the second is the one that actually bites:

*   An address that is not publicly routable is refused.
*   The check runs on **every redirect hop**, not just on the URL we were
    handed. `follow_redirects=True` made the first check decorative: a public
    URL that 302s to a link-local address passed the check and fetched the
    target anyway.
"""

from __future__ import annotations

import pytest

from agent.scraping.netguard import BlockedAddress, assert_public_url


# ── Addresses that must never be fetched ─────────────────────────────────────


@pytest.mark.parametrize(
    "url, why",
    [
        ("http://169.254.170.2/v2/credentials/abc", "ECS task role credentials"),
        ("http://169.254.169.254/latest/meta-data/", "EC2 instance metadata"),
        ("http://127.0.0.1:8000/founders/founder_demo", "our own API"),
        ("http://localhost:8000/ready", "our own API by name"),
        ("http://[::1]:8000/ready", "our own API over IPv6"),
        ("http://10.0.1.15/", "private RFC1918"),
        ("http://192.168.1.1/", "home router"),
        ("http://172.16.0.5/", "private RFC1918, the range people forget"),
        ("http://0.0.0.0/", "unspecified"),
        ("http://[fd00::1]/", "IPv6 unique-local"),
    ],
)
def test_unroutable_addresses_are_refused(url, why):
    with pytest.raises(BlockedAddress):
        assert_public_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "gopher://example.com/",
        "ftp://example.com/",
        "data:text/html,hello",
    ],
)
def test_non_http_schemes_are_refused(url):
    """The scheme check lives here too, so one call covers the whole question."""
    with pytest.raises(BlockedAddress):
        assert_public_url(url)


def test_a_hostless_url_is_refused():
    with pytest.raises(BlockedAddress):
        assert_public_url("http:///no-host-here")


# ── Addresses that must still be fetchable ───────────────────────────────────


def test_a_public_address_passes(monkeypatch):
    """A real target still resolves. Stubbed so the suite needs no network."""
    _stub_resolver(monkeypatch, {"idea.rutgers.edu": ["128.6.4.1"]})

    assert_public_url("https://idea.rutgers.edu/programs/scarletpitch")


def test_a_literal_public_ip_passes(monkeypatch):
    _stub_resolver(monkeypatch, {})

    assert_public_url("http://93.184.216.34/page")


# ── The multi-record case ────────────────────────────────────────────────────


def test_every_resolved_address_must_be_public(monkeypatch):
    """One public A record does not license the internal one beside it.

    A host answering with both a routable address and 127.0.0.1 is a
    DNS-rebinding setup, and checking only the first answer walks straight
    into it.
    """
    _stub_resolver(monkeypatch, {"split.example": ["93.184.216.34", "127.0.0.1"]})

    with pytest.raises(BlockedAddress):
        assert_public_url("http://split.example/")


def test_a_host_that_does_not_resolve_is_refused(monkeypatch):
    """Unresolvable is refused, not allowed through to httpx to find out.

    Fail closed: "we could not tell" and "it is safe" are different answers.
    """
    _stub_resolver(monkeypatch, {})

    with pytest.raises(BlockedAddress):
        assert_public_url("http://nx.invalid/")


# ── The hop that matters: redirects ──────────────────────────────────────────


def test_a_redirect_into_link_local_is_refused(monkeypatch, tmp_path):
    """The actual attack, end to end.

    A page that ranks for one of our search queries answers 302 to the ECS
    credential endpoint. Before the manual redirect loop this fetched it and
    archived the response; now the second hop is checked like the first.
    """
    fetcher = _fetcher(monkeypatch, tmp_path)
    _stub_resolver(monkeypatch, {"grants.example": ["93.184.216.34"]})
    _stub_http(
        monkeypatch,
        {
            "https://grants.example/apply": _redirect(
                "https://grants.example/apply", "http://169.254.170.2/v2/credentials/x"
            )
        },
    )

    text, record = fetcher.fetch("https://grants.example/apply")

    assert text == ""
    assert record.failure is not None
    assert record.failure.startswith("BLOCKED_ADDRESS")
    assert not list(tmp_path.rglob("*.html")), "a blocked hop must archive nothing"


def test_the_blocked_failure_does_not_name_the_internal_address(monkeypatch, tmp_path):
    """Run notes are served over the API. They must not map the network."""
    fetcher = _fetcher(monkeypatch, tmp_path)
    _stub_resolver(monkeypatch, {"grants.example": ["93.184.216.34"]})
    _stub_http(
        monkeypatch,
        {
            "https://grants.example/apply": _redirect(
                "https://grants.example/apply", "http://10.0.1.15/internal"
            )
        },
    )

    _, record = fetcher.fetch("https://grants.example/apply")

    assert "10.0.1.15" not in record.failure


def test_an_ordinary_redirect_is_still_followed(monkeypatch, tmp_path):
    """The guard must not break the normal case: sites move pages constantly."""
    fetcher = _fetcher(monkeypatch, tmp_path)
    _stub_resolver(
        monkeypatch,
        {"grants.example": ["93.184.216.34"], "www.grants.example": ["93.184.216.34"]},
    )
    body = "<html><body>" + ("Apply for the fund. " * 60) + "</body></html>"
    _stub_http(
        monkeypatch,
        {
            "https://grants.example/apply": _redirect(
                "https://grants.example/apply", "https://www.grants.example/apply"
            ),
            "https://www.grants.example/apply": _ok(
                "https://www.grants.example/apply", body
            ),
        },
    )

    text, record = fetcher.fetch("https://grants.example/apply")

    assert "Apply for the fund" in text
    assert record.failure is None


# ── Helpers ──────────────────────────────────────────────────────────────────


def _fetcher(monkeypatch, tmp_path):
    """A `PoliteFetcher` with robots stubbed to allow, writing under tmp_path."""
    from agent.scraping.fetch import PoliteFetcher
    from agent.scraping.robots import RobotsDecision

    fetcher = PoliteFetcher(tmp_path / "raw")
    monkeypatch.setattr(
        fetcher.robots,
        "check",
        lambda url: RobotsDecision(
            allowed=True, robots_url="", crawl_delay_s=0.0, reason="stubbed"
        ),
    )
    return fetcher


def _ok(url: str, body: str) -> "httpx.Response":
    import httpx

    return httpx.Response(200, text=body, request=httpx.Request("GET", url))


def _redirect(url: str, location: str) -> "httpx.Response":
    import httpx

    return httpx.Response(
        302, headers={"location": location}, request=httpx.Request("GET", url)
    )


def _stub_http(monkeypatch, responses: dict):
    """Replace `httpx.get` with a lookup table keyed by URL."""

    def fake_get(url, **kwargs):
        assert kwargs.get("follow_redirects") is False, (
            "the fetcher must follow redirects itself so every hop is checked"
        )
        if url not in responses:
            raise AssertionError(f"unexpected fetch of {url}")
        return responses[url]

    monkeypatch.setattr("agent.scraping.fetch.httpx.get", fake_get)


def _stub_resolver(monkeypatch, table: dict[str, list[str]]):
    """Replace DNS with a fixed table, so these tests never touch the network."""
    import socket

    def fake_getaddrinfo(host, port, *args, **kwargs):
        if host not in table:
            raise socket.gaierror(f"stubbed: no record for {host}")
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, port or 80))
            for address in table[host]
        ]

    monkeypatch.setattr(
        "agent.scraping.netguard.socket.getaddrinfo", fake_getaddrinfo
    )
