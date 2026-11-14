"""B4: assess one claim end to end: collect sources -> fetch -> verify -> aggregate to ONE claim verdict.

Source collection (DECISIONS Q18 + leo's answer on the board):
- links in the claim's sentence/paragraph are the sources;
- a claim without links but with named attributions: Exa resolves each attribution to a URL; if the original
  is found it is fetched and verified like any source, else the claim is `not_checkable` (neutral);
- no links and no attribution -> `unsourced`.
Neutral outcomes never count against an article: unreachable/paywall/blocked, social_media, self_citation,
unlocated named sources, failed LLM calls, and negative verdicts the code could not back with a verified quote.
"""
from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Callable

import httpx

from ..config import Settings, get_settings
from ..fetch import Fetcher, FetchResult
from ..llm import LLMClient, LLMError
from ..models import ClaimVerdictRecord
from .exa import resolve_named_source
from .extract import ExtractedClaim
from .verify import VerifyResult, verify_claim

log = logging.getLogger("citetrace.assess")

MAX_ATTRIBUTIONS = 2           # Exa lookups per claim
MAX_SOURCES_PER_CLAIM = 3      # keep cost bounded
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


@dataclass
class ClaimAssessment:
    claim: ExtractedClaim
    verdict: str                    # contract ClaimVerdict
    subtype: str | None
    explanation: str
    sources: list[SourceAssessment] = field(default_factory=list)
    best: SourceAssessment | None = None   # the source whose evidence the verdict rests on

    @property
    def evidence(self) -> VerifyResult | None:
        return self.best.verify if self.best and self.best.verify and self.best.verify.has_evidence else None


class _FetchCache:
    """Same URL cited by several claims is fetched once (rate limit + cost)."""

    def __init__(self, fetcher: Fetcher, article_url: str):
        self.fetcher, self.article_url = fetcher, article_url
        self._data: dict[str, FetchResult] = {}
        self._locks: dict[str, threading.Lock] = {}
        self._guard = threading.Lock()

    def get(self, url: str) -> FetchResult:
        with self._guard:
            lock = self._locks.setdefault(url, threading.Lock())
        with lock:
            if url not in self._data:
                self._data[url] = self.fetcher.fetch_source(url, self.article_url)
            return self._data[url]


def assess_claims(claims: list[ExtractedClaim], article_url: str, *, fetcher: Fetcher, llm: LLMClient,
                  settings: Settings | None = None, exa_client: httpx.Client | None = None,
                  max_workers: int = 3) -> list[ClaimAssessment]:
    s = settings or get_settings()
    cache = _FetchCache(fetcher, article_url)
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = [pool.submit(assess_claim, c, article_url, cache=cache, llm=llm, settings=s, exa_client=exa_client)
                   for c in claims]
        return [f.result() for f in futures]


def assess_claim(claim: ExtractedClaim, article_url: str, *, cache: _FetchCache, llm: LLMClient,
                 settings: Settings, exa_client: httpx.Client | None = None) -> ClaimAssessment:
    candidates: list[tuple[str | None, str | None]] = [(l.url, None) for l in claim.links][:MAX_SOURCES_PER_CLAIM]
    unresolved: list[str] = []
    if not candidates:
        for attribution in claim.attributions[:MAX_ATTRIBUTIONS]:
            hit = resolve_named_source(attribution, claim.text, api_key=settings.exa_api_key,
                                       client=exa_client, exclude_domain=article_url)
            if hit:
                candidates.append((hit.url, attribution))
            else:
                unresolved.append(attribution)
    if not candidates:
        if unresolved:   # leo: attributed but original not found -> neutral
            names = ", ".join(unresolved)
            return ClaimAssessment(claim, "not_checkable", None,
                                   f"The claim is attributed to {names}, but no original source could be located.")
        return ClaimAssessment(claim, "unsourced", None, "The article gives no source for this claim.")

    sources = [_assess_source(claim, url, attribution, article_url, cache, llm, settings) for url, attribution in candidates]
    return _aggregate(claim, sources)


def _assess_source(claim: ExtractedClaim, url: str, attribution: str | None, article_url: str, cache: _FetchCache,
                   llm: LLMClient, settings: Settings) -> SourceAssessment:
    fetch = cache.get(url)
    kind = "named" if attribution else fetch.kind
    if fetch.status != "ok":
        notes = {"not_fetched": "Self-citation or social media post: not independent evidence."}
        return SourceAssessment(url, kind, attribution, fetch, None, "not_checkable",
                                notes.get(fetch.status, f"Source could not be read ({fetch.status})."))
    try:
        v = verify_claim(claim.text, fetch.text or "", llm=llm, settings=settings)
    except LLMError as exc:
        log.warning("verify failed for %s: %s", url, exc)
        return SourceAssessment(url, kind, attribution, fetch, None, "not_checkable", "Verification could not be completed.")
    verdict = v.verdict
    note = ""
    if kind == "named" and verdict == "not_mentioned":
        # An Exa match is a guess at the original. Not finding the claim in it says nothing about the article.
        verdict, note = "not_checkable", "A possible original source was found but does not cover this claim."
    return SourceAssessment(url, kind, attribution, fetch, v, verdict, note)


def _aggregate(claim: ExtractedClaim, sources: list[SourceAssessment]) -> ClaimAssessment:
    def key(sa: SourceAssessment):
        score = sa.verify.fuzzy_score if sa.verify and sa.verify.fuzzy_score else 0
        return (_RANK[sa.verdict], score)

    best = max(sources, key=key)
    if best.verdict == "not_checkable":
        subtype = None
        if all(sa.fetch and sa.fetch.subtype for sa in sources):
            subtype = sources[0].fetch.subtype if len({sa.fetch.subtype for sa in sources}) == 1 else None
        expl = best.verify.explanation if best.verify else best.note
        return ClaimAssessment(claim, "not_checkable", subtype, expl, sources, best)
    return ClaimAssessment(claim, best.verdict, None, best.verify.explanation if best.verify else best.note, sources, best)


def audit_records(a: ClaimAssessment, claim_pk: int, source_id: Callable[[SourceAssessment], str | None]) -> list[ClaimVerdictRecord]:
    """One ClaimVerdictRecord per (claim, source): everything needed to reproduce the verdict."""
    rows = []
    for sa in a.sources:
        v = sa.verify
        rows.append(ClaimVerdictRecord(
            claim_pk=claim_pk, source_id=source_id(sa), verdict=sa.verdict,
            subtype=sa.fetch.subtype if sa.fetch else None,
            quote_original=v.quote_original if v else None, quote_translation=v.quote_translation if v else None,
            language=v.language if v else None, fuzzy_score=v.fuzzy_score if v else None,
            llm_verdict=v.llm_verdict if v else None, explanation=(v.explanation if v else sa.note),
            model=v.model if v else "", prompt_version=v.prompt_version if v else "",
        ))
    if not rows:  # unsourced / unlocated named source: still leave a trace
        rows.append(ClaimVerdictRecord(claim_pk=claim_pk, source_id=None, verdict=a.verdict, explanation=a.explanation))
    return rows
