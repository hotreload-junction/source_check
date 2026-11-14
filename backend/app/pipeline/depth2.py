"""M1: depth 2 = what the depth-1 sources themselves cite.

Code only, no extra LLM call. For every depth-1 source that was read, take the passage the claim rests on (the
verified evidence quote; else the best alignment of the claim text in the source) and the links in that passage's
sentence / paragraph, exactly like article claims (DECISIONS Q18). Those links are the depth-2 sources: they are
fetched (text + raw HTML) so the infrastructure step sees their domains, and every link that returns to the article's
domain is recorded as a candidate for the citation-circle signal (`signals/circles.py`, decided in `verdict.py`).

Why no verification of depth-2 sources: the contract has no verdict slot on a Source, so a second LLM verdict would
cost money and could not be shown or audited. The structure (who cites whom) and the infrastructure behind it are
facts, not judgements, which is what depth 2 is for.

Everything neutral stays neutral: unreachable, paywalled, social or self-cited pages are rows with a fetch_status and
never evidence against the article.
"""
from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from dataclasses import dataclass, field
from typing import Callable
from urllib.parse import urlsplit

from ..fetch import Fetcher
from ..fetch.urls import registrable_domain
from ..signals import SourcePage
from ..signals.circles import Citation
from .textutil import LinkRef, attach_links, build_plain, locate, to_markdown_span

log = logging.getLogger("citetrace.depth2")

MAX_LINKS_PER_SOURCE = 2       # depth-2 links followed per depth-1 source
MAX_FETCHES = 8                # depth-2 pages fetched per analysis (cost + latency)
QUOTE_ALIGN_THRESHOLD = 85.0   # locating the verified evidence quote in the source
CLAIM_ALIGN_THRESHOLD = 70.0   # no quote: locating the claim itself; weaker, so stricter than "anything similar"
DEADLINE_S = 25.0              # wall clock for all depth-2 fetches; the rest is "unreachable" (neutral)
Emit = Callable[[str, dict], None]


@dataclass
class Depth2Result:
    rows: list[dict] = field(default_factory=list)          # new contract Source rows (depth 2), "_html" is internal
    edges: list[dict] = field(default_factory=list)         # `cites` edges parent -> child (new or existing source)
    pages: list[SourcePage] = field(default_factory=list)   # for the infra step
    citations: list[Citation] = field(default_factory=list)


@dataclass
class _Candidate:
    parent: dict
    claim_id: str
    link: LinkRef
    domain: str
    existing: dict | None = None


def passage_links(source_text: str, quote: str | None, claim_text: str, source_url: str) -> list[LinkRef]:
    """Links in the sentence/paragraph of the passage the claim rests on in a source. [] when it cannot be located."""
    plain = build_plain(source_text)
    loc = locate(plain, quote, QUOTE_ALIGN_THRESHOLD) if quote else None
    if loc is None:
        loc = locate(plain, build_plain(claim_text).text, CLAIM_ALIGN_THRESHOLD)
    if loc is None:
        return []
    start, end = to_markdown_span(source_text, plain, loc[0], loc[1])
    return attach_links(source_text, start, end, source_url)


def _is_homepage(url: str) -> bool:
    """A bare homepage link is a credit / navigation link, not a citation of anything."""
    p = urlsplit(url)
    return p.path in ("", "/") and not p.query


def expand_sources(rows: list[dict], assessments, claims: list[dict], *, fetcher: Fetcher, article_url: str,
                   article_domain: str, emit: Emit | None = None, deadline_s: float = DEADLINE_S) -> Depth2Result:
    """Add the depth-2 sources to `rows` (the registry list, mutated: ids continue after the last source).

    `rows` are the depth-1 contract Source dicts; `assessments` the ClaimAssessments they came from (fetched text and
    the verified quote live there); `claims` the contract Claim dicts (which claim cites which source).
    """
    out = emit or (lambda event, data: None)
    by_url: dict[str, object] = {}
    for a in assessments:
        for sa in a.sources:
            if sa.url and sa.fetch is not None and sa.url not in by_url:
                by_url[sa.url] = (a, sa)
    known = {r["url"]: r for r in rows if r["url"]}

    # 1. who cites what (sequential and in source order: the same article always yields the same depth-2 set)
    cands: list[_Candidate] = []
    seen: set[tuple[str, str]] = set()
    for parent in [r for r in rows if r["depth"] == 1 and r["url"] and r["fetch_status"] == "ok"]:
        hit = by_url.get(parent["url"])
        claim = next((c for c in claims if parent["id"] in c["source_ids"]), None)
        if hit is None or claim is None or not hit[1].fetch.text:
            continue
        a, sa = hit
        quote = sa.verify.quote_original if sa.verify else None
        taken = 0
        for link in passage_links(sa.fetch.text, quote, a.claim.text, parent["url"]):
            domain = registrable_domain(link.url)
            if domain == parent["domain"] or _is_homepage(link.url) or (parent["id"], link.url) in seen:
                continue                          # a source citing itself / its own front page is not a citation
            seen.add((parent["id"], link.url))
            cands.append(_Candidate(parent, claim["id"], link, domain, known.get(link.url)))
            taken += 1
            if taken >= MAX_LINKS_PER_SOURCE:
                break

    # 2. fetch the new ones. A link back to the article's own domain is never fetched (that is a self-citation of
    #    the outlet, and all we need is the fact that the source points back).
    res = Depth2Result()
    todo = [c for c in cands if c.existing is None and c.domain != article_domain]
    fetch_urls = list(dict.fromkeys(c.link.url for c in todo))[:MAX_FETCHES]
    fetched: dict[str, object] = {}
    if fetch_urls:
        pool = ThreadPoolExecutor(max_workers=min(len(fetch_urls), 6), thread_name_prefix="depth2")
        end = time.monotonic() + deadline_s
        futures = {u: pool.submit(_safe_fetch, fetcher, u, article_url) for u in fetch_urls}
        for u, fut in futures.items():
            try:
                fetched[u] = fut.result(timeout=max(end - time.monotonic(), 0.0))
            except FutureTimeout:
                log.warning("depth-2 fetch of %s did not finish in %.0fs", u, deadline_s)
                fut.cancel()
        pool.shutdown(wait=False)

    # 3. rows, edges, citations
    for c in cands:
        child = c.existing or next((r for r in res.rows if r["url"] == c.link.url), None)
        if child is None:
            if c.domain != article_domain and c.link.url not in fetch_urls:
                continue                          # over the fetch budget: not followed at all
            f = fetched.get(c.link.url)
            if c.domain == article_domain:
                kind, status, via, html = "self", "not_fetched", None, None
            else:
                kind = f.kind if f is not None else "link"
                status = f.status if f is not None else "unreachable"      # timed out / crashed: neutral
                via, html = (f.via, f.html) if f is not None else (None, None)
            child = {"id": f"s{len(rows) + 1}", "url": c.link.url, "domain": c.domain, "kind": kind,
                     "attribution_text": c.link.anchor_text or None, "depth": 2, "parent_source_id": c.parent["id"],
                     "fetch_status": status, "fetched_via": via}
            rows.append(child)
            res.rows.append(child)
            res.pages.append(SourcePage(domain=c.domain, html=html))
            out("node", {"id": child["id"], "type": "source", "label": c.domain, "verdict": None, "cluster_id": None, "depth": 2})
        edge = {"id": f"e-{c.parent['id']}-{child['id']}", "source": c.parent["id"], "target": child["id"], "type": "cites"}
        if child["id"] != c.parent["id"] and edge not in res.edges:
            res.edges.append(edge)
            out("edge", edge)
        res.citations.append(Citation(c.claim_id, c.parent["id"], child["id"], c.domain))
    return res


def _safe_fetch(fetcher: Fetcher, url: str, article_url: str):
    try:
        return fetcher.fetch_source(url, article_url, with_html=True)
    except Exception as exc:       # the fetch layer should not raise; a depth-2 page must never break the analysis
        log.warning("depth-2 fetch of %s failed: %s", url, type(exc).__name__)
        return None
