"""Membership in a DOCUMENTED influence network (facts only; whether it fires a signal is decided in verdict.py).

A host counts when it equals a listed host or is a subdomain of one (`germany.news-pravda.com` matches the listed
`news-pravda.com`). Never by registrable domain: the list contains hosts like `lenta.kharkiv.ua`, and the rest of
`kharkiv.ua` is unrelated. The lists are published by government / research teams (known_networks.py names the
source and date); nothing here is inferred, so a host that is not listed says nothing either way.
"""
from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit

from .known_networks import PORTAL_KOMBAT_HOSTS, PORTAL_KOMBAT_SOURCE

NETWORKS: tuple[tuple[str, frozenset[str], str], ...] = (
    ("Portal Kombat / Pravda network", PORTAL_KOMBAT_HOSTS, PORTAL_KOMBAT_SOURCE),
)


@dataclass(frozen=True)
class NetworkMatch:
    host: str          # the host that matched (as seen in the URL, without www.)
    network: str       # e.g. "Portal Kombat / Pravda network"
    listed_as: str     # the listed entry it matched
    list_source: str   # who published the list, and when


def _host(url_or_host: str) -> str:
    raw = url_or_host.strip()
    host = (urlsplit(raw).hostname or "") if "//" in raw else raw.split("/")[0].split(":")[0]
    host = host.lower().rstrip(".")
    return host[4:] if host.startswith("www.") else host


def match_network(url_or_host: str | None) -> NetworkMatch | None:
    if not url_or_host:
        return None
    host = _host(url_or_host)
    if not host:
        return None
    labels = host.split(".")
    candidates = [".".join(labels[i:]) for i in range(len(labels) - 1)]   # host itself, then each parent
    for name, hosts, source in NETWORKS:
        for c in candidates:
            if c in hosts:
                return NetworkMatch(host, name, c, source)
    return None
