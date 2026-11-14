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
from urllib.parse import unquote, urlsplit

import re
from datetime import datetime

from ..config import Settings
from ..fetch import Fetcher
from ..fetch.urls import registrable_domain
from ..llm import LLMClient
from ..signals import SourcePage
from ..signals.circles import Citation
from .textutil import (LINK_RE, LinkRef, attach_links, build_plain, is_navigation_link, locate, paragraph_bounds,
                       plain_anchor, sentence_bounds, sentence_units, to_markdown_span)
from .verify import verify_claim

log = logging.getLogger("citetrace.depth2")

MAX_LINKS_PER_SOURCE = 2       # child links followed per parent source
MAX_FETCHES = 8                # new pages fetched per hop (depth 1->2, 2->3, ...)
MAX_FETCHES_TOTAL = 14         # total deeper-hop fetches across all depths (cost + latency)
QUOTE_ALIGN_THRESHOLD = 85.0   # locating the verified evidence quote in the source
CLAIM_ALIGN_THRESHOLD = 70.0   # no quote: locating the claim itself; weaker, so stricter than "anything similar"
DEADLINE_S = 25.0              # wall clock for the fetches of ONE hop; the rest is "unreachable" (neutral)
MIN_RELEVANCE = 2              # a page link far from the passage needs this many claim words in its anchor / URL slug
MIN_RELEVANCE_NEAR = 1         # ... one is enough in the paragraph just before / after the passage
MAX_RELEVANT_POOL = 6          # best-ranked relevant links offered per source (the per-source cap still applies)
CHECK_DEADLINE_S = 40.0        # wall clock for the checks (LLM) of ONE hop; a check that does not finish is simply absent
MAX_CHECK_WORKERS = 4
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


def _locate(source_text: str, quote: str | None, claim_text: str, alt_quote: str | None):
    """(plain view, (start, end, score) | None): where in a source the passage the claim rests on sits."""
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
    return plain, loc


def passage_of(source_text: str, quote: str | None, claim_text: str, alt_quote: str | None = None) -> tuple[str, str] | None:
    """(sentence, paragraph) of the passage the claim rests on, as plain text; None when it cannot be located."""
    plain, loc = _locate(source_text, quote, claim_text, alt_quote)
    if loc is None:
        return None
    a, b = to_markdown_span(source_text, plain, loc[0], loc[1])
    s0, s1 = sentence_bounds(source_text, a, b)
    p0, p1 = paragraph_bounds(source_text, a, b)
    return build_plain(source_text[s0:s1]).text.strip(), build_plain(source_text[p0:p1]).text.strip()


def passage_links(source_text: str, quote: str | None, claim_text: str, source_url: str,
                  alt_quote: str | None = None) -> list[LinkRef]:
    """Links the passage a claim rests on in a source points to.

    1. the links in the located passage's sentence, else its paragraph (what the source cites for exactly this);
    2. none there, or the passage cannot be located: links elsewhere on the page whose anchor text / URL slug share
       words with the claim (`relevant_links`), nearest paragraphs first. Navigation, footer and share links share
       none, so a page full of links does not turn into a crawl of the whole page."""
    plain, loc = _locate(source_text, quote, claim_text, alt_quote)
    span = None
    if loc is not None:
        span = to_markdown_span(source_text, plain, loc[0], loc[1])
        links = attach_links(source_text, span[0], span[1], source_url)
        if links:
            return links
    return relevant_links(source_text, [claim_text, quote, alt_quote], source_url, span)


_STOP = frozenset("""about above after again also another because been before being between both could does done down
during each from have here into just like more most much must only other over said same should since some such than that
their them then there these they this those through under until upon very were what when where which while will with
would your according read more news story article latest video click share view link links page pages today
aber auch dass dazu denn doch durch eine einem einen einer eines euch haben hatte hier ihre kann mehr nach noch nur oder
sein sich sind über unter viel wann warum weil wenn werden wird wurde zum zur""".split())


def _stems(text: str) -> set[str]:
    """Content words as crude 6-letter stems ("districts"/"district", "expands"/"expand"); digits never count (URL dates)."""
    return {w[:6] for w in re.findall(r"[^\W\d_]{4,}", text.lower()) if w not in _STOP}


def _unwrap(url: str) -> str:
    """Newsletter / click-tracker redirects (`track.example/L0/https:%2F%2Fwww.cnbc.com%2F...`) cite the wrapped page."""
    m = re.search(r"https?(?::|%3[Aa])(?://|%2[Ff]%2[Ff])", url[8:])
    if not m:
        return url
    inner = unquote(url[8:][m.start():]).split("&", 1)[0].split("?utm_", 1)[0]
    return inner if re.match(r"https?://[^/\s]+\.[^/\s]+", inner) else url


def relevant_links(md: str, texts: list[str | None], source_url: str,
                   near: tuple[int, int] | None = None) -> list[LinkRef]:
    """Body links of a page that talk about the claim, best first (decided by code, no LLM).

    A link counts when its anchor text plus URL slug share >= MIN_RELEVANCE words with the claim (and the quote /
    the parent's quote); one shared word is enough in the paragraph just before or after the located passage. The
    domain is not matched, so "whitehouse.gov" cannot match a claim only because it says "white house"."""
    claim = _stems(" ".join(build_plain(x).text for x in texts if x))
    if not claim:
        return []
    lo = hi = None
    if near is not None:
        p0, p1 = paragraph_bounds(md, near[0], near[1])
        lo = paragraph_bounds(md, max(p0 - 2, 0), max(p0 - 2, 0))[0] if p0 > 1 else p0
        nxt = min(p1 + 2, len(md))
        hi = paragraph_bounds(md, nxt, nxt)[1]
    scored: list[tuple[int, int, LinkRef]] = []
    seen: set[str] = set()
    for m in LINK_RE.finditer(md):
        url, anchor = _unwrap(m.group(2)), plain_anchor(m.group(1))
        key = url.split("#")[0].rstrip("/")
        if key in seen or key == source_url.split("#")[0].rstrip("/") or is_navigation_link(url, source_url):
            continue
        seen.add(key)
        slug = " ".join(p for p in re.split(r"[/_\-.?=&]+", urlsplit(url).path) if p)
        shared = len(claim & _stems(f"{anchor} {slug}"))
        need = MIN_RELEVANCE_NEAR if lo is not None and lo <= m.start() <= hi else MIN_RELEVANCE
        if shared >= need:
            scored.append((-shared, m.start(), LinkRef(url, anchor, "relevant")))
    scored.sort(key=lambda x: (x[0], x[1]))
    return [r for _, _, r in scored[:MAX_RELEVANT_POOL]]


def _is_homepage(url: str) -> bool:
    """A bare homepage link is a credit / navigation link, not a citation of anything."""
    p = urlsplit(url)
    return p.path in ("", "/") and not p.query


_SEARCH_ENGINES = {"google", "bing", "duckduckgo", "yandex", "baidu", "ecosia", "startpage"}


def _is_search_page(url: str) -> bool:
    """A search-results page (Google / Yahoo / DuckDuckGo ...) is how a fact-checker shows 'I looked and found
    nothing', not a source the passage rests on. It must not use up the per-source link cap."""
    p = urlsplit(url)
    host = p.netloc.lower()
    if host.startswith("search.") or ".search." in host:      # uk.news.search.yahoo.com, search.brave.com
        return True
    if registrable_domain(url).split(".")[0] in _SEARCH_ENGINES:
        return p.path.lower().startswith("/search") or p.path in ("", "/") or any(k in p.query for k in ("q=", "wd=", "text="))
    return False


def expand_sources(rows: list[dict], assessments, claims: list[dict], *, fetcher: Fetcher, article_url: str,
                   article_domain: str, max_depth: int = 2, emit: Emit | None = None,
                   deadline_s: float = DEADLINE_S, llm: LLMClient | None = None,
                   settings: Settings | None = None, step_cache=None) -> Depth2Result:
    """Add depth-2..max_depth sources to `rows` (mutated: ids continue after the last source).

    `rows` are the initial contract Source dicts; `assessments` the ClaimAssessments they came from (fetched text
    and verified quotes live there); `claims` the contract Claim dicts (which claim cites which source).
    A source whose passage links nothing usable but NAMES where its information comes from ("the New York Times reported")
    gets that source searched for (`chainsearch.py`, Exa Agent + our searches) when `settings.chain_search` is on.
    With `llm` + `settings`, every newly read depth >= 2 source is CHECKED against the claim it is cited for (quote
    first, like every verdict) and gets `Source.check`; without them the structure is recorded but nothing is judged.
    """
    res = Depth2Result()
    if max_depth < 2:
        return res
    out = emit or (lambda event, data: None)
    text_by_url: dict[str, str] = {}
    quote_by_url: dict[str, str | None] = {}
    pub_by_url: dict[str, datetime | None] = {}
    for a in assessments:
        for sa in a.sources:
            if sa.url and sa.fetch is not None and sa.fetch.text:
                text_by_url.setdefault(sa.url, sa.fetch.text)
                pub_by_url.setdefault(sa.url, _parse_dt(sa.fetch.published_at))
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

    seen: set[tuple[str, str]] = set()
    total_fetched = 0
    searches_left = settings.max_chain_searches if (settings is not None and llm is not None and settings.chain_search
                                                    and settings.exa_api_key) else 0
    chain_cache = None

    for cur_depth in range(1, max_depth):
        end = time.monotonic() + deadline_s      # per hop: a slow hop 2 must not starve hops 3..5
        parents = [r for r in rows if r["depth"] == cur_depth and r["url"] and r["fetch_status"] == "ok"]
        if not parents:
            break

        # 1. who cites what (sequential and in source order: deterministic across replays)
        cands: list[_Candidate] = []
        unlinked: list[tuple[dict, str, str, str, str | None, str | None]] = []
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
                if domain == parent["domain"] or _is_homepage(link.url) or _is_search_page(link.url) \
                        or (parent["id"], link.url) in seen:
                    continue                      # a source citing itself / its own front page is not a citation
                seen.add((parent["id"], link.url))
                cands.append(_Candidate(parent, claim_id, claim_text, link, domain, quote or alt_quote, known.get(link.url)))
                taken += 1
                if taken >= MAX_LINKS_PER_SOURCE:
                    break
            if taken == 0 and searches_left > 0:        # no usable link: the passage may still NAME a source
                unlinked.append((parent, claim_id, claim_text, parent_text, quote, alt_quote))
        if not cands and not (unlinked and searches_left > 0):
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
        to_check: list[tuple[dict, str, str]] = []
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
                if f is not None and f.text and status == "ok":
                    to_check.append((child, c.claim_text, f.text))
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
        if unlinked and searches_left > 0:
            if chain_cache is None:
                from .assess import _FetchCache      # assess imports this module: import it late
                chain_cache = _FetchCache(fetcher, article_url)
            searches_left -= _search_named(unlinked, next_depth, rows, known, claim_by_source, chain_by_source,
                                           alt_quote_by_source, text_by_url, pub_by_url, res, out, llm=llm, settings=settings,
                                           cache=chain_cache, step_cache=step_cache, max_searches=searches_left)
        if llm is not None and settings is not None:
            _check_sources(to_check, llm, settings)
    if chain_cache is not None:
        chain_cache.join_html(timeout=5.0)
        for r, pg in zip(res.rows, res.pages):          # the raw HTML of found pages (source type), after the searches
            sa_html = chain_cache._data.get(r["url"]) if r.get("url") else None
            if r["kind"] == "named" and sa_html is not None and pg.html is None:
                pg.html = sa_html.html
    return res


def _parse_dt(v) -> datetime | None:
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00")).replace(tzinfo=None) if v else None
    except ValueError:
        return None


def _search_named(unlinked, next_depth, rows, known, claim_by_source, chain_by_source, alt_quote_by_source, text_by_url,
                  pub_by_url, res: Depth2Result, out, *, llm, settings, cache, step_cache, max_searches: int) -> int:
    """Name + look for the source each of these passages points to (in parallel, one hop deadline). Adds one row per
    passage that names a source: the page we found (verified quote, `check`), else a stub with just the name.
    Returns how many searches ran (they count against `max_chain_searches`)."""
    from .chainsearch import find_cited, named_source

    jobs = []
    for parent, claim_id, claim_text, parent_text, quote, alt_quote in unlinked:
        passage = passage_of(parent_text, quote, claim_text, alt_quote)
        if passage is not None:
            jobs.append((parent, claim_id, claim_text, parent_text, passage))
    if not jobs:
        return 0
    end = time.monotonic() + settings.chain_search_deadline_s
    # 1. naming is one cheap LLM call: do it for every passage, so the (paid) searches below are not spent on passages
    #    that name nobody ("plain fact", "our reporters")
    with ThreadPoolExecutor(max_workers=min(len(jobs), 6), thread_name_prefix="chainname") as npool:
        names = list(npool.map(lambda j: named_source(j[4][0], j[4][1], j[2], j[0]["domain"], llm=llm, model=settings.model_extract), jobs))
    jobs = [(j, who) for j, who in zip(jobs, names) if who][:max_searches]
    if not jobs:
        return 0
    # 2. look for each named source (Exa Agent + our searches), in parallel, one hop deadline
    pool = ThreadPoolExecutor(max_workers=len(jobs), thread_name_prefix="chain")
    futures = [(j, pool.submit(find_cited, j[0]["url"], j[0]["domain"], j[3], j[4][0], j[4][1], j[2], cache=cache, llm=llm,
                               settings=settings, published_at=pub_by_url.get(j[0]["url"]), step_cache=step_cache, who=who))
               for j, who in jobs]
    for (parent, claim_id, claim_text, parent_text, passage), fut in futures:
        try:
            cited = fut.result(timeout=max(end - time.monotonic(), 0.0))
        except FutureTimeout:
            log.warning("search for the source cited by %s did not finish before deadline", parent["url"])
            fut.cancel()
            continue
        except Exception as exc:
            log.warning("search for the source cited by %s failed: %s", parent["url"], type(exc).__name__)
            continue
        if cited is None:
            continue
        sa = cited.found
        child = known.get(sa.url) if sa else None
        if child is None:
            child = {"id": f"s{len(rows) + 1}", "url": sa.url if sa else None, "domain": registrable_domain(sa.url) if sa else None,
                     "kind": "named", "attribution_text": cited.who, "depth": next_depth, "parent_source_id": parent["id"],
                     "fetch_status": sa.fetch.status if sa else "not_fetched", "fetched_via": sa.fetch.via if sa else None}
            if sa:
                v = sa.verify
                child["check"] = {"verdict": v.verdict, "quote_original": v.quote_original, "fuzzy_score": v.fuzzy_score,
                                  "explanation": v.explanation, "model": v.model, "prompt_version": v.prompt_version}
                text_by_url.setdefault(sa.url, sa.fetch.text or "")
                known[sa.url] = child
            rows.append(child)
            res.rows.append(child)
            res.pages.append(SourcePage(domain=child["domain"] or "", html=sa.fetch.html if sa else None))
            claim_by_source[child["id"]] = (claim_id, claim_text)
            chain_by_source[child["id"]] = chain_by_source[parent["id"]] + (child["id"],)
            alt_quote_by_source[child["id"]] = cited.statement
            out("node", {"id": child["id"], "type": "source", "label": child["domain"] or cited.who, "verdict": None,
                         "cluster_id": None, "depth": next_depth})
        edge = {"id": f"e-{parent['id']}-{child['id']}", "source": parent["id"], "target": child["id"], "type": "cites"}
        if edge not in res.edges:
            res.edges.append(edge)
            out("edge", edge)
        # no Citation: a page WE searched for is not one the source cites, so it can never close a citation circle
    pool.shutdown(wait=False)
    return len(jobs)


def _check_sources(items: list[tuple[dict, str, str]], llm: LLMClient, settings: Settings) -> None:
    """Judge each newly read source against the claim it is cited for and store `Source.check` (display only: it
    never reaches `verdict.py`, so a depth-2 page can neither condemn nor rescue an article). A failed or late check
    leaves the key out, which means "not checked"."""
    if not items:
        return
    end = time.monotonic() + CHECK_DEADLINE_S
    pool = ThreadPoolExecutor(max_workers=min(len(items), MAX_CHECK_WORKERS), thread_name_prefix="d2check")
    futures = [(row, pool.submit(verify_claim, claim_text, text, llm=llm, settings=settings, factcheck=True))
               for row, claim_text, text in items]
    for row, fut in futures:
        try:
            v = fut.result(timeout=max(end - time.monotonic(), 0.0))
        except FutureTimeout:
            log.warning("depth check of %s did not finish before deadline", row["url"])
            fut.cancel()
            continue
        except Exception as exc:       # LLM down / bad output: no check, never a failed analysis
            log.warning("depth check of %s failed: %s", row["url"], type(exc).__name__)
            continue
        row["check"] = {"verdict": v.verdict, "quote_original": v.quote_original, "fuzzy_score": v.fuzzy_score,
                        "explanation": v.explanation, "model": v.model, "prompt_version": v.prompt_version}
    pool.shutdown(wait=False)


def rows_quote_map(rows: list[dict], quote_by_url: dict[str, str | None]) -> dict[str, str]:
    return {r["id"]: quote_by_url[r["url"]] for r in rows if r.get("url") and quote_by_url.get(r["url"])}


def _safe_fetch(fetcher: Fetcher, url: str, article_url: str):
    try:
        return fetcher.fetch_source(url, article_url, with_html=True)
    except Exception as exc:       # the fetch layer should not raise; a depth-2 page must never break the analysis
        log.warning("depth-2 fetch of %s failed: %s", url, type(exc).__name__)
        return None
