"""SSRF guard for every request we send to an address that came from the outside world.

The analysed URL is typed in by any visitor, and the pages it links to (depth 1, 2, ...) are chosen by whoever wrote the
article. Without a guard, `http://169.254.169.254/...` (cloud metadata), `http://db:3306`, `http://localhost:8000/...` or
any service on the VPS's private network could be fetched by the backend and their content shown back in
`article.text_markdown`. Two layers:

1. `is_public_hostname` (no DNS, used by the API): IP literals that are not globally routable, `localhost`,
   single-label names (`db`, `backend`) and internal suffixes are refused before a job even starts.
2. `guard_request` (httpx request hook on the fetcher's own client): runs before EVERY request, including each redirect
   hop, resolves the host and refuses it when any address it resolves to is not public, or when the port is unusual.

Residual risk: DNS rebinding between our lookup and httpx's own connect. The port allowlist keeps internal services on
other ports out of reach even then.
"""
from __future__ import annotations

import ipaddress
import socket
from typing import Callable, Iterable

import httpx

ALLOWED_PORTS = frozenset({80, 443, 8080, 8443})
_INTERNAL_SUFFIXES = (".localhost", ".local", ".internal", ".localdomain", ".home.arpa", ".lan", ".intranet", ".corp")

Resolver = Callable[[str, int], Iterable[str]]


class BlockedAddress(httpx.RequestError):
    """The target is not on the public internet. Subclass of httpx.HTTPError, so every existing `except httpx.HTTPError`
    turns it into the neutral `unreachable` status."""


def _ip(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        return ipaddress.ip_address(host.strip("[]").split("%", 1)[0])
    except ValueError:
        return None


def is_public_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(ip, ipaddress.IPv6Address):
        mapped = ip.ipv4_mapped or ip.sixtofour
        if mapped is not None:
            return is_public_ip(mapped)
    return ip.is_global and not ip.is_multicast


def _legacy_ipv4(host: str) -> bool:
    """`0x7f000001`, `2130706433`, `127.1`: forms inet_aton (and so some resolvers) still accept as IPv4."""
    try:
        socket.inet_aton(host)
    except (OSError, ValueError):
        return False
    return True


def is_public_hostname(host: str | None) -> bool:
    """Cheap, DNS-free check: False for names that can only point inside our own network."""
    if not host:
        return False
    host = host.lower().rstrip(".")
    ip = _ip(host)
    if ip is not None:
        return is_public_ip(ip)
    if _legacy_ipv4(host):
        return False                    # numeric shorthand: never a real site, often an SSRF trick
    if host == "localhost" or host.endswith(_INTERNAL_SUFFIXES):
        return False
    return "." in host                  # single-label names (`db`, `backend`, `web`) are docker / LAN hosts


def _default_resolver(host: str, port: int) -> list[str]:
    return [info[4][0] for info in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)]


def check_target(host: str | None, port: int | None, scheme: str = "https", resolver: Resolver | None = None) -> None:
    """Raise BlockedAddress unless every address `host` resolves to is public and the port is a web port."""
    if scheme not in ("http", "https"):
        raise BlockedAddress(f"scheme {scheme!r} is not allowed")
    port = port or (443 if scheme == "https" else 80)
    if port not in ALLOWED_PORTS:
        raise BlockedAddress(f"port {port} is not allowed")
    if not is_public_hostname(host):
        raise BlockedAddress(f"host {host!r} is not a public address")
    ip = _ip(host or "")
    addresses = [str(ip)] if ip is not None else None
    if addresses is None:
        try:
            addresses = list((resolver or _default_resolver)(host, port))
        except (OSError, UnicodeError) as exc:
            raise BlockedAddress(f"host {host!r} does not resolve") from exc
    if not addresses:
        raise BlockedAddress(f"host {host!r} does not resolve")
    for a in addresses:
        parsed = _ip(a)
        if parsed is None or not is_public_ip(parsed):
            raise BlockedAddress(f"host {host!r} resolves to a non-public address")


def make_request_hook(resolver: Resolver | None = None) -> Callable[[httpx.Request], None]:
    def guard_request(request: httpx.Request) -> None:
        try:
            check_target(request.url.host, request.url.port, request.url.scheme, resolver)
        except BlockedAddress as exc:
            raise BlockedAddress(str(exc), request=request) from None
    return guard_request


guard_request = make_request_hook()
