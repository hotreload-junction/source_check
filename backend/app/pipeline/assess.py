"""B4: assess one claim end to end: collect sources -> fetch -> verify -> aggregate to ONE claim verdict.

Source collection (DECISIONS Q18 + leo's answer on the board):
- links in the claim's sentence/paragraph are the sources;
- a claim without links but with named attributions: Exa proposes candidates for each attribution. Only an ORIGINAL
  (official statement, report, transcript, Q&A interview; judged by verify.py `source_type`) that CONFIRMS the claim
  counts (`supported`); everything else about a guessed source is neutral.
  Another outlet reporting the same statement is corroboration, not the original -> `not_checkable` (neutral);
- no links and no attribution -> `unsourced`.
Neutral outcomes never count against an article: unreachable/paywall/blocked, social_media, self_citation,
unlocated named sources, failed LLM calls, and negative verdicts the code could not back with a verified quote.
"""
from __future__ import annotations

import dataclasses
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from dataclasses import dataclass, field
from typing import Callable

import httpx

from ..config import Settings, get_settings
from ..fetch import Fetcher, FetchResult
from ..fetch.urls import classify_url, is_same_domain, registrable_domain
from ..llm import LLMClient, LLMError
from ..models import ClaimVerdictRecord
from ..signals.networks import match_network
from ..signals.reputation import snapshots
from datetime import datetime

from .depth2 import passage_links
from .evidence import Candidate, SearchPlan, gather_candidates, merge_agent_hits, plan_search
from .exa_agent import AgentResult, build_body, find_sources
from .factcheck import FactResult, build_query, run_factcheck
from .extract import ExtractedClaim, _name_key
from .textutil import SYNDICATION_THRESHOLD, syndication_share, build_plain
from .verify import VerifyResult, verify_claim

log = logging.getLogger("citetrace.assess")

MAX_SOURCES_PER_CLAIM = 3      # keep cost bounded
NO_SOURCE = "The article gives no source for this claim."
MAX_CROSS_PAGES = 3            # unsourced claim: pages found for OTHER unsourced claims that it is checked against
CROSS_DEADLINE_S = 45.0        # wall clock for that extra round; unfinished checks leave the claim `unsourced` (neutral)
NOT_VERIFIED = "Quote could not be verified: neither the original (interview, transcript, statement) nor an independent report of it was found."
_RANK = {"supported": 4, "contradicted": 3, "distorted": 3, "not_mentioned": 2, "not_checkable": 1}


@dataclass
class SourceAssessment:
    url: str | None
    kind: str                       # link | named | telegram | social | self
    attribution_text: str | None
    fetch: FetchResult | None
    verify: VerifyResult | None
    verdict: str                    # per-source, after neutral mappings
    note: str = ""                  # why neutral, when no VerifyResult explains it
    corroborated: bool = False      # named source: a secondary report supports the claim, original not found
    venue_hosted: bool = False      # found on the site of the venue the article names
    phrase_hit: bool = False        # found through the exact quoted words
    depth: int = 1                  # 2 = a source of a self-cited page ("source of the source")
    parent_url: str | None = None   # depth 2: the self-cited page that cites it
    agent_kind: str | None = None   # the Exa Agent returned it (its own label, never trusted)
    factchecked: bool = False       # found by the fact-check agent and verified in fact-check mode (can refute, not only confirm)


@dataclass
class ClaimAssessment:
    claim: ExtractedClaim
    verdict: str                    # contract ClaimVerdict
    subtype: str | None
    explanation: str
    sources: list[SourceAssessment] = field(default_factory=list)
    best: SourceAssessment | None = None   # the source whose evidence the verdict rests on
    plan: SearchPlan | None = None         # attributed claims: who/where/when the search looked for

    @property
    def evidence(self) -> VerifyResult | None:
        """Evidence only when the verdict rests on it (a neutralised secondary report is not evidence)."""
        if self.verdict != "unsourced" and self.best and self.best.verify and self.best.verify.has_evidence \
                and self.best.verdict in ("supported", "distorted", "contradicted"):
            return self.best.verify
        return None


class _FetchCache:
    """Same URL cited by several claims is fetched once (rate limit + cost).

    Verification only needs the TEXT. The raw HTML (infra signals, B5) is fetched in the background while the
    LLM verifies, and joined at the end: it used to sit in front of every verify call (AP: 87 s summed)."""

    def __init__(self, fetcher: Fetcher, article_url: str, html_workers: int = 6):
        self.fetcher, self.article_url = fetcher, article_url
        self._data: dict[str, FetchResult] = {}
        self._locks: dict[str, threading.Lock] = {}
        self._guard = threading.Lock()
        self._html_pool = ThreadPoolExecutor(max_workers=html_workers, thread_name_prefix="html")
        self._html: dict[str, object] = {}

    def get(self, url: str) -> FetchResult:
        with self._guard:
            lock = self._locks.setdefault(url, threading.Lock())
        with lock:
            if url not in self._data:
                res = self.fetcher.fetch_source(url, self.article_url, with_html=False)
                self._data[url] = res
                if res.status == "ok" and res.html is None:
                    self._html[url] = self._html_pool.submit(self.fetcher.fetch_html, url)
            return self._data[url]

    def get_page(self, url: str) -> FetchResult:
        """Page of the article's own site (self-citation) read for the chain; separate from `get`, which never reads it."""
        key = "page:" + url
        with self._guard:
            lock = self._locks.setdefault(key, threading.Lock())
        with lock:
            if key not in self._data:
                self._data[key] = self.fetcher.fetch_linked_page(url)
            return self._data[key]

    def join_html(self, timeout: float = 10.0) -> None:
        """Wait (bounded) for the background HTML fetches and attach the result to the shared FetchResults."""
        end = time.monotonic() + timeout
        for url, fut in list(self._html.items()):
            try:
                self._data[url].html = fut.result(timeout=max(end - time.monotonic(), 0.0))
            except Exception as exc:   # slow or failed: that domain is simply skipped by the infra signals
                log.info("html fetch skipped for %s: %s", url, type(exc).__name__)
        self._html_pool.shutdown(wait=False)


def assess_claims(claims: list[ExtractedClaim], article_url: str, *, fetcher: Fetcher, llm: LLMClient,
                  settings: Settings | None = None, exa_client: httpx.Client | None = None,
                  max_workers: int | None = None, published_at: datetime | None = None,
                  on_claim: Callable[[int, "ClaimAssessment"], None] | None = None, step_cache=None,
                  article_text: str = "") -> list[ClaimAssessment]:
    s = settings or get_settings()
    cache = _FetchCache(fetcher, article_url)
    # Search and verify use the self-contained check sentence (`check_text`, statement.py); what is returned and shown keeps the
    # claim as written: `restore` puts the original back on every assessment before anyone sees it
    originals = list(claims)
    claims = [dataclasses.replace(c, text=c.check_text) if c.check_text else c for c in originals]

    def restore(a: "ClaimAssessment", i: int) -> "ClaimAssessment":
        a.claim = originals[i]
        return a
    deadline = time.monotonic() + s.claim_deadline_s
    # claims are independent and almost all wait on the network: run them all at once (max 6 by contract)
    pool = ThreadPoolExecutor(max_workers=max_workers or max(len(claims), 1), thread_name_prefix="claim")
    futures = [pool.submit(assess_claim, c, article_url, cache=cache, llm=llm, settings=s, exa_client=exa_client,
                           published_at=published_at, step_cache=step_cache, article_text=article_text) for c in claims]
    results: list[ClaimAssessment] = []
    held: list[int] = []            # unsourced claims: announced after the cross-check below, so they are final when shown
    attr_held: list[int] = []       # attributed claims with another same-speaker claim: cross-checked before announcing
    attr_keys = [{k for a in c.attributions if (k := _name_key(a))} for c in claims]
    for i, (claim, fut) in enumerate(zip(claims, futures)):
        try:
            results.append(restore(fut.result(timeout=max(deadline - time.monotonic(), 0.0)), i))
        except FutureTimeout:
            # Bounded latency beats completeness for a live demo: neutral, never evidence against the article.
            log.warning("claim %s not finished within %.0fs, marking not_checkable", claim.id, s.claim_deadline_s)
            fut.cancel()
            results.append(restore(ClaimAssessment(claim, "not_checkable", None,
                                                   "Checking the sources took too long, so this claim was left unverified."), i))
        if s.cross_check_found and s.search_unsourced and s.exa_api_key and results[-1].verdict == "unsourced" and _is_unsourced_type(claim):
            held.append(i)
        elif (s.cross_check_found and s.search_unsourced and s.exa_api_key and results[-1].verdict == "not_checkable"
              and claim.attributions and not claim.links
              and any(attr_keys[i] & attr_keys[j] for j in range(len(claims)) if j != i)):
            attr_held.append(i)
        elif on_claim:  # called from this thread: the SSE stream can show each claim as it is final
            on_claim(i, results[-1])
    pool.shutdown(wait=False)      # stragglers finish in the background and are ignored
    if held:
        try:
            _cross_check(claims, results, held, article_url, cache=cache, llm=llm, settings=s, published_at=published_at,
                         article_text=article_text)
        except Exception as exc:    # never break the analysis for a bonus check
            log.warning("cross-check failed: %s", exc)
    if attr_held:
        try:
            _cross_check_attributed(claims, results, attr_held, article_url, cache=cache, llm=llm, settings=s,
                                    published_at=published_at, article_text=article_text)
        except Exception as exc:    # never break the analysis for a bonus check
            log.warning("attributed cross-check failed: %s", exc)
    for i in range(len(results)):                  # cross-checks build new assessments: the shown claim is always the original
        restore(results[i], i)
    if on_claim:
        for i in sorted([*held, *attr_held]):
            on_claim(i, results[i])
    cache.join_html(timeout=max(min(deadline - time.monotonic(), 10.0), 1.0))
    return results


def assess_claim(claim: ExtractedClaim, article_url: str, *, cache: _FetchCache, llm: LLMClient,
                 settings: Settings, exa_client: httpx.Client | None = None,
                 published_at: datetime | None = None, step_cache=None, article_text: str = "") -> ClaimAssessment:
    kw = dict(cache=cache, llm=llm, settings=settings, exa_client=exa_client, published_at=published_at,
              step_cache=step_cache, article_text=article_text)
    if claim.links:
        return _assess_linked(claim, article_url, **kw)
    if claim.attributions:
        res = _assess_attributed(claim, article_url, **kw)
        return _factcheck_attributed(claim, res, article_url, **kw) if _wants_factcheck(res, settings) else res
    if settings.search_unsourced and settings.exa_api_key:
        return _assess_unsourced(claim, article_url, **kw)
    return ClaimAssessment(claim, "unsourced", None, NO_SOURCE)


# ---------------------------------------------------------------------------------------------- cited links
def _assess_linked(claim, article_url, *, cache, llm, settings, exa_client, published_at, step_cache, article_text) -> ClaimAssessment:
    """The article cites something. A link to the article's OWN site is no independent evidence, but the chain is followed
    one step: what does the self-cited page itself cite? That is the claim's source of the source (depth 2)."""
    jobs = []
    cited = claim.links[:MAX_SOURCES_PER_CLAIM]
    own = [l for l in cited if classify_url(l.url, article_url) == "self"]
    # the longest slug is the most article-like own-site link: those take the limited self-citation slots
    followed = {l.url for l in sorted(own, key=lambda l: -len(l.url))[: settings.max_self_citation_links]} if settings.follow_self_citations else set()
    for link in cited:
        if link.url in followed:
            jobs.append(lambda l=link: _assess_self_citation(claim, l, article_url, cache, llm, settings, published_at))
        else:
            jobs.append(lambda l=link: [_assess_source(claim, l.url, None, article_url, cache, llm, settings,
                                                       loose_link=(l.scope == "paragraph" or _is_homepage(l.url)),
                                                       anchor_text=(l.anchor_text if l.scope == "sentence" else None))])
    with ThreadPoolExecutor(max_workers=max(len(jobs), 1), thread_name_prefix="cited") as pool:
        sources = [sa for group in pool.map(lambda j: j(), jobs) for sa in group]
    res = _aggregate(claim, sources)
    if res.verdict == "not_checkable" and claim.attributions:
        extra = _assess_attributed(claim, article_url, cache=cache, llm=llm, settings=settings, exa_client=exa_client,
                                   published_at=published_at, step_cache=step_cache, article_text=article_text)
        found = [sa for sa in extra.sources if sa.verdict == "supported" or sa.corroborated]
        found.sort(key=lambda sa: (sa.verdict != "supported", not sa.venue_hosted, -(sa.verify.fuzzy_score or 0) if sa.verify else 0))
        if found:
            seen_u = {sa.url for sa in sources if sa.url}
            for sa in found[: settings.max_evidence_links]:
                if sa.url and sa.url not in seen_u:
                    sources.append(sa)
                    seen_u.add(sa.url)
            res = _aggregate(claim, sources)
            res.plan = extra.plan
    return res


def _assess_self_citation(claim: ExtractedClaim, link, article_url: str, cache: _FetchCache, llm: LLMClient, settings: Settings,
                          published_at: datetime | None) -> list[SourceAssessment]:
    """[self-cited page (depth 1, neutral), its sources (depth 2, verified)]. The self-cited page itself never decides
    anything; what it cites can CONFIRM the claim, never count against the article (like a loosely attached link)."""
    page = cache.get_page(link.url)
    head = SourceAssessment(link.url, "self", None, page, None, "not_checkable",
                            "Self-citation: the article cites its own site, which is not independent evidence.")
    if page.status != "ok" or not page.text:
        return [head]
    try:
        v = verify_claim(claim.text, page.text, llm=llm, settings=settings)
    except LLMError as exc:
        log.info("could not read the self-cited page for the claim: %s", exc)
        v = None
    head.verify = v          # locates the passage the claim rests on (quote), for the links below and for M1
    quote = v.quote_original if v and v.quote_original and (v.fuzzy_score or 0) >= settings.fuzzy_threshold else None
    chosen, seen = [], set()
    for l in passage_links(page.text, quote, claim.text, link.url):
        if l.url in seen or is_same_domain(l.url, article_url) or _is_homepage(l.url):
            continue               # the chain stops at the site itself: no second self-citation hop
        seen.add(l.url)
        chosen.append(l)
    chosen = chosen[: settings.max_chain_sources]
    if not chosen:
        return [head]
    with ThreadPoolExecutor(max_workers=len(chosen), thread_name_prefix="chain") as pool:
        d2 = list(pool.map(lambda l: _assess_source(claim, l.url, None, article_url, cache, llm, settings, loose_link=True), chosen))
    for sa in d2:
        sa.depth, sa.parent_url = 2, link.url
    return [head] + d2


def _is_homepage(url: str) -> bool:
    from urllib.parse import urlsplit

    p = urlsplit(url)
    return p.path in ("", "/") and not p.query


# ---------------------------------------------------------------------------------------------- searched sources
def _agent_first(settings: Settings, legacy: bool) -> bool:
    """Agent first, our own Exa searches only when the agent's pages settle nothing (AGENT_FIRST). Needs the agent that is in play."""
    return bool(settings.agent_first and settings.exa_api_key and (settings.exa_agent if legacy else settings.factcheck_agent))


def _own_candidates(plan: SearchPlan, claim: ExtractedClaim, article_url: str, *, settings: Settings, exa_client,
                    published_at: datetime | None, step_cache) -> list[Candidate]:
    return gather_candidates(plan, claim.text, article_url, published_at, api_key=settings.exa_api_key, exa_client=exa_client,
                             step_cache=step_cache, limit=settings.max_evidence_candidates, include_secondary=settings.verify_secondary)


def _discover(claim: ExtractedClaim, article_url: str, *, attributed: bool, llm: LLMClient, settings: Settings, exa_client,
              published_at: datetime | None, step_cache, legacy_agent: bool = True,
              own_searches: bool = True) -> tuple[SearchPlan, list[Candidate], AgentResult]:
    """Candidate pages for a claim the article gives no link for: plan, then OUR searches and the Exa Agent side by side,
    merged and ranked. (Measured: equal on originals but different ones; the agent finds far more confirming pages.)
    `own_searches=False` (AGENT_FIRST): the agent only; the caller runs our searches afterwards if the agent's pages settle nothing."""
    who = claim.attributions[0] if attributed else None
    plan = plan_search(who, claim.text, claim.context, published_at, llm=llm, model=settings.light_model)
    agent = AgentResult(status="disabled")
    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="discover") as pool:
        agent_future = None
        if legacy_agent and settings.exa_agent and settings.exa_api_key:
            body = build_body(claim.text, claim.context, article_url, registrable_domain(article_url), published_at,
                              effort=settings.exa_agent_effort, speaker=plan.speaker, venue=plan.venue,
                              occasion=plan.occasion, attributed=attributed)
            agent_future = pool.submit(find_sources, body, api_key=settings.exa_api_key, article_url=article_url,
                                       timeout_s=settings.exa_agent_timeout_s, concurrency=settings.exa_agent_concurrency,
                                       client=exa_client, cache=step_cache)
        ours = _own_candidates(plan, claim, article_url, settings=settings, exa_client=exa_client, published_at=published_at,
                               step_cache=step_cache) if own_searches else []
        if agent_future is not None:
            agent = agent_future.result()
    merged = merge_agent_hits(ours, agent.hits, plan, plan.speaker or who, settings.max_evidence_candidates)
    return plan, merged, agent


def _verify_all(claim, cands, who, article_url, cache, llm, settings, published_at, plan, article_text, found_unsourced=False):
    if not cands:
        return []
    with ThreadPoolExecutor(max_workers=min(len(cands), 4), thread_name_prefix="verify") as pool:   # independent: verify together
        return list(pool.map(lambda c: _assess_source(claim, c.hit.url, who, article_url, cache, llm, settings, published_at,
                                                      plan=plan, cand=c, article_text=article_text,
                                                      found_unsourced=found_unsourced), cands))


def _assess_attributed(claim, article_url, *, cache, llm, settings, exa_client, published_at, step_cache, article_text) -> ClaimAssessment:
    staged = _agent_first(settings, legacy=True)
    plan, cands, _agent = _discover(claim, article_url, attributed=True, llm=llm, settings=settings, exa_client=exa_client,
                                    published_at=published_at, step_cache=step_cache, own_searches=not staged)
    who = plan.speaker or claim.attributions[0]
    sources = _verify_all(claim, cands, who, article_url, cache, llm, settings, published_at, plan, article_text)
    if staged and not any(sa.verdict == "supported" for sa in sources):
        # the agent's pages settle nothing (no original found, or none at all): now our own searches, minus what was read
        seen = {c.hit.url for c in cands}
        more = [c for c in _own_candidates(plan, claim, article_url, settings=settings, exa_client=exa_client, published_at=published_at,
                                           step_cache=step_cache) if c.hit.url not in seen]
        sources += _verify_all(claim, more, who, article_url, cache, llm, settings, published_at, plan, article_text)
    if not sources:   # attributed but nothing located -> neutral, never "unsourced"
        return ClaimAssessment(claim, "not_checkable", None, f"{NOT_VERIFIED} It is attributed to {who}, but no page containing it was found.",
                               plan=plan)
    a = _aggregate(claim, sources)
    a.plan = plan
    return a


def _assess_unsourced(claim, article_url, *, cache, llm, settings, exa_client, published_at, step_cache, article_text) -> ClaimAssessment:
    """The article names NO source. Look for a page that supports the claim anyway. A page that contains the claim's
    evidence passage verbatim (quote-first, fuzzy-checked like every verdict) makes the claim `supported`; the explanation
    says the article itself gave no source, and the page is listed in `found_source_ids`, not `source_ids`. Nothing that
    supports it: the claim stays `unsourced`. A page that does not support it is ignored (never held against the article)."""
    use_fc = settings.factcheck_agent and bool(settings.exa_api_key)
    staged = use_fc and _agent_first(settings, legacy=False)
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="factcheck") as pool:
        # the fact-check agent needs no plan: it runs next to our own searches and replaces the support-only agent run
        fut = pool.submit(_run_factcheck, claim, article_url, settings=settings, published_at=published_at, step_cache=step_cache) if use_fc else None
        plan, cands, _agent = _discover(claim, article_url, attributed=False, llm=llm, settings=settings, exa_client=exa_client,
                                        published_at=published_at, step_cache=step_cache, legacy_agent=not use_fc,
                                        own_searches=not staged)
        fact = fut.result() if fut else FactResult(status="disabled")
    fact_urls = {h.url for h in fact.hits}
    cands = [c for c in cands if c.hit.url not in fact_urls]      # a page both found is verified once, in fact-check mode
    sources = _verify_all(claim, cands, None, article_url, cache, llm, settings, published_at, plan, article_text, found_unsourced=True)
    sources += _verify_factcheck(claim, fact, article_url, cache, llm, settings, published_at, plan, article_text)
    if staged and not any(sa.verdict == "supported" or (sa.verdict in _REFUTING and sa.verify and sa.verify.has_evidence) for sa in sources):
        # the agent confirmed or refuted nothing: now our own searches, minus the pages it already returned
        more = [c for c in _own_candidates(plan, claim, article_url, settings=settings, exa_client=exa_client, published_at=published_at,
                                           step_cache=step_cache) if c.hit.url not in fact_urls]
        sources += _verify_all(claim, more, None, article_url, cache, llm, settings, published_at, plan, article_text, found_unsourced=True)
    return _found_assessment(claim, sources, plan, settings)


# ---------------------------------------------------------------------------------------------- fact-check agent
_REFUTING = ("contradicted", "distorted")


def _run_factcheck(claim: ExtractedClaim, article_url: str, *, settings: Settings, published_at, step_cache,
                   plan: SearchPlan | None = None, who: str | None = None) -> FactResult:
    query = build_query(claim.text, claim.context, registrable_domain(article_url), published_at, who=who,
                        venue=plan.venue if plan else None, occasion=plan.occasion if plan else None)
    return run_factcheck(query, api_key=settings.exa_api_key, article_url=article_url, effort=settings.factcheck_effort,
                         timeout_s=settings.factcheck_timeout_s, concurrency=settings.exa_agent_concurrency, cache=step_cache)


def _verify_factcheck(claim, fact: FactResult, article_url, cache, llm, settings, published_at, plan, article_text) -> list[SourceAssessment]:
    """Every URL the agent returned goes through fetch + verify in fact-check mode. Its stance / kind / quote are ignored."""
    if not fact.hits:
        return []
    with ThreadPoolExecutor(max_workers=min(len(fact.hits), 4), thread_name_prefix="verify-fc") as pool:
        return list(pool.map(lambda h: _assess_source(claim, h.url, None, article_url, cache, llm, settings, published_at,
                                                      plan=plan, article_text=article_text, found_unsourced=True, factcheck=True), fact.hits))


def _wants_factcheck(res: ClaimAssessment, settings: Settings) -> bool:
    """Attributed claim whose original could not be found and that no independent report confirms."""
    return bool(settings.factcheck_agent and settings.exa_api_key and res.verdict == "not_checkable"
                and not any(sa.corroborated or sa.verdict == "supported" for sa in res.sources))


def _factcheck_attributed(claim, base: ClaimAssessment, article_url, *, cache, llm, settings, exa_client, published_at,
                          step_cache, article_text) -> ClaimAssessment:
    """"According to journalists ..." and no original found: the question becomes whether the FACTS hold. One agent run looks
    for confirming or refuting pages. Anything that goes wrong leaves the neutral `base` answer."""
    try:
        plan = base.plan
        who = (plan.speaker if plan and plan.speaker else None) or claim.attributions[0]
        fact = _run_factcheck(claim, article_url, settings=settings, published_at=published_at, step_cache=step_cache, plan=plan, who=who)
        sources = _verify_factcheck(claim, fact, article_url, cache, llm, settings, published_at, plan, article_text)
        return _attributed_after_factcheck(claim, base, sources, who, settings)
    except Exception as exc:                       # never break the analysis for a bonus check
        log.warning("fact-check for claim %s failed: %s", claim.id, exc)
        return base


def _quality(sa: SourceAssessment) -> tuple:
    return (sa.verdict != "contradicted", -((sa.verify.fuzzy_score or 0.0) if sa.verify else 0.0))


def _refutation_text(top: SourceAssessment) -> str:
    word = "contradicts" if top.verdict == "contradicted" else "conflicts with"
    return f"a page that {word} the claim was found: {registrable_domain(top.url)}. {top.verify.explanation if top.verify else ''}".strip()


def _attributed_after_factcheck(claim, base: ClaimAssessment, sources: list[SourceAssessment], who: str, settings) -> ClaimAssessment:
    """Refutation counts only with a verified quote (verify_claim downgrades anything else). Pages that confirm the facts follow the
    existing corroboration policy (neutral unless CORROBORATION_COUNTS). Both kinds of page: the sources disagree, neutral."""
    refuting = sorted((sa for sa in sources if sa.verdict in _REFUTING and sa.verify and sa.verify.has_evidence), key=_quality)
    confirming = sorted((sa for sa in sources if sa.verdict == "supported"), key=_quality)
    keep = settings.max_evidence_links
    if refuting and not confirming:
        top = refuting[0]
        return ClaimAssessment(claim, top.verdict, None, f"The original statement could not be found, and {_refutation_text(top)}",
                               list(base.sources) + refuting[:keep], top, base.plan)
    if refuting:
        return ClaimAssessment(claim, "not_checkable", None, "Sources disagree: some pages confirm this claim, others contradict it, "
                               "so it is not held against the article.", list(base.sources) + refuting[:1] + confirming[:1], None, base.plan)
    if confirming:
        for sa in confirming[:keep]:
            sa.corroborated = True
        top = confirming[0]
        trusted = next((sa for sa in confirming if _is_trusted_corroborator(sa.url)), None)
        if settings.corroboration_counts or trusted is not None:
            chosen = trusted or top
            verdict = "supported"
            expl = (f"The original statement attributed to {who} could not be found, and independent reporting by "
                    f"{registrable_domain(chosen.url)} confirms the facts.")
            return ClaimAssessment(claim, verdict, None, expl, list(base.sources) + confirming[:keep], chosen, base.plan)
        expl = (f"{NOT_VERIFIED} Independent sources confirm the facts, but the original statement attributed to {who} could not be "
                f"found: {registrable_domain(top.url)}.")
        return ClaimAssessment(claim, "not_checkable", None, expl, list(base.sources) + confirming[:keep], None, base.plan)
    return base


def _found_assessment(claim: ExtractedClaim, sources: list[SourceAssessment], plan: SearchPlan | None, settings: Settings) -> ClaimAssessment:
    found = [sa for sa in sources if sa.verdict == "supported"]
    found.sort(key=lambda sa: (-(sa.verify.fuzzy_score or 0) if sa.verify else 0))
    found = found[: settings.max_evidence_links]
    refuting = sorted((sa for sa in sources if sa.verdict in _REFUTING and sa.verify and sa.verify.has_evidence), key=_quality)
    if refuting and not found:       # only the fact-check agent's pages can be here; the quote is verified like every verdict
        top = refuting[0]
        return ClaimAssessment(claim, top.verdict, None, f"{NO_SOURCE} Checked against other sources, {_refutation_text(top)}",
                               refuting[: settings.max_evidence_links], top, plan)
    if refuting:
        return ClaimAssessment(claim, "not_checkable", None, f"{NO_SOURCE} Sources disagree: some pages confirm the claim, others "
                               "contradict it, so it is not held against the article.", found[:1] + refuting[:1], None, plan)
    if not found:
        return ClaimAssessment(claim, "unsourced", None, NO_SOURCE, plan=plan)
    top = found[0]
    quote = (top.verify.quote_original or "")[:160] if top.verify else ""
    expl = f"{NO_SOURCE} A page that supports it was found: {registrable_domain(top.url)}" + (f" (\u201c{quote}\u201d)." if quote else ".")
    return ClaimAssessment(claim, "supported", None, expl, sources=found, best=top, plan=plan)


def _is_unsourced_type(claim: ExtractedClaim) -> bool:
    return not claim.links and not claim.attributions


def _cross_check(claims: list[ExtractedClaim], results: list[ClaimAssessment], idxs: list[int], article_url: str, *,
                 cache: _FetchCache, llm: LLMClient, settings: Settings, published_at, article_text: str) -> None:
    """A page found for one claim can support another unsourced one (live example: the Destatis page found for claim 3
    or a study DOI linked in an earlier paragraph). Every claim that is still `unsourced` is checked against the supported
    pages from other claims (found unsourced pages prioritised first, then other verified sources), at most MAX_CROSS_PAGES
    per claim. `results` is updated in place; a claim nothing confirms stays `unsourced`."""
    pool: dict[str, tuple[int, float]] = {}
    for a, c in zip(results, claims):
        tier = 0 if _is_unsourced_type(c) else 1
        for sa in a.sources:
            if sa.url and sa.verdict == "supported":
                score = (sa.verify.fuzzy_score or 0.0) if sa.verify else 0.0
                prev = pool.get(sa.url)
                if prev is None or (tier, -score) < (prev[0], -prev[1]):
                    pool[sa.url] = (tier, score)
    if not pool:
        return
    ranked = sorted(pool, key=lambda u: (pool[u][0], -pool[u][1]))[:MAX_CROSS_PAGES]
    jobs = [(i, u) for i in idxs for u in ranked]
    if not jobs:
        return
    end = time.monotonic() + CROSS_DEADLINE_S
    with ThreadPoolExecutor(max_workers=min(len(jobs), 4), thread_name_prefix="cross") as ex:
        futs = [(i, ex.submit(_assess_source, claims[i], u, None, article_url, cache, llm, settings, published_at,
                              plan=results[i].plan, article_text=article_text, found_unsourced=True)) for i, u in jobs]
        got: dict[int, list[SourceAssessment]] = {}
        for i, fut in futs:
            try:
                got.setdefault(i, []).append(fut.result(timeout=max(end - time.monotonic(), 0.0)))
            except Exception as exc:          # timeout or failed call: the claim simply stays unsourced
                log.info("cross-check for claim %s skipped: %s", claims[i].id, type(exc).__name__)
                fut.cancel()
    for i, found in got.items():
        results[i] = _found_assessment(claims[i], found, results[i].plan, settings)


def _cross_check_attributed(claims: list[ExtractedClaim], results: list[ClaimAssessment], idxs: list[int],
                            article_url: str, *, cache: _FetchCache, llm: LLMClient, settings: Settings,
                            published_at, article_text: str) -> None:
    """Check `not_checkable` attributed claims against supported/corroborated sources already verified for other
    claims naming the same speaker/study."""
    attr_keys = [{k for a in c.attributions if (k := _name_key(a))} for c in claims]
    jobs: list[tuple[int, str, str]] = []
    for i in idxs:
        already = {sa.url for sa in results[i].sources if sa.url}
        pool: dict[str, tuple[int, int, float]] = {}
        for j, a in enumerate(results):
            if j == i or not (attr_keys[i] & attr_keys[j]):
                continue
            for sa in a.sources:
                if not sa.url or sa.url in already or (sa.verdict != "supported" and not sa.corroborated):
                    continue
                rank = (0 if sa.verdict == "supported" else 1, 0 if sa.venue_hosted else 1,
                        -((sa.verify.fuzzy_score or 0.0) if sa.verify else 0.0))
                prev = pool.get(sa.url)
                if prev is None or rank < prev:
                    pool[sa.url] = rank
        ranked = sorted(pool, key=pool.__getitem__)[:MAX_CROSS_PAGES]
        who = (results[i].plan.speaker if results[i].plan and results[i].plan.speaker else None) or claims[i].attributions[0]
        for u in ranked:
            jobs.append((i, u, who))
    if not jobs:
        return
    end = time.monotonic() + CROSS_DEADLINE_S
    with ThreadPoolExecutor(max_workers=min(len(jobs), 4), thread_name_prefix="cross-attr") as ex:
        futs = [(i, ex.submit(_assess_source, claims[i], u, who, article_url, cache, llm, settings, published_at,
                              plan=results[i].plan, article_text=article_text)) for i, u, who in jobs]
        got: dict[int, list[SourceAssessment]] = {}
        for i, fut in futs:
            try:
                sa = fut.result(timeout=max(end - time.monotonic(), 0.0))
                if sa.verdict == "supported" or sa.corroborated:
                    got.setdefault(i, []).append(sa)
            except Exception as exc:
                log.info("attributed cross-check for claim %s skipped: %s", claims[i].id, type(exc).__name__)
                fut.cancel()
    for i, hits in got.items():
        plan = results[i].plan
        sources = list(results[i].sources) + hits
        a = _aggregate(claims[i], sources)
        a.plan = plan
        results[i] = a


def _is_trusted_corroborator(url: str | None) -> bool:
    """True when a corroborating URL belongs to a WP:RSP generally-reliable outlet outside documented networks."""
    if not url or match_network(url) is not None:
        return False
    dom = registrable_domain(url)
    rows = snapshots().rsp.get(dom, [])
    return bool(rows) and any("generally_reliable" in row.get("statuses", []) and not row.get("blacklisted") for row in rows) and not any(
        s in ("generally_unreliable", "deprecated") for row in rows for s in row.get("statuses", [])
    )


def _assess_source(claim: ExtractedClaim, url: str, attribution: str | None, article_url: str, cache: _FetchCache,
                   llm: LLMClient, settings: Settings, published_at: datetime | None = None,
                   loose_link: bool = False, plan: SearchPlan | None = None, cand: Candidate | None = None,
                   article_text: str = "", found_unsourced: bool = False, factcheck: bool = False,
                   anchor_text: str | None = None) -> SourceAssessment:
    fetch = cache.get(url)
    kind = "named" if (attribution or found_unsourced or factcheck) else fetch.kind
    searched = bool(attribution) or found_unsourced or factcheck   # a page we FOUND, not one the article cites
    venue_hosted, phrase_hit = (cand.venue_hosted, cand.phrase_hit) if cand else (False, False)
    agent_kind = cand.agent_kind if cand else None
    if fetch.status != "ok":
        notes = {"not_fetched": "Self-citation or social media post: not independent evidence."}
        return SourceAssessment(url, kind, attribution, fetch, None, "not_checkable",
                                notes.get(fetch.status, f"Source could not be read ({fetch.status})."),
                                venue_hosted=venue_hosted, phrase_hit=phrase_hit, agent_kind=agent_kind)
    try:
        # Search results can be stale (a 2025 page, an earlier forum): tell the model when the claim was reported, and
        # which occasion / venue the article itself names (a May interview is older than a November article and right)
        def judge(model: str | None = None) -> VerifyResult:
            if factcheck:
                return verify_claim(claim.text, fetch.text or "", llm=llm, settings=settings, factcheck=True, model=model)
            return verify_claim(
                claim.text, fetch.text or "", llm=llm, settings=settings,
                article_date=published_at.date() if (published_at and searched) else None,
                occasion=plan.occasion if (plan and searched) else None,
                venue=plan.venue if (plan and searched) else None,
                anchor_text=anchor_text if not searched else None, model=model)

        v = _screen(judge, settings) if (searched and not factcheck and not venue_hosted and not phrase_hit and fetch.text) else None
        if v is None:
            v = judge()
    except LLMError as exc:
        log.warning("verify failed for %s: %s", url, exc)
        return SourceAssessment(url, kind, attribution, fetch, None, "not_checkable", "Verification could not be completed.",
                                venue_hosted=venue_hosted, phrase_hit=phrase_hit, agent_kind=agent_kind)
    verdict, note, corroborated = v.verdict, "", False
    # the venue's own page (Fox Business's report of its own interview) is first-hand even when the model calls it a report
    original_like = v.source_type == "original" or venue_hosted
    if factcheck:
        # The agent was asked for pages that confirm OR refute the claim. The verify model judged the FACTS (fact-check mode) and
        # the quote is fuzzy-checked in verify_claim; a refutation without a verified quote is already `not_checkable`.
        if verdict == "supported" and article_text:
            share = syndication_share(article_text, claim.text, fetch.text or "")
            if share is not None and share >= SYNDICATION_THRESHOLD:
                verdict, note = "not_checkable", "The page found is a copy or syndication of the article itself, not independent reporting."
        elif verdict not in ("supported", *_REFUTING):
            verdict, note = "not_checkable", "A page was found but it neither confirms nor refutes this claim."
        return SourceAssessment(url, kind, attribution, fetch, v, verdict, note, venue_hosted=venue_hosted, phrase_hit=phrase_hit,
                                agent_kind=agent_kind, factchecked=True)
    if found_unsourced:
        # No source named: ANY page that supports the claim is a useful source for the reader, original or independent
        # report. It must be independent of the article (a syndicated copy is the article itself); a page that does not
        # support the claim says nothing about the article.
        if verdict == "supported" and article_text:
            share = syndication_share(article_text, claim.text, fetch.text or "")
            if share is not None and share >= SYNDICATION_THRESHOLD:
                verdict, note = "not_checkable", "The page found is a copy or syndication of the article itself, not independent reporting."
        elif verdict != "supported":
            verdict, note = "not_checkable", "A page was found but it does not support this claim."
    elif kind == "named" and not original_like:
        # Another outlet, not the statement itself. It can corroborate; it counts when configured or WP:RSP generally_reliable.
        corroborated = v.verdict == "supported"
        if corroborated and article_text:
            # A wire story is republished by dozens of sites. Such a copy is the article itself, not independent
            # reporting (measured: 15% of "confirming" pages were copies, e.g. four mirrors of one Reuters text).
            share = syndication_share(article_text, claim.text, fetch.text or "")
            if share is not None and share >= SYNDICATION_THRESHOLD:
                return SourceAssessment(url, kind, attribution, fetch, v, "not_checkable",
                                        NOT_VERIFIED + " The page found is a copy or syndication of the article itself, not independent reporting.",
                                        venue_hosted=venue_hosted, phrase_hit=phrase_hit, agent_kind=agent_kind)
        if corroborated and (settings.corroboration_counts or _is_trusted_corroborator(url)):
            verdict = "supported"
            if not settings.corroboration_counts:
                v = dataclasses.replace(
                    v,
                    explanation=f"Independent reporting by {registrable_domain(url)} confirms this attributed statement: {v.explanation}",
                )
        else:
            verdict = "not_checkable"
            note = ("Independent reporting confirms the quote, but the original (the interview, transcript or statement "
                    "itself) could not be found." if corroborated else NOT_VERIFIED)
    elif loose_link and verdict != "supported":
        # The link sits in the claim's PARAGRAPH, not its sentence (DECISIONS Q18 allows both). Live example: one link
        # at the end of a paragraph belonged to the last sentence, and the two sentences before it were judged
        # "not_mentioned" against it. A loosely attached link can confirm a claim, never count against it.
        verdict = "not_checkable"
        note = ("This page is not cited for this exact claim (it is linked elsewhere in the paragraph, or cited by a page the "
                "article cites), and it does not confirm the claim; this is not held against the article.")
    elif kind == "named" and verdict != "supported":
        # An Exa match is a GUESS at the document the article refers to. Live example: a ministry briefing from
        # BEFORE a trip was "distorted" against a report written after it. Only a match that confirms the claim
        # counts; any mismatch says nothing about the article.
        verdict = "not_checkable"
        note = (NOT_VERIFIED + " A page was found but it does not contain this quote." if v.verdict == "not_mentioned" else
                NOT_VERIFIED + " A page was found but it differs from the claim, so it cannot be confirmed to be what the article refers to.")
    return SourceAssessment(url, kind, attribution, fetch, v, verdict, note, corroborated,
                            venue_hosted=venue_hosted, phrase_hit=phrase_hit, agent_kind=agent_kind)


def _screen(judge: Callable[..., VerifyResult], settings: Settings) -> VerifyResult | None:
    """Candidate screen: one cheap-model read of a page SEARCH found, same prompt and quote check as the real verify. Returns
    its result only when the page does not mention the claim (`not_mentioned`, not downgraded): such a page is neutral anyway
    (`not_checkable`, "a page was found but it does not ..."), so MODEL_VERIFY is not asked. Anything else, an error or an
    inconclusive answer returns None and the page goes on to MODEL_VERIFY, which decides every supported / distorted /
    contradicted. The audit trail keeps the screen's model on the skipped page."""
    model = settings.screen_model
    if not model or model == settings.model_verify:
        return None
    try:
        sv = judge(model)
    except LLMError as exc:
        log.info("candidate screen failed, verifying with the full model: %s", exc)
        return None
    return sv if sv.verdict == "not_mentioned" and not sv.downgraded else None


def _aggregate(claim: ExtractedClaim, sources: list[SourceAssessment]) -> ClaimAssessment:
    def key(sa: SourceAssessment):
        score = sa.verify.fuzzy_score if sa.verify and sa.verify.fuzzy_score else 0
        return (_RANK[sa.verdict], score)

    best = max(sources, key=key)
    first_level = [sa for sa in sources if sa.depth == 1]
    if best.verdict == "not_checkable":
        subtype = None
        if first_level and all(sa.fetch and sa.fetch.subtype for sa in first_level):
            subtype = first_level[0].fetch.subtype if len({sa.fetch.subtype for sa in first_level}) == 1 else None
        corroborating = next((sa for sa in sources if sa.corroborated), None)
        if corroborating:
            best = corroborating
        expl = best.note or (best.verify.explanation if best.verify else "")
        return ClaimAssessment(claim, "not_checkable", subtype, expl, sources, best)
    expl = best.verify.explanation if best.verify else best.note
    if best.depth == 2 and best.parent_url:        # confirmed through the article's own earlier piece
        expl = (f"The article cites its own earlier piece ({registrable_domain(best.parent_url)}), which cites "
                f"{registrable_domain(best.url)}: {expl}")
    return ClaimAssessment(claim, best.verdict, None, expl, sources, best)


def audit_records(a: ClaimAssessment, claim_pk: int, source_id: Callable[[SourceAssessment], str | None]) -> list[ClaimVerdictRecord]:
    """One ClaimVerdictRecord per (claim, source): everything needed to reproduce the verdict."""
    rows = []
    for sa in a.sources:
        v = sa.verify
        rows.append(ClaimVerdictRecord(
            claim_pk=claim_pk, source_id=source_id(sa), source_url=sa.url, verdict=sa.verdict,
            subtype=sa.fetch.subtype if sa.fetch else None,
            quote_original=v.quote_original if v else None, quote_translation=v.quote_translation if v else None,
            language=v.language if v else None, fuzzy_score=v.fuzzy_score if v else None,
            llm_verdict=v.llm_verdict if v else None, explanation=(v.explanation if v else sa.note),
            model=v.model if v else "", prompt_version=v.prompt_version if v else "",
        ))
    if not rows:  # unsourced / unlocated named source: still leave a trace
        rows.append(ClaimVerdictRecord(claim_pk=claim_pk, source_id=None, verdict=a.verdict, explanation=a.explanation))
    return rows
