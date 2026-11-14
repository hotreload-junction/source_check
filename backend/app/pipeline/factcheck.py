"""Fact-check agent: for a claim the article gives NO usable source for, ONE Exa Agent run looks for pages that either
CONFIRM or REFUTE it (leo's decision; Exa Agent API through the `exa-py` SDK: `exa.agent.runs`).

Used for (a) claims without any source and (b) attributed claims ("according to journalists ...") whose original
statement could not be located. The question is no longer "who said it" but "does it hold": what do registries, filings,
official statements and independent reporting say about the same entity at the time the claim names?

The agent is ONE MORE SOURCE OF URLS, like pipeline/exa_agent.py: everything it returns goes through the same fetch +
verify step (quote first, fuzzy-checked against the fetched page). Its `stance` / `kind` labels and its quotes are never
trusted; a refuting page only counts when the verify model quotes the conflicting passage verbatim and the code finds it
in the page (AGENTS.md section 7). Never raises: any problem is a neutral, empty result.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from ..fetch.urls import is_same_domain
from .exa_agent import _slot

log = logging.getLogger("citetrace.factcheck")

POLL_INTERVAL_MS = 3000
CREATE_RETRIES = 4

PROMPT = """You are fact-checking ONE claim from a news article published on {date}. The article gives no usable source for it.{attribution}

CLAIM: {claim}

PARAGRAPH it appears in: {context}

Decide whether the claim holds by finding the best evidence that CONFIRMS it or REFUTES it. Both outcomes are equally valuable: do not look only for support.
- Check the concrete facts: who, what, when, how much. For ownership, sale, acquisition, appointment or status claims, find what sources say about the situation AT THE TIME the claim names (and shortly after): who owned or held it then. A page that names a different owner, buyer, date, figure or outcome for that time REFUTES the claim, even if it never mentions the claim itself.
- Primary sources first: registries and filings, company or institution press releases, government and agency data, court records, official statements. Then independent outlets with their OWN reporting (not copies of one wire text).
- The claim may splice together facts that belong to different matters. If so, find sources that show what each part really belongs to.
- There is no date limit: older pages are fine when they establish the facts.
- Never return the article itself or other pages of {domain}.
For every source give: the url; "stance" = "confirms" or "refutes" (what that page says about the claim); "kind" = "primary" or "independent"; and a VERBATIM quote of at most 300 characters copied from that page that states the relevant fact (null if you cannot quote it). Do not invent URLs or quotes. If the claim can be neither confirmed nor refuted, set found to false and return no sources."""

SCHEMA = {"type": "object", "properties": {
    "found": {"type": "boolean"},
    "sources": {"type": "array", "maxItems": 4, "items": {"type": "object", "properties": {
        "url": {"type": "string"},
        "stance": {"type": "string", "enum": ["confirms", "refutes"]},
        "kind": {"type": "string", "enum": ["primary", "independent"]},
        "quote": {"type": ["string", "null"]}}, "required": ["url", "stance"]}}},
    "required": ["found", "sources"]}


@dataclass
class FactHit:
    url: str
    stance: str = "confirms"           # what the AGENT says about the claim; never trusted (verify.py decides)
    kind: str = "independent"          # primary | independent: the agent's label, never trusted
    quote: str | None = None


@dataclass
class FactResult:
    hits: list[FactHit] = field(default_factory=list)
    found: bool | None = None
    cost: float = 0.0
    secs: float = 0.0
    status: str = ""                   # completed | failed | cancelled | timeout | unavailable | disabled
    cached: bool = False


def build_query(claim_text: str, context: str, article_domain: str, article_date, *, who: str | None = None,
                venue: str | None = None, occasion: str | None = None) -> str:
    date = article_date.date().isoformat() if article_date else "recently"
    attribution = ""
    if who:
        bits = [f"it is attributed to: {who}"] + ([f"named venue: {venue}"] if venue else []) + ([f"occasion: {occasion}"] if occasion else [])
        attribution = ("\nThe article presents it as coming from someone ("
                       + "; ".join(bits) + "). The original statement could not be found, so check the FACTS themselves.")
    return PROMPT.format(date=date, claim=claim_text[:600], context=context[:1500], domain=article_domain, attribution=attribution)


def make_client(api_key: str):
    from exa_py import Exa

    return Exa(api_key, timeout=30)


def _create(exa, query: str, effort: str, retries: int = CREATE_RETRIES):
    """exa-py raises ValueError("Request failed with status code 429: ...") when the start rate is exceeded: wait, retry."""
    for attempt in range(retries):
        try:
            return exa.agent.runs.create(query=query, effort=effort, output_schema=SCHEMA)
        except ValueError as exc:
            if "status code 429" in str(exc) and attempt < retries - 1:
                time.sleep(min(2.0 * (attempt + 1), 8.0))
                continue
            raise


def run_factcheck(query: str, *, api_key: str, article_url: str, effort: str = "low", timeout_s: float = 80.0,
                  concurrency: int = 8, exa=None, cache=None) -> FactResult:
    """One agent run for one claim. `exa`: an exa_py.Exa (tests pass a fake). Never raises."""
    from .. import cache as cachemod

    if not api_key and exa is None:
        return FactResult(status="disabled")
    step_cache = cache if cache is not None else cachemod.get_default()
    ckey = cachemod.make_key("factcheck", {"query": query, "effort": effort})
    hit = step_cache.get("factcheck", ckey)
    if hit is not None:
        return FactResult([FactHit(**h) for h in hit["hits"]], hit.get("found"), 0.0, 0.0, hit.get("status", "completed"), cached=True)
    t0 = time.monotonic()
    run_id = None
    try:
        exa = exa or make_client(api_key)
        with _slot(concurrency):
            run = _create(exa, query, effort)
            run_id = run.id
            run = exa.agent.runs.poll_until_finished(run_id, poll_interval=POLL_INTERVAL_MS, timeout_ms=int(timeout_s * 1000))
    except TimeoutError:
        log.info("factcheck run %s still going after %.0fs, dropped", run_id, time.monotonic() - t0)
        _cancel(exa, run_id)                       # stop paying for a run nobody waits for
        return FactResult(status="timeout", secs=time.monotonic() - t0)       # transient: not cached
    except Exception as exc:                       # network, 4xx/5xx, SDK change: neutral
        log.warning("factcheck run failed: %s %s", type(exc).__name__, str(exc)[:120])
        return FactResult(status="unavailable", secs=time.monotonic() - t0)
    secs = time.monotonic() - t0
    cost = float(getattr(getattr(run, "cost_dollars", None), "total", 0) or 0)
    if run.status != "completed":
        res = FactResult(status=str(run.status), cost=cost, secs=secs)
        step_cache.put("factcheck", ckey, {"hits": [], "found": None, "status": res.status}, cachemod.FAILURE_TTL_S // 12)
        return res
    structured = (getattr(run.output, "structured", None) if run.output is not None else None) or {}
    hits: dict[str, FactHit] = {}
    for src in (structured.get("sources") if isinstance(structured, dict) else None) or []:
        url = src.get("url") if isinstance(src, dict) else None
        if not (isinstance(url, str) and url.startswith(("http://", "https://"))) or is_same_domain(url, article_url) or url in hits:
            continue
        hits[url] = FactHit(url, src.get("stance") if src.get("stance") in ("confirms", "refutes") else "confirms",
                            src.get("kind") if src.get("kind") in ("primary", "independent") else "independent",
                            src.get("quote") if isinstance(src.get("quote"), str) else None)
    res = FactResult(list(hits.values()), structured.get("found") if isinstance(structured, dict) else None, cost, secs, "completed")
    step_cache.put("factcheck", ckey, {"hits": [h.__dict__ for h in res.hits], "found": res.found, "status": "completed"})
    return res


def _cancel(exa, run_id: str | None) -> None:
    if exa is None or not run_id:
        return
    try:
        exa.agent.runs.cancel(run_id)
    except Exception as exc:                       # best effort
        log.info("could not cancel run %s: %s", run_id, type(exc).__name__)
