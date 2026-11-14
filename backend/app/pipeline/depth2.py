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
from .textutil import LinkRef, attach_links, build_plain, locate, sentence_units, to_markdown_span

log = logging.getLogger("citetrace.depth2")

MAX_LINKS_PER_SOURCE = 2       # child links followed per parent source
MAX_FETCHES = 8                # new pages fetched per hop (depth 1->2, 2->3, ...)
MAX_FETCHES_TOTAL = 14         # total deeper-hop fetches across all depths (cost + latency)
QUOTE_ALIGN_THRESHOLD = 85.0   # locating the verified evidence quote in the source
CLAIM_ALIGN_THRESHOLD = 70.0   # no quote: locating the claim itself; weaker, so stricter than "anything similar"
DEADLINE_S = 25.0              # wall clock for all deeper-hop fetches; the rest is "unreachable" (neutral)
Emit = Callable[[str, dict], None]


@dataclass
class Depth2Result:
    rows: list[dict] = field(default_factory=list)          # new contract Source rows (depth >= 2)
    edges: list[dict] = field(default_factory=list)         # `cites` edges parent -> child (new or existing source)
    pages: list[SourcePage] = field(default_factory=list)   # for the infra step
    citations: list[Citation] = field(default_factory=list)


@dataclass
class _Candidate:
    parent: dict
    claim_id: str
    claim_text: str
    link: LinkRef
    domain: str
    quote_hint: str | None = None
    existing: dict | None = None


def passage_links(source_text: str, quote: str | None, claim_text: str, source_url: str,
                  alt_quote: str | None = None) -> list[LinkRef]:
    """Links in the sentence/paragraph of the passage the claim rests on in a source. [] when it cannot be located."""
    plain = build_plain(source_text)
    loc = locate(plain, quote, QUOTE_ALIGN_THRESHOLD) if quote else None
    if loc is None:
        loc = locate(plain, build_plain(claim_text).text, CLAIM_ALIGN_THRESHOLD)
    if loc is None and alt_quote:
        loc = locate(plain, build_plain(alt_quote).text, CLAIM_ALIGN_THRESHOLD)
        if loc is None:
            for _, _, sent in sentence_units(alt_quote, dedupe=False):
                s_plain = build_plain(sent).text
                if len(s_plain) >= 35:
                    loc = locate(plain, s_plain, CLAIM_ALIGN_THRESHOLD)
                    if loc is not None:
                        break
    if loc is None:
        return []
    start, end = to_markdown_span(source_text, plain, loc[0], loc[1])
    return attach_links(source_text, start, end, source_url)


def _is_homepage(url: str) -> bool:
    """A bare homepage link is a credit / navigation link, not a citation of anything."""
    p = urlsplit(url)
    return p.path in ("", "/") and not p.query


def expand_sources(rows: list[dict], assessments, claims: list[dict], *, fetcher: Fetcher, article_url: str,
                   article_domain: str, max_depth: int = 2, emit: Emit | None = None,
                   deadline_s: float = DEADLINE_S) -> Depth2Result:
    """Add depth-2..max_depth sources to `rows` (mutated: ids continue after the last source).

    `rows` are the initial contract Source dicts; `assessments` the ClaimAssessments they came from (fetched text
    and verified quotes live there); `claims` the contract Claim dicts (which claim cites which source).
    """
    res = Depth2Result()
    if max_depth < 2:
        return res
    out = emit or (lambda event, data: None)
    text_by_url: dict[str, str] = {}
    quote_by_url: dict[str, str | None] = {}
    for a in assessments:
        for sa in a.sources:
            if sa.url and sa.fetch is not None and sa.fetch.text:
                text_by_url.setdefault(sa.url, sa.fetch.text)
                q = sa.verify.quote_original if sa.verify and not sa.verify.downgraded else None
                if q and not quote_by_url.get(sa.url):
                    quote_by_url[sa.url] = q

    known = {r["url"]: r for r in rows if r["url"]}
    claim_by_source: dict[str, tuple[str, str]] = {}
    for c in claims:
        for sid in (*c["source_ids"], *c.get("found_source_ids", ())):
            claim_by_source.setdefault(sid, (c["id"], c["text"]))
    chain_by_source: dict[str, tuple[str, ...]] = {}
    alt_quote_by_source: dict[str, str | None] = {}
    rqm = rows_quote_map(rows, quote_by_url)
    for r in sorted(rows, key=lambda r: r["depth"]):
        pid = r.get("parent_source_id")
        chain_by_source[r["id"]] = (chain_by_source[pid] + (r["id"],)) if pid and pid in chain_by_source else (r["id"],)
        if pid and pid in claim_by_source and r["id"] not in claim_by_source:
            claim_by_source[r["id"]] = claim_by_source[pid]
        if pid and pid in rqm:
            alt_quote_by_source[r["id"]] = rqm[pid]

    end = time.monotonic() + deadline_s
    seen: set[tuple[str, str]] = set()
    total_fetched = 0

    for cur_depth in range(1, max_depth):
        parents = [r for r in rows if r["depth"] == cur_depth and r["url"] and r["fetch_status"] == "ok"]
        if not parents:
            break

        # 1. who cites what (sequential and in source order: deterministic across replays)
        cands: list[_Candidate] = []
        for parent in parents:
            parent_text = text_by_url.get(parent["url"])
            cinfo = claim_by_source.get(parent["id"])
            if not parent_text or cinfo is None:
                continue
            claim_id, claim_text = cinfo
            quote = quote_by_url.get(parent["url"])
            alt_quote = alt_quote_by_source.get(parent["id"])
            taken = 0
            for link in passage_links(parent_text, quote, claim_text, parent["url"], alt_quote=alt_quote):
                domain = registrable_domain(link.url)
                if domain == parent["domain"] or _is_homepage(link.url) or (parent["id"], link.url) in seen:
                    continue                      # a source citing itself / its own front page is not a citation
                seen.add((parent["id"], link.url))
                cands.append(_Candidate(parent, claim_id, claim_text, link, domain, quote or alt_quote, known.get(link.url)))
                taken += 1
                if taken >= MAX_LINKS_PER_SOURCE:
                    break
        if not cands:
            break

        # 2. fetch the new ones (skip own article domain: a link back back-closes a circle without fetching).
        budget = min(MAX_FETCHES, max(MAX_FETCHES_TOTAL - total_fetched, 0))
        todo = [c for c in cands if c.existing is None and c.domain != article_domain]
        fetch_urls = list(dict.fromkeys(c.link.url for c in todo))[:budget]
        fetched: dict[str, object] = {}
        if fetch_urls and time.monotonic() < end:
            pool = ThreadPoolExecutor(max_workers=min(len(fetch_urls), 6), thread_name_prefix=f"depth{cur_depth + 1}")
            futures = {u: pool.submit(_safe_fetch, fetcher, u, article_url) for u in fetch_urls}
            for u, fut in futures.items():
                try:
                    fetched[u] = fut.result(timeout=max(end - time.monotonic(), 0.0))
                except FutureTimeout:
                    log.warning("depth-%d fetch of %s did not finish before deadline", cur_depth + 1, u)
                    fut.cancel()
            pool.shutdown(wait=False)
        total_fetched += len(fetch_urls)

        # 3. rows, edges, citations
        next_depth = cur_depth + 1
        for c in cands:
            child = c.existing or known.get(c.link.url)
            if child is None:
                if c.domain != article_domain and c.link.url not in fetch_urls:
                    continue                      # over the fetch budget: not followed at all
                f = fetched.get(c.link.url)
                if c.domain == article_domain:
                    kind, status, via, html = "self", "not_fetched", None, None
                else:
                    kind = f.kind if f is not None else "link"
                    status = f.status if f is not None else "unreachable"      # timed out / crashed: neutral
                    via, html = (f.via, f.html) if f is not None else (None, None)
                    if f is not None and f.text:
                        text_by_url.setdefault(c.link.url, f.text)
                child = {"id": f"s{len(rows) + 1}", "url": c.link.url, "domain": c.domain, "kind": kind,
                         "attribution_text": c.link.anchor_text or None, "depth": next_depth,
                         "parent_source_id": c.parent["id"], "fetch_status": status, "fetched_via": via}
                rows.append(child)
                known[c.link.url] = child
                res.rows.append(child)
                res.pages.append(SourcePage(domain=c.domain, html=html))
                claim_by_source[child["id"]] = (c.claim_id, c.claim_text)
                chain_by_source[child["id"]] = chain_by_source[c.parent["id"]] + (child["id"],)
                if c.quote_hint:
                    alt_quote_by_source[child["id"]] = c.quote_hint
                out("node", {"id": child["id"], "type": "source", "label": c.domain, "verdict": None,
                             "cluster_id": None, "depth": next_depth})
            edge = {"id": f"e-{c.parent['id']}-{child['id']}", "source": c.parent["id"], "target": child["id"], "type": "cites"}
            if child["id"] != c.parent["id"] and edge not in res.edges:
                res.edges.append(edge)
                out("edge", edge)
            res.citations.append(Citation(c.claim_id, c.parent["id"], child["id"], c.domain,
                                          chain=chain_by_source[c.parent["id"]]))
    return res


def rows_quote_map(rows: list[dict], quote_by_url: dict[str, str | None]) -> dict[str, str]:
    return {r["id"]: quote_by_url[r["url"]] for r in rows if r.get("url") and quote_by_url.get(r["url"])}


def _safe_fetch(fetcher: Fetcher, url: str, article_url: str):
    try:
        return fetcher.fetch_source(url, article_url, with_html=True)
    except Exception as exc:       # the fetch layer should not raise; a depth-2 page must never break the analysis
        log.warning("depth-2 fetch of %s failed: %s", url, type(exc).__name__)
        return None
