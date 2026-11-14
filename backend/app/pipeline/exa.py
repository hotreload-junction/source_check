"""Resolve a named-but-unlinked source (e.g. "the Finnish Ministry of Defence") to a URL via Exa (DECISIONS Q7)."""
from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

log = logging.getLogger("citetrace.exa")
EXA_URL = "https://api.exa.ai/search"


@dataclass
class NamedSourceHit:
    url: str
    title: str | None
    published: str | None
    attribution: str


def resolve_named_source(attribution: str, claim_text: str, *, api_key: str,
                         client: httpx.Client | None = None, timeout: float = 20.0,
                         exclude_domain: str | None = None) -> NamedSourceHit | None:
    """Best Exa match for an attribution in the context of its claim, or None (never raises).

    `exclude_domain`: skip results from the article's own domain (that would be a self-citation, not a source).
    """
    if not api_key or not attribution.strip():
        return None
    query = f"{attribution.strip()} {claim_text.strip()}"[:350]
    try:
        r = (client or httpx).post(EXA_URL, json={"query": query, "numResults": 3, "type": "auto"},
                                   headers={"x-api-key": api_key}, timeout=timeout)
        if r.status_code != 200:
            log.warning("exa http %s", r.status_code)
            return None
        results = r.json().get("results") or []
    except (httpx.HTTPError, ValueError) as exc:
        log.warning("exa failed: %s", type(exc).__name__)
        return None
    from ..fetch.urls import is_same_domain

    for item in results:
        if item.get("url") and not (exclude_domain and is_same_domain(item["url"], exclude_domain)):
            return NamedSourceHit(item["url"], item.get("title"), item.get("publishedDate"), attribution)
    return None
