"""Resolve a named-but-unlinked source (e.g. "the Finnish Ministry of Defence") to candidate URLs via Exa.

Measured behaviour (live, Nov 2026): Exa mostly returns OTHER OUTLETS reporting the same statement. It finds the
original for organisations that publish reports (amnesty.org) and sometimes for ministries, rarely for spoken
statements (the original is a video). So: ask for several candidates, rank the ones that look first-hand first,
and let the verify step decide original vs secondary from the page itself (verify.py `source_type`).
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta

import httpx

from ..fetch.urls import is_same_domain, normalize_url, registrable_domain

log = logging.getLogger("citetrace.exa")
EXA_URL = "https://api.exa.ai/search"
NUM_RESULTS = 8
_OFFICIAL = re.compile(r"(^|\.)(gov|mil|europa\.eu|un\.org|int|gouv\.fr|gob\.[a-z]{2}|bund\.de|admin\.ch)(\.|$)|\.gov\.[a-z]{2}$|president\.|kremlin\.|parlament|parliament|ministry|ministerstvo|minister", re.I)
_STOP = {"the", "of", "and", "a", "an", "for", "in", "on", "to", "by", "said", "says", "report", "statement",
         "ministry", "department", "office", "according", "spokesperson", "spokesman", "official", "officials"}


@dataclass
class NamedSourceHit:
    url: str
    title: str | None
    published: str | None
    attribution: str
    primary_hint: int = 0        # heuristic only (0 = looks like a news outlet); verify.py makes the real call


def primary_hint(url: str, attribution: str) -> int:
    """2 = domain carries the attributed name (amnesty.org for "Amnesty International"), 1 = official-looking
    domain, 0 = anything else (news outlet). Only used to ORDER candidates, never to trust them."""
    host = registrable_domain(url)
    label = host.split(".")[0].lower()
    tokens = {t for t in re.findall(r"[a-z0-9]{4,}", attribution.lower()) if t not in _STOP}
    if any(t in label or label in t for t in tokens if len(label) >= 4):
        return 2
    if _OFFICIAL.search(url.split("/")[2].lower() if "//" in url else host):
        return 1
    return 0


def find_candidates(attribution: str, claim_text: str, *, api_key: str, client: httpx.Client | None = None,
                    timeout: float = 20.0, exclude_url: str | None = None,
                    around: datetime | None = None, limit: int = 3) -> list[NamedSourceHit]:
    """Up to `limit` candidates for the attribution, first-hand-looking ones first. Never raises.

    The article itself and everything on its domain are excluded (a self-citation is not a source).
    `around` = article publication date: restricts results to a window around it (older pages are never the
    statement the article reports on).
    """
    if not api_key or not attribution.strip():
        return []
    body: dict = {"query": f"{attribution.strip()} {claim_text.strip()}"[:350], "numResults": NUM_RESULTS, "type": "auto"}
    if around:
        body["startPublishedDate"] = (around - timedelta(days=14)).strftime("%Y-%m-%dT00:00:00.000Z")
        body["endPublishedDate"] = (around + timedelta(days=2)).strftime("%Y-%m-%dT00:00:00.000Z")
    try:
        r = (client or httpx).post(EXA_URL, json=body, headers={"x-api-key": api_key}, timeout=timeout)
        if r.status_code != 200:
            log.warning("exa http %s", r.status_code)
            return []
        results = r.json().get("results") or []
    except (httpx.HTTPError, ValueError) as exc:
        log.warning("exa failed: %s", type(exc).__name__)
        return []
    own = normalize_url(exclude_url) if exclude_url else None
    hits: list[NamedSourceHit] = []
    for item in results:
        url = item.get("url")
        if not url or (exclude_url and (is_same_domain(url, exclude_url) or _safe_norm(url) == own)):
            continue
        hits.append(NamedSourceHit(url, item.get("title"), item.get("publishedDate"), attribution,
                                   primary_hint(url, attribution)))
    hits.sort(key=lambda h: -h.primary_hint)   # stable: Exa's relevance order within a tier
    return hits[:limit]


def resolve_named_source(attribution: str, claim_text: str, *, api_key: str, client: httpx.Client | None = None,
                         timeout: float = 20.0, exclude_domain: str | None = None) -> NamedSourceHit | None:
    """Best single candidate (kept for callers that want one)."""
    hits = find_candidates(attribution, claim_text, api_key=api_key, client=client, timeout=timeout,
                           exclude_url=exclude_domain, limit=1)
    return hits[0] if hits else None


def _safe_norm(url: str) -> str | None:
    try:
        return normalize_url(url)
    except ValueError:
        return None
