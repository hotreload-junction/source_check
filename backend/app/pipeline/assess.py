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
from ..llm import LLMClient, LLMError
from ..models import ClaimVerdictRecord
from datetime import datetime

from .evidence import Candidate, SearchPlan, gather_candidates, plan_search
from .extract import ExtractedClaim
from .textutil import SYNDICATION_THRESHOLD, syndication_share
from .verify import VerifyResult, verify_claim

log = logging.getLogger("citetrace.assess")

MAX_SOURCES_PER_CLAIM = 3      # keep cost bounded
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
        if self.best and self.best.verify and self.best.verify.has_evidence \
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
    if claim.links:
        sources = [_assess_source(claim, l.url, None, article_url, cache, llm, settings,
                                  loose_link=(l.scope == "paragraph"))
                   for l in claim.links[:MAX_SOURCES_PER_CLAIM]]
        return _aggregate(claim, sources)
    if not claim.attributions:
        return ClaimAssessment(claim, "unsourced", None, "The article gives no source for this claim.")

    speaker = claim.attributions[0]
    plan = plan_search(speaker, claim.text, claim.context, published_at, llm=llm, model=settings.model_extract)
    cands = gather_candidates(plan, claim.text, article_url, published_at, api_key=settings.exa_api_key, exa_client=exa_client,
                              step_cache=step_cache, limit=settings.max_evidence_candidates,
                              include_secondary=settings.verify_secondary)
    who = plan.speaker or speaker
    with ThreadPoolExecutor(max_workers=min(len(cands), 4) or 1) as pool:   # candidates are independent: verify together
        sources = list(pool.map(lambda c: _assess_source(claim, c.hit.url, who, article_url, cache, llm, settings, published_at,
                                                         plan=plan, cand=c, article_text=article_text), cands))
    if not sources:   # attributed but nothing located -> neutral, never "unsourced"
        return ClaimAssessment(claim, "not_checkable", None, f"{NOT_VERIFIED} It is attributed to {who}, but no page containing it was found.",
                               plan=plan)
    a = _aggregate(claim, sources)
    a.plan = plan
    return a


def _assess_source(claim: ExtractedClaim, url: str, attribution: str | None, article_url: str, cache: _FetchCache,
                   llm: LLMClient, settings: Settings, published_at: datetime | None = None,
                   loose_link: bool = False, plan: SearchPlan | None = None, cand: Candidate | None = None,
                   article_text: str = "") -> SourceAssessment:
    fetch = cache.get(url)
    kind = "named" if attribution else fetch.kind
    venue_hosted, phrase_hit = (cand.venue_hosted, cand.phrase_hit) if cand else (False, False)
    if fetch.status != "ok":
        notes = {"not_fetched": "Self-citation or social media post: not independent evidence."}
        return SourceAssessment(url, kind, attribution, fetch, None, "not_checkable",
                                notes.get(fetch.status, f"Source could not be read ({fetch.status})."),
                                venue_hosted=venue_hosted, phrase_hit=phrase_hit)
    try:
        # Search results can be stale (a 2025 page, an earlier forum): tell the model when the claim was reported, and
        # which occasion / venue the article itself names (a May interview is older than a November article and right)
        v = verify_claim(claim.text, fetch.text or "", llm=llm, settings=settings,
                         article_date=published_at.date() if (published_at and attribution) else None,
                         occasion=plan.occasion if (plan and attribution) else None,
                         venue=plan.venue if (plan and attribution) else None)
    except LLMError as exc:
        log.warning("verify failed for %s: %s", url, exc)
        return SourceAssessment(url, kind, attribution, fetch, None, "not_checkable", "Verification could not be completed.",
                                venue_hosted=venue_hosted, phrase_hit=phrase_hit)
    verdict, note, corroborated = v.verdict, "", False
    # the venue's own page (Fox Business's report of its own interview) is first-hand even when the model calls it a report
    original_like = v.source_type == "original" or venue_hosted
    if kind == "named" and not original_like:
        # Another outlet, not the statement itself. It can corroborate; it decides only if the team switched that on.
        corroborated = v.verdict == "supported"
        if corroborated and article_text:
            # A wire story is republished by dozens of sites. Such a copy is the article itself, not independent
            # reporting (measured: 15% of "confirming" pages were copies, e.g. four mirrors of one Reuters text).
            share = syndication_share(article_text, claim.text, fetch.text or "")
            if share is not None and share >= SYNDICATION_THRESHOLD:
                return SourceAssessment(url, kind, attribution, fetch, v, "not_checkable",
                                        NOT_VERIFIED + " The page found is a copy or syndication of the article itself, not independent reporting.",
                                        venue_hosted=venue_hosted, phrase_hit=phrase_hit)
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
        note = ("The article links this page elsewhere in the same paragraph, not for this exact sentence, and it does "
                "not confirm the claim; this is not held against the article.")
    elif kind == "named" and verdict != "supported":
        # An Exa match is a GUESS at the document the article refers to. Live example: a ministry briefing from
        # BEFORE a trip was "distorted" against a report written after it. Only a match that confirms the claim
        # counts; any mismatch says nothing about the article.
        verdict = "not_checkable"
        note = (NOT_VERIFIED + " A page was found but it does not contain this quote." if v.verdict == "not_mentioned" else
                NOT_VERIFIED + " A page was found but it differs from the claim, so it cannot be confirmed to be what the article refers to.")
    return SourceAssessment(url, kind, attribution, fetch, v, verdict, note, corroborated,
                            venue_hosted=venue_hosted, phrase_hit=phrase_hit)


def _aggregate(claim: ExtractedClaim, sources: list[SourceAssessment]) -> ClaimAssessment:
    def key(sa: SourceAssessment):
        score = sa.verify.fuzzy_score if sa.verify and sa.verify.fuzzy_score else 0
        return (_RANK[sa.verdict], score)

    best = max(sources, key=key)
    if best.verdict == "not_checkable":
        subtype = None
        if all(sa.fetch and sa.fetch.subtype for sa in sources):
            subtype = sources[0].fetch.subtype if len({sa.fetch.subtype for sa in sources}) == 1 else None
        corroborating = next((sa for sa in sources if sa.corroborated), None)
        if corroborating:
            best = corroborating
        expl = best.note or (best.verify.explanation if best.verify else "")
        return ClaimAssessment(claim, "not_checkable", subtype, expl, sources, best)
    return ClaimAssessment(claim, best.verdict, None, best.verify.explanation if best.verify else best.note, sources, best)


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
