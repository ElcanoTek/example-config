"""Unit tests for the SSRF guard for the bundle's URL fetchers
(utils.url_guard).

A fetcher of URLs taken from email bodies handles input that is attacker
controlled for anyone who can email the mailbox, so the guard's contract is
pinned tightly here:

* non-http(s) schemes are refused;
* literal loopback / private / link-local (cloud metadata) / CGNAT / ULA /
  multicast / reserved / IPv4-mapped addresses are refused, IPv4 and IPv6;
* hostnames are resolved, and ANY non-public address in the answer refuses
  the fetch (mixed public+private answers are an attacker's DNS trick);
* the request event hook re-validates EVERY redirect hop, so a public URL
  302-ing to an internal address is refused mid-chain;
* plain public URLs (and redirects between public hosts) keep working.
"""

import os
import sys

import httpx
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from utils import url_guard
from utils.url_guard import BlockedUrlError, assert_public_url

PUBLIC_IP = "93.184.216.34"  # example.com


@pytest.fixture
def resolver(monkeypatch):
    """Route url_guard's DNS through a controllable in-test table."""
    table: dict[str, list[str]] = {}

    async def fake_resolve(host: str) -> list[str]:
        try:
            return table[host]
        except KeyError:
            raise BlockedUrlError(f"SECURITY: URL host {host!r} did not resolve: test") from None

    monkeypatch.setattr(url_guard, "_resolve_host", fake_resolve)
    return table


# ── Schemes ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("url", ["ftp://example.com/x", "file:///etc/passwd", "gopher://example.com/"])
async def test_non_http_schemes_refused(url):
    with pytest.raises(BlockedUrlError, match="scheme"):
        await assert_public_url(url)


async def test_missing_host_refused():
    with pytest.raises(BlockedUrlError):
        await assert_public_url("http://")


# ── IP literals ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "host",
    [
        "127.0.0.1",  # loopback
        "10.0.0.1",  # RFC1918
        "172.16.0.1",  # RFC1918
        "192.168.1.1",  # RFC1918
        "169.254.169.254",  # link-local — AWS/GCP metadata
        "100.64.0.1",  # CGNAT (Alibaba metadata lives at 100.100.100.200)
        "0.0.0.0",  # unspecified
        "224.0.0.1",  # multicast
        "255.255.255.255",  # broadcast
        "[::1]",  # v6 loopback
        "[fd00::1]",  # v6 ULA
        "[fe80::1]",  # v6 link-local
        "[::ffff:127.0.0.1]",  # IPv4-mapped loopback
        "[::ffff:10.0.0.1]",  # IPv4-mapped RFC1918
        "[64:ff9b::a00:1]",  # NAT64-embedded 10.0.0.1 (reserved space)
    ],
)
async def test_non_public_ip_literals_refused(host):
    with pytest.raises(BlockedUrlError, match="SECURITY"):
        await assert_public_url(f"http://{host}/latest/meta-data/")


@pytest.mark.parametrize("host", [PUBLIC_IP, "[2001:4860:4860::8888]"])
async def test_public_ip_literals_allowed(host):
    await assert_public_url(f"https://{host}/report.csv")  # must not raise


# ── Hostname resolution ──────────────────────────────────────────────────────


async def test_hostname_resolving_private_is_refused(resolver):
    resolver["internal.example.com"] = ["10.1.2.3"]
    with pytest.raises(BlockedUrlError, match="10.1.2.3"):
        await assert_public_url("https://internal.example.com/report.csv")


async def test_hostname_with_mixed_answers_is_refused(resolver):
    # An attacker's DNS can return public + private A records and let the
    # resolver pick — one bad address poisons the whole answer.
    resolver["evil.example.com"] = [PUBLIC_IP, "169.254.169.254"]
    with pytest.raises(BlockedUrlError, match="169.254.169.254"):
        await assert_public_url("https://evil.example.com/file.pdf")


async def test_hostname_resolving_public_is_allowed(resolver):
    resolver["cdn.example.com"] = [PUBLIC_IP]
    await assert_public_url("https://cdn.example.com/report.csv")  # must not raise


async def test_unresolvable_hostname_is_refused(resolver):
    with pytest.raises(BlockedUrlError, match="did not resolve"):
        await assert_public_url("https://nxdomain.example.com/x")


# ── Redirect-hop re-validation via the httpx request hook ────────────────────


def _mock_client(routes: dict[str, httpx.Response]) -> httpx.AsyncClient:
    """AsyncClient wired exactly like the download tools: follow_redirects
    plus the guard's request hook, but over a MockTransport."""

    def handler(request: httpx.Request) -> httpx.Response:
        return routes[str(request.url)]

    return httpx.AsyncClient(
        follow_redirects=True,
        transport=httpx.MockTransport(handler),
        event_hooks={"request": [url_guard.request_hook]},
    )


async def test_redirect_to_internal_address_is_refused_mid_chain(resolver):
    resolver["tracker.example.com"] = [PUBLIC_IP]
    routes = {
        "https://tracker.example.com/ls/click": httpx.Response(
            302, headers={"location": "http://169.254.169.254/latest/meta-data/"}
        ),
    }
    async with _mock_client(routes) as client:
        with pytest.raises(BlockedUrlError, match="169.254.169.254"):
            await client.get("https://tracker.example.com/ls/click")


async def test_redirect_to_private_hostname_is_refused_mid_chain(resolver):
    resolver["tracker.example.com"] = [PUBLIC_IP]
    resolver["intranet.corp"] = ["192.168.1.10"]
    routes = {
        "https://tracker.example.com/ls/click": httpx.Response(
            302, headers={"location": "https://intranet.corp/admin"}
        ),
    }
    async with _mock_client(routes) as client:
        with pytest.raises(BlockedUrlError, match="192.168.1.10"):
            await client.get("https://tracker.example.com/ls/click")


async def test_redirect_between_public_hosts_still_works(resolver):
    resolver["tracker.example.com"] = [PUBLIC_IP]
    resolver["files.example.com"] = [PUBLIC_IP]
    routes = {
        "https://tracker.example.com/ls/click": httpx.Response(
            302, headers={"location": "https://files.example.com/report.csv"}
        ),
        "https://files.example.com/report.csv": httpx.Response(200, content=b"a,b\n1,2\n"),
    }
    async with _mock_client(routes) as client:
        response = await client.get("https://tracker.example.com/ls/click")
    assert response.status_code == 200
    assert response.content == b"a,b\n1,2\n"
    assert len(response.history) == 1  # the redirect really was followed
