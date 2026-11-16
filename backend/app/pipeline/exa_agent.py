"""Exa Agent (POST https://api.exa.ai/agent/runs): find where a claim comes from, for claims WITHOUT a link.

Measured against our own search on 36 real claims (backend/experiments/exa_agent_comparison.md): equal on originals
(12-13 of 36, but different ones), far better at finding confirming pages (83% vs 67% with secondary sources; 64% of
the URLs it returns verify, vs 14-27%), and cheaper than our 7-search fan-out at effort `low`. It is used as ONE MORE
SOURCE OF URLS: everything it returns goes through the same fetch + verify step as any candidate. It over-labels
"original" (40 claimed, 14 verified) and 6-16% of its quotes are stitched, so none of its labels or quotes is trusted.

Async API: create a run, poll `GET /agent/runs/{id}` until it is completed / failed / cancelled. A run that is still
going after `timeout_s` is dropped (neutral). Results are stored in the step cache, so a finished analysis replays
without paying again.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field

import httpx

from ..fetch.urls import is_same_domain

log = logging.getLogger("citetrace.exa_agent")
AGENT_URL = "https://api.exa.ai/agent/runs"
POLL_INTERVAL_S = 4.0
_slots: threading.BoundedSemaphore | None = None
_slots_size = 0
_slots_lock = threading.Lock()

PROMPT_ATTRIBUTED = """You are helping to fact-check a news article published on {date}. The article reports the statement below but gives no link to it.

CLAIM: {claim}

PARAGRAPH it appears in: {context}
{hints}
Find where this statement was actually made or published. Prefer the ORIGINAL source: the interview transcript or video page of the outlet that is named, an official statement or press release, a report or document itself, a court, parliament or agency record, the speaker's own post. If the original cannot be found, find independent outlets that quote the same statement. Never return the article itself or other pages of {domain}. For every source give the URL, whether it is the original or only corroborating, and a VERBATIM quote of at most 300 characters copied from that page that shows the statement (null if you cannot quote it). Do not invent URLs or quotes. If nothing can be found, say so."""

PROMPT_UNSOURCED = """You are helping to fact-check a news article published on {date}. The article makes the claim below WITHOUT naming any source.

CLAIM: {claim}

PARAGRAPH it appears in: {context}

Find the best sources for this claim: primary sources first (official statements, agency or government data, reports, court or parliament records, company filings, transcripts), otherwise independent outlets with their OWN reporting (not copies of the same wire text). Only return pages that actually state what the claim says. Never return the article itself or other pages of {domain}. For every source give the URL, whether it is a primary source ("original") or independent reporting ("corroborating"), and a VERBATIM quote of at most 300 characters copied from that page that supports the claim (null if you cannot quote it). Do not invent URLs or quotes. If nothing supports the claim, say so."""

SCHEMA = {"type": "object", "properties": {
    "found": {"type": "boolean"},
    "sources": {"type": "array", "maxItems": 4, "items": {"type": "object", "properties": {
        "url": {"type": "string"}, "kind": {"type": "string", "enum": ["original", "corroborating"]},
        "quote": {"type": ["string", "null"]}}, "required": ["url", "kind"]}}},
    "required": ["found", "sources"]}


@dataclass
class AgentHit:
    url: str
    kind: str = "corroborating"        # what the AGENT says; never trusted (verify.py decides)
    quote: str | None = None


@dataclass
class AgentResult:
    hits: list[AgentHit] = field(default_factory=list)
    found: bool | None = None
    cost: float = 0.0
    secs: float = 0.0
    status: str = ""                   # completed | failed | cancelled | timeout | unavailable | disabled
    cached: bool = False


def _slot(size: int) -> threading.BoundedSemaphore:
    global _slots, _slots_size
    with _slots_lock:
        if _slots is None or _slots_size != size:
            _slots, _slots_size = threading.BoundedSemaphore(max(size, 1)), size
        return _slots


def build_body(claim_text: str, context: str, article_url: str, article_domain: str, article_date, *, effort: str,
               speaker: str | None = None, venue: str | None = None, occasion: str | None = None,
               attributed: bool = True) -> dict:
    date = article_date.date().isoformat() if article_date else "recently"
    if attributed:
        hints = "".join(f"{k}: {v}\n" for k, v in (("SPEAKER", speaker), ("VENUE named by the article", venue),
                                                  ("OCCASION", occasion)) if v)
        hints = ("\nWhat the article says about it:\n" + hints) if hints else ""
        query = PROMPT_ATTRIBUTED.format(date=date, claim=claim_text[:600], context=context[:1500], domain=article_domain, hints=hints)
    else:
        query = PROMPT_UNSOURCED.format(date=date, claim=claim_text[:600], context=context[:1500], domain=article_domain)
    return {"query": query, "effort": effort, "outputSchema": SCHEMA}


def find_sources(body: dict, *, api_key: str, article_url: str, timeout_s: float = 80.0, concurrency: int = 8,
                 client: httpx.Client | None = None, cache=None) -> AgentResult:
    """Run the agent for one claim. Never raises: any problem is a neutral, empty result."""
    from .. import cache as cachemod

    if not api_key:
        return AgentResult(status="disabled")
    step_cache = cache if cache is not None else cachemod.get_default()
    ckey = cachemod.make_key("agent", body)
    hit = step_cache.get("agent", ckey)
    if hit is not None:
        return AgentResult([AgentHit(**h) for h in hit["hits"]], hit.get("found"), 0.0, 0.0, hit.get("status", "completed"), cached=True)
    headers = {"x-api-key": api_key, "Content-Type": "application/json"}
    own_client = client is None              # a client made here is closed here (one per claim used to leak sockets)
    http = client or httpx.Client(timeout=30)
    try:
        return _run(http, body, headers, api_key=api_key, article_url=article_url, timeout_s=timeout_s,
                    concurrency=concurrency, step_cache=step_cache, ckey=ckey)
    except Exception as exc:                 # "never raises": an unexpected answer shape is a neutral, empty result
        log.warning("exa agent run failed: %s", type(exc).__name__)
        return AgentResult(status="unavailable")
    finally:
        if own_client:
            http.close()


def _run(http: httpx.Client, body: dict, headers: dict, *, api_key: str, article_url: str, timeout_s: float,
         concurrency: int, step_cache, ckey: str) -> AgentResult:
    from .. import cache as cachemod

    t0 = time.monotonic()
    with _slot(concurrency):
        run_id = _create(http, body, headers)
        if run_id is None:
            return AgentResult(status="unavailable", secs=time.monotonic() - t0)
        run = None
        while time.monotonic() - t0 < timeout_s:
            time.sleep(POLL_INTERVAL_S)
            try:
                run = http.get(f"{AGENT_URL}/{run_id}", headers=headers, timeout=20).json()
            except (httpx.HTTPError, ValueError):
                continue
            if isinstance(run, dict) and run.get("status") in ("completed", "failed", "cancelled"):
                break
            run = None
    secs = time.monotonic() - t0
    if run is None:
        log.info("exa agent run %s still running after %.0fs, dropped", run_id, secs)
        return AgentResult(status="timeout", secs=secs)       # transient: not cached
    cost = float((run.get("costDollars") or {}).get("total", 0) or 0)
    if run.get("status") != "completed":
        res = AgentResult(status=run.get("status", "failed"), cost=cost, secs=secs)
        step_cache.put("agent", ckey, {"hits": [], "found": None, "status": res.status}, cachemod.FAILURE_TTL_S // 12)
        return res
    out = run.get("output") if isinstance(run.get("output"), dict) else {}
    structured = out.get("structured") if isinstance(out.get("structured"), dict) else {}
    hits: dict[str, AgentHit] = {}
    for src in structured.get("sources") or []:
        url = src.get("url") if isinstance(src, dict) else None
        if isinstance(url, str) and _usable(url, article_url) and url not in hits:
            hits[url] = AgentHit(url, src.get("kind") if src.get("kind") in ("original", "corroborating") else "corroborating",
                                 src.get("quote") if isinstance(src.get("quote"), str) else None)
    for grounding in out.get("grounding") or []:           # cited pages the agent did not list itself
        for c in (grounding.get("citations") or [] if isinstance(grounding, dict) else []):
            url = c.get("url") if isinstance(c, dict) else None
            if isinstance(url, str) and _usable(url, article_url) and url not in hits:
                hits[url] = AgentHit(url)
    res = AgentResult(list(hits.values()), structured.get("found"), cost, secs, "completed")
    step_cache.put("agent", ckey, {"hits": [h.__dict__ for h in res.hits], "found": res.found, "status": "completed"})
    return res


def _usable(url: str, article_url: str) -> bool:
    return url.startswith(("http://", "https://")) and not is_same_domain(url, article_url)


def _create(http: httpx.Client, body: dict, headers: dict) -> str | None:
    for attempt in range(4):
        try:
            r = http.post(AGENT_URL, json=body, headers=headers, timeout=30)
        except httpx.HTTPError as exc:
            log.warning("exa agent create failed: %s", type(exc).__name__)
            return None
        if r.status_code == 429:                  # start rate / concurrency limit: wait and try again
            time.sleep(min(2.0 * (attempt + 1), 8.0))
            continue
        if r.status_code != 200:
            log.warning("exa agent http %s: %s", r.status_code, r.text[:120])
            return None
        try:
            return r.json()["id"]
        except (ValueError, KeyError):
            return None
    return None
