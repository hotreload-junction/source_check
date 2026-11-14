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
                    around: datetime | None = None, limit: int = 3,
                    include_domains: list[str] | None = None, query: str | None = None, cache=None) -> list[NamedSourceHit]:
    """Up to `limit` candidates for the attribution, first-hand-looking ones first. Never raises.

    The article itself and everything on its domain are excluded (a self-citation is not a source).
    `around` = article publication date: restricts results to a window around it (older pages are never the
    statement the article reports on).
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
    if around and not include_domains:
        body["startPublishedDate"] = (around - timedelta(days=14)).strftime("%Y-%m-%dT00:00:00.000Z")
        body["endPublishedDate"] = (around + timedelta(days=2)).strftime("%Y-%m-%dT00:00:00.000Z")
    from .. import cache as cachemod

    step_cache = cache if cache is not None else cachemod.get_default()
    ckey = cachemod.make_key("exa", body)       # the search index moves: freeze what a finished analysis saw
    results = step_cache.get("exa", ckey)
    if results is None:
        try:
            r = (client or httpx).post(EXA_URL, json=body, headers={"x-api-key": api_key}, timeout=timeout)
            if r.status_code != 200:
                log.warning("exa http %s", r.status_code)
                step_cache.put("exa", ckey, [], cachemod.FAILURE_TTL_S // 12)   # 30 min: replay the failure, retry soon
                return []
            results = [{k: it.get(k) for k in ("url", "title", "publishedDate")} for it in (r.json().get("results") or [])]
        except (httpx.HTTPError, ValueError) as exc:
            log.warning("exa failed: %s", type(exc).__name__)
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


OFFICIAL_SEARCH_PROMPT = """A news article attributes a claim to someone. Help find the ORIGINAL first-hand material.
Return JSON {"domains": [...], "query": "..."}:
- "domains": up to 3 website domains where that person or organisation publishes its OWN first-hand material (press releases, official statements, transcripts, reports). Only domains you are confident exist; [] if unknown or the source is a private individual without an official site.
- "query": 3 to 8 plain keywords naming the specific event or topic of the claim (no full sentence, no quotation marks), e.g. "Starshield speed restriction occupied territory"."""
_DOMAIN_RE = re.compile(r"^(?:[a-z0-9-]+\.)+[a-z]{2,}$")


def suggest_official_search(attribution: str, claim_text: str, *, llm, model: str) -> tuple[list[str], str | None]:
    """Cheap-model hint for where the original lives and how to search for it. A wrong guess is harmless: Exa
    then returns nothing, or a page the verify step rejects. Never raises."""
    from ..llm import LLMError

    try:
        res = llm.chat_json(model, OFFICIAL_SEARCH_PROMPT,
                            f"Attributed to: {attribution}\nClaim: {claim_text[:300]}", max_tokens=1000)
    except LLMError as exc:
        log.info("official-search suggestion failed: %s", exc)
        return [], None
    raw = res.data.get("domains")
    out: list[str] = []
    for d in raw if isinstance(raw, list) else []:
        d = str(d).strip().lower().removeprefix("https://").removeprefix("http://").removeprefix("www.").split("/")[0]
        labels = d.split(".")
        too_broad = len(labels) == 2 and labels[0] in ("gov", "com", "co", "org", "net", "edu", "ac", "mil")   # gov.ua
        if _DOMAIN_RE.match(d) and d not in out and not too_broad:
            out.append(d)
    q = res.data.get("query")
    q = re.sub(r"\s+", " ", q.replace('"', " ")).strip() if isinstance(q, str) and q.strip() else None
    return out[:3], q


def suggest_official_domains(attribution: str, claim_text: str, *, llm, model: str) -> list[str]:
    return suggest_official_search(attribution, claim_text, llm=llm, model=model)[0]
