"""SSRF guard for agent-facing URL fetches.

No server in this bundle fetches a model-supplied URL today (inbound email,
and its links, moved to Elcano Explorer); the guard stays for the next one. A
tool that fetches URLs taken from EMAIL BODIES handles attacker-controlled
input for anyone who can email the mailbox. Without a host check such a tool
is an SSRF primitive: a link to
``http://169.254.169.254/latest/meta-data/`` reads cloud metadata, a link to
``http://10.x.x.x/`` reaches internal services, and a public host can 302 to
either. This module centralizes the policy such a fetcher applies:

- http/https only (checked again on every hop — a redirect can change scheme);
- the host of EVERY request must resolve exclusively to public, globally
  routable addresses. Loopback, RFC 1918 private, link-local (including the
  169.254.169.254 cloud metadata endpoint), CGNAT (100.64.0.0/10, where some
  providers park metadata too), IPv6 ULA, multicast, reserved space (which
  covers NAT64's 64:ff9b::/96 embedding trick), and IPv4-mapped IPv6 forms of
  all of the above are refused. If a hostname resolves to a MIX of public and
  non-public addresses, it is refused — the resolver, not us, would pick which
  one the connect uses.

Redirect coverage: wire :func:`request_hook` into the fetching client's
``event_hooks={"request": [...]}``. httpx invokes request hooks once per hop
inside ``_send_handling_redirects`` (verified against httpx 0.28.1), BEFORE
the hop's request is sent, so a public URL that redirects to an internal
address is refused at the redirect, with no request made.

Known residual risk (accepted): the hook resolves DNS separately from the
connect httpx performs right after it, so a DNS-rebinding attacker flipping
records between the two lookups could still race it. Closing that requires
pinning the resolved IP into a custom transport; for this surface the guard's
job is stopping the straightforward internal-address and redirect tricks —
the sandbox's egress policy remains the outer wall.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket

import httpx

_ALLOWED_SCHEMES = ("http", "https")


class BlockedUrlError(ValueError):
    """A URL failed the SSRF policy. Message is agent-facing and actionable."""


def _is_public(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """True only for plain, globally routable unicast addresses.

    IPv4-mapped IPv6 (``::ffff:10.0.0.1``) is unwrapped first so the mapped
    form cannot smuggle a private IPv4 past the IPv6 checks on older Pythons
    (current CPython already folds these into is_global, but the unwrap keeps
    the check self-contained and the error message concrete). ``is_reserved``
    is rejected on top of ``is_global`` because NAT64's 64:ff9b::/96 — which
    embeds an arbitrary IPv4 address — reports is_global=True.
    """
    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None:
        ip = mapped
    return ip.is_global and not ip.is_multicast and not ip.is_reserved


async def _resolve_host(host: str) -> list[str]:
    """Resolve a hostname to all its addresses (A + AAAA) without blocking
    the event loop. Raises BlockedUrlError when resolution fails — an
    unresolvable host cannot be vetted, so it cannot be fetched."""
    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise BlockedUrlError(f"SECURITY: URL host {host!r} did not resolve: {exc}") from exc
    return [info[4][0] for info in infos]


async def assert_public_url(url: str | httpx.URL) -> None:
    """Raise BlockedUrlError unless ``url`` is http(s) to a public host.

    Every address the host resolves to must pass :func:`_is_public`; IP
    literals are checked directly (getaddrinfo would just echo them back).
    """
    parsed = httpx.URL(str(url))
    if parsed.scheme not in _ALLOWED_SCHEMES:
        raise BlockedUrlError(f"SECURITY: URL scheme {parsed.scheme!r} is not allowed. Only http/https supported.")
    host = parsed.host
    if not host:
        raise BlockedUrlError("SECURITY: URL has no host.")
    try:
        addresses: list[str] = [str(ipaddress.ip_address(host))]
    except ValueError:
        addresses = await _resolve_host(host)
    for addr in addresses:
        ip = ipaddress.ip_address(addr)
        if not _is_public(ip):
            raise BlockedUrlError(
                f"SECURITY: refusing to fetch {str(parsed)!r} — host {host!r} resolves to "
                f"the non-public address {addr}. Links from email bodies may only point at "
                "publicly routable hosts."
            )


async def request_hook(request: httpx.Request) -> None:
    """httpx request event hook: vet every request the client is about to
    send, including each redirect hop. Attach via
    ``httpx.AsyncClient(event_hooks={"request": [url_guard.request_hook]})``."""
    await assert_public_url(request.url)
