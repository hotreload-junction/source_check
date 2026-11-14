"""Resolve a named-but-unlinked source (e.g. "the Finnish Ministry of Defence") to candidate URLs via Exa.

Measured behaviour (live, Nov 2026): Exa mostly returns OTHER OUTLETS reporting the same statement. It finds the
original for organisations that publish reports (amnesty.org) and sometimes for ministries, rarely for spoken
statements (the original is a video). So: ask for several candidates, rank the ones that look first-hand first,
and let the verify step decide original vs secondary from the page itself (verify.py `source_type`).
"""
from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime

import httpx

from ..fetch.urls import is_same_domain, normalize_url, registrable_domain

log = logging.getLogger("citetrace.exa")
EXA_URL = "https://api.exa.ai/search"
# One analysis can fire 40+ searches (up to 7 per attributed claim, all claims at once): measured live, that gets HTTP 429.
# At most this many requests are in flight in the whole process, and a 429 is retried after a pause, never cached.
EXA_CONCURRENCY = 4
EXA_RETRIES = 3
_exa_slots = threading.BoundedSemaphore(EXA_CONCURRENCY)
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
                    window: tuple[datetime, datetime] | None = None, limit: int = 3,
                    include_domains: list[str] | None = None, query: str | None = None, cache=None,
                    include_text: str | None = None) -> list[NamedSourceHit]:
    """Up to `limit` candidates for the attribution, first-hand-looking ones first. Never raises.

    The article itself and everything on its domain are excluded (a self-citation is not a source).
    `window`: only pages published in it (the occasion the article describes, see evidence.py).
    `include_text`: a verbatim phrase of at most 5 words that every result must contain (Exa `includeText`): the
    quickest way to find the page where a quoted sentence was actually said.
    """
    if not api_key or not attribution.strip():
        return []
    # Restricted to official domains, a SHORT query works better (measured on president.gov.ua)
    qlen = 160 if include_domains else 350
    text = query.strip() if query else f"{attribution.strip()} {claim_text.strip()}"
    body: dict = {"query": text[:qlen], "numResults": NUM_RESULTS, "type": "auto"}
    if include_domains:
        body["includeDomains"] = include_domains
    # Not for official-site searches: measured live, Exa's date filter dropped president.gov.ua's matching page
    # (11 Nov) from a window that contained it. The domain restriction already narrows the search enough.
    if window and not include_domains and not include_text:
        body["startPublishedDate"] = window[0].strftime("%Y-%m-%dT00:00:00.000Z")
        body["endPublishedDate"] = window[1].strftime("%Y-%m-%dT00:00:00.000Z")
    if include_text:
        body["includeText"] = [include_text]
    from .. import cache as cachemod

    step_cache = cache if cache is not None else cachemod.get_default()
    ckey = cachemod.make_key("exa", body)       # the search index moves: freeze what a finished analysis saw
    results = step_cache.get("exa", ckey)
    if results is None:
        results = _post_with_backoff(body, api_key, client, timeout)
        if results is None:        # rate limited / network trouble: transient, never frozen into the cache
            return []
        if results == "failed":
            step_cache.put("exa", ckey, [], cachemod.FAILURE_TTL_S // 12)   # 30 min: replay a real failure, retry soon
            return []
        step_cache.put("exa", ckey, results)
    own = normalize_url(exclude_url) if exclude_url else None
    hits: list[NamedSourceHit] = []
    for item in results:
        url = item.get("url")
        if not url or (exclude_url and (is_same_domain(url, exclude_url) or _safe_norm(url) == own)):
            continue
        hint = 3 if include_domains else primary_hint(url, attribution)   # the suggested official site wins
        hits.append(NamedSourceHit(url, item.get("title"), item.get("publishedDate"), attribution, hint))
    hits.sort(key=lambda h: -h.primary_hint)   # stable: Exa's relevance order within a tier
    return hits[:limit]


def _post_with_backoff(body: dict, api_key: str, client, timeout: float):
    """-> list of results | "failed" (a definite error answer) | None (still rate limited / unreachable: transient)."""
    for attempt in range(EXA_RETRIES):
        try:
            with _exa_slots:
                r = (client or httpx).post(EXA_URL, json=body, headers={"x-api-key": api_key}, timeout=timeout)
        except (httpx.HTTPError, ValueError) as exc:
            log.warning("exa failed: %s", type(exc).__name__)
            return None
        if r.status_code == 429:
            pause = min(float(r.headers.get("retry-after", 0) or 0) or 1.5 * (attempt + 1), 6.0)
            log.info("exa rate limited, retry in %.1fs", pause)
            time.sleep(pause)
            continue
        if r.status_code != 200:
            log.warning("exa http %s", r.status_code)
            return "failed"
        try:
            return [{k: it.get(k) for k in ("url", "title", "publishedDate")} for it in (r.json().get("results") or [])]
        except ValueError:
            return "failed"
    log.warning("exa still rate limited after %d tries", EXA_RETRIES)
    return None


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
