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
from datetime import datetime

from .depth2 import passage_links
from .evidence import Candidate, SearchPlan, gather_candidates, merge_agent_hits, plan_search
from .exa_agent import AgentResult, build_body, find_sources
from .extract import ExtractedClaim
from .textutil import SYNDICATION_THRESHOLD, syndication_share, build_plain
from .verify import VerifyResult, verify_claim

log = logging.getLogger("citetrace.assess")

MAX_SOURCES_PER_CLAIM = 3      # keep cost bounded
NO_SOURCE = "The article gives no source for this claim."
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
    deadline = time.monotonic() + s.claim_deadline_s
    # claims are independent and almost all wait on the network: run them all at once (max 6 by contract)
    pool = ThreadPoolExecutor(max_workers=max_workers or max(len(claims), 1), thread_name_prefix="claim")
    futures = [pool.submit(assess_claim, c, article_url, cache=cache, llm=llm, settings=s, exa_client=exa_client,
                           published_at=published_at, step_cache=step_cache, article_text=article_text) for c in claims]
    results: list[ClaimAssessment] = []
    for i, (claim, fut) in enumerate(zip(claims, futures)):
        try:
            results.append(fut.result(timeout=max(deadline - time.monotonic(), 0.0)))
        except FutureTimeout:
            # Bounded latency beats completeness for a live demo: neutral, never evidence against the article.
            log.warning("claim %s not finished within %.0fs, marking not_checkable", claim.id, s.claim_deadline_s)
            fut.cancel()
            results.append(ClaimAssessment(claim, "not_checkable", None,
                                           "Checking the sources took too long, so this claim was left unverified."))
        if on_claim:    # called in claim order from this thread: the SSE stream can show each claim as it is final
            on_claim(i, results[-1])
    pool.shutdown(wait=False)      # stragglers finish in the background and are ignored
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
        return _assess_attributed(claim, article_url, **kw)
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
                                                       loose_link=(l.scope == "paragraph"))])
    with ThreadPoolExecutor(max_workers=max(len(jobs), 1), thread_name_prefix="cited") as pool:
        sources = [sa for group in pool.map(lambda j: j(), jobs) for sa in group]
    return _aggregate(claim, sources)


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
def _discover(claim: ExtractedClaim, article_url: str, *, attributed: bool, llm: LLMClient, settings: Settings, exa_client,
              published_at: datetime | None, step_cache) -> tuple[SearchPlan, list[Candidate], AgentResult]:
    """Candidate pages for a claim the article gives no link for: plan, then OUR searches and the Exa Agent side by side,
    merged and ranked. (Measured: equal on originals but different ones; the agent finds far more confirming pages.)"""
    who = claim.attributions[0] if attributed else None
    plan = plan_search(who, claim.text, claim.context, published_at, llm=llm, model=settings.model_extract)
    agent = AgentResult(status="disabled")
    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="discover") as pool:
        agent_future = None
        if settings.exa_agent and settings.exa_api_key:
            body = build_body(claim.text, claim.context, article_url, registrable_domain(article_url), published_at,
                              effort=settings.exa_agent_effort, speaker=plan.speaker, venue=plan.venue,
                              occasion=plan.occasion, attributed=attributed)
            agent_future = pool.submit(find_sources, body, api_key=settings.exa_api_key, article_url=article_url,
                                       timeout_s=settings.exa_agent_timeout_s, concurrency=settings.exa_agent_concurrency,
                                       client=exa_client, cache=step_cache)
        ours = gather_candidates(plan, claim.text, article_url, published_at, api_key=settings.exa_api_key, exa_client=exa_client,
                                 step_cache=step_cache, limit=settings.max_evidence_candidates,
                                 include_secondary=settings.verify_secondary)
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
    plan, cands, _agent = _discover(claim, article_url, attributed=True, llm=llm, settings=settings, exa_client=exa_client,
                                    published_at=published_at, step_cache=step_cache)
    who = plan.speaker or claim.attributions[0]
    sources = _verify_all(claim, cands, who, article_url, cache, llm, settings, published_at, plan, article_text)
    if not sources:   # attributed but nothing located -> neutral, never "unsourced"
        return ClaimAssessment(claim, "not_checkable", None, f"{NOT_VERIFIED} It is attributed to {who}, but no page containing it was found.",
                               plan=plan)
    a = _aggregate(claim, sources)
    a.plan = plan
    return a


def _assess_unsourced(claim, article_url, *, cache, llm, settings, exa_client, published_at, step_cache, article_text) -> ClaimAssessment:
    """The article names NO source. Look for a page that supports the claim anyway. The verdict stays `unsourced`: that is a
    statement about how the ARTICLE sources its claim, not about whether the claim is true. A found page is shown next to the
    claim; one that does not support it is ignored (never held against the article)."""
    plan, cands, _agent = _discover(claim, article_url, attributed=False, llm=llm, settings=settings, exa_client=exa_client,
                                    published_at=published_at, step_cache=step_cache)
    sources = _verify_all(claim, cands, None, article_url, cache, llm, settings, published_at, plan, article_text, found_unsourced=True)
    found = [sa for sa in sources if sa.verdict == "supported"]
    found.sort(key=lambda sa: (-(sa.verify.fuzzy_score or 0) if sa.verify else 0))
    found = found[: settings.max_evidence_links]
    if not found:
        return ClaimAssessment(claim, "unsourced", None, NO_SOURCE, plan=plan)
    top = found[0]
    quote = (top.verify.quote_original or "")[:160] if top.verify else ""
    expl = f"{NO_SOURCE} A page that supports it was found: {registrable_domain(top.url)}" + (f" (\u201c{quote}\u201d)." if quote else ".")
    return ClaimAssessment(claim, "unsourced", None, expl, sources=found, best=top, plan=plan)


def _assess_source(claim: ExtractedClaim, url: str, attribution: str | None, article_url: str, cache: _FetchCache,
                   llm: LLMClient, settings: Settings, published_at: datetime | None = None,
                   loose_link: bool = False, plan: SearchPlan | None = None, cand: Candidate | None = None,
                   article_text: str = "", found_unsourced: bool = False) -> SourceAssessment:
    fetch = cache.get(url)
    kind = "named" if (attribution or found_unsourced) else fetch.kind
    searched = bool(attribution) or found_unsourced            # a page we FOUND, not one the article cites
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
        v = verify_claim(claim.text, fetch.text or "", llm=llm, settings=settings,
                         article_date=published_at.date() if (published_at and searched) else None,
                         occasion=plan.occasion if (plan and searched) else None,
                         venue=plan.venue if (plan and searched) else None)
    except LLMError as exc:
        log.warning("verify failed for %s: %s", url, exc)
        return SourceAssessment(url, kind, attribution, fetch, None, "not_checkable", "Verification could not be completed.",
                                venue_hosted=venue_hosted, phrase_hit=phrase_hit, agent_kind=agent_kind)
    verdict, note, corroborated = v.verdict, "", False
    # the venue's own page (Fox Business's report of its own interview) is first-hand even when the model calls it a report
    original_like = v.source_type == "original" or venue_hosted
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
        # Another outlet, not the statement itself. It can corroborate; it decides only if the team switched that on.
        corroborated = v.verdict == "supported"
        if corroborated and article_text:
            # A wire story is republished by dozens of sites. Such a copy is the article itself, not independent
            # reporting (measured: 15% of "confirming" pages were copies, e.g. four mirrors of one Reuters text).
            share = syndication_share(article_text, claim.text, fetch.text or "")
            if share is not None and share >= SYNDICATION_THRESHOLD:
                return SourceAssessment(url, kind, attribution, fetch, v, "not_checkable",
                                        NOT_VERIFIED + " The page found is a copy or syndication of the article itself, not independent reporting.",
                                        venue_hosted=venue_hosted, phrase_hit=phrase_hit, agent_kind=agent_kind)
        if corroborated and settings.corroboration_counts:
            verdict = "supported"
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
