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
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Callable

import httpx

from ..config import Settings, get_settings
from ..fetch import Fetcher, FetchResult
from ..llm import LLMClient, LLMError
from ..models import ClaimVerdictRecord
from datetime import datetime

from .exa import find_candidates, suggest_official_search
from .extract import ExtractedClaim
from .verify import VerifyResult, verify_claim

log = logging.getLogger("citetrace.assess")

MAX_ATTRIBUTIONS = 2           # Exa lookups per claim
MAX_NAMED_VERIFY = 4           # verify calls spent on Exa candidates per claim
STALE_AFTER_DAYS = 60          # an Exa candidate published this long before the article is a different occasion
FUTURE_TOLERANCE_DAYS = 5
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
    corroborated: bool = False      # named source: a secondary report supports the claim, original not found


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
        """Evidence only when the verdict rests on it (a neutralised secondary report is not evidence)."""
        if self.best and self.best.verify and self.best.verify.has_evidence \
                and self.best.verdict in ("supported", "distorted", "contradicted"):
            return self.best.verify
        return None


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
                  max_workers: int = 3, published_at: datetime | None = None) -> list[ClaimAssessment]:
    s = settings or get_settings()
    cache = _FetchCache(fetcher, article_url)
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = [pool.submit(assess_claim, c, article_url, cache=cache, llm=llm, settings=s, exa_client=exa_client,
                   published_at=published_at)
                   for c in claims]
        return [f.result() for f in futures]


def assess_claim(claim: ExtractedClaim, article_url: str, *, cache: _FetchCache, llm: LLMClient,
                 settings: Settings, exa_client: httpx.Client | None = None,
                 published_at: datetime | None = None) -> ClaimAssessment:
    if claim.links:
        sources = [_assess_source(claim, l.url, None, article_url, cache, llm, settings)
                   for l in claim.links[:MAX_SOURCES_PER_CLAIM]]
        return _aggregate(claim, sources)
    if not claim.attributions:
        return ClaimAssessment(claim, "unsourced", None, "The article gives no source for this claim.")

    hits: list[tuple[str, str]] = []                      # (url, attribution), official-site hits first
    for attribution in claim.attributions[:MAX_ATTRIBUTIONS]:
        for hit in _candidates(attribution, claim, article_url, llm, settings, exa_client, published_at):
            if hit.url not in {u for u, _ in hits}:
                hits.append((hit.url, attribution))
    hits = hits[:MAX_NAMED_VERIFY]
    with ThreadPoolExecutor(max_workers=min(len(hits), 4) or 1) as pool:   # candidates are independent: verify together
        sources = list(pool.map(lambda h: _assess_source(claim, h[0], h[1], article_url, cache, llm, settings, published_at), hits))
    if not sources:   # leo: attributed but nothing located -> neutral, never "unsourced"
        names = ", ".join(claim.attributions[:MAX_ATTRIBUTIONS])
        return ClaimAssessment(claim, "not_checkable", None,
                               f"The claim is attributed to {names}, but no original source could be located.")
    return _aggregate(claim, sources)


def is_contemporary(published: str | None, article_date: datetime | None) -> bool:
    """False only when Exa dates the candidate clearly away from the article (measured live: a 2022 mid.ru release
    and a 2025 president.gov.ua page were "verified" as support for Nov 2026 claims). Unknown date -> True; the
    verify prompt's ARTICLE DATE rule is the second line of defence."""
    if not published or not article_date:
        return True
    try:
        d = datetime.fromisoformat(published.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return True
    delta = (article_date - d).days
    return -FUTURE_TOLERANCE_DAYS <= delta <= STALE_AFTER_DAYS


def _candidates(attribution, claim, article_url, llm, settings, exa_client, published_at):
    """Official-site hits first (model-suggested domains), then the general ranked search; deduplicated."""
    kw = dict(api_key=settings.exa_api_key, client=exa_client, exclude_url=article_url, around=published_at)
    domains, query = suggest_official_search(attribution, claim.text, llm=llm, model=settings.model_extract)
    hits: list = []
    if domains:   # two phrasings: the model's keywords, and the claim itself (keyword guesses can miss, e.g. Starlink/Starshield)
        for q in dict.fromkeys(x for x in (query, f"{attribution} {claim.text}"[:160]) if x):
            hits += [h for h in find_candidates(attribution, claim.text, include_domains=domains, query=q, limit=2, **kw)
                     if h.url not in {x.url for x in hits}]
    seen = {h.url for h in hits}
    hits += [h for h in find_candidates(attribution, claim.text, **kw) if h.url not in seen]
    return [h for h in hits if is_contemporary(h.published, published_at)]


def _assess_source(claim: ExtractedClaim, url: str, attribution: str | None, article_url: str, cache: _FetchCache,
                   llm: LLMClient, settings: Settings, published_at: datetime | None = None) -> SourceAssessment:
    fetch = cache.get(url)
    kind = "named" if attribution else fetch.kind
    if fetch.status != "ok":
        notes = {"not_fetched": "Self-citation or social media post: not independent evidence."}
        return SourceAssessment(url, kind, attribution, fetch, None, "not_checkable",
                                notes.get(fetch.status, f"Source could not be read ({fetch.status})."))
    try:
        # Exa guesses can be stale (a 2025 page, an earlier forum): tell the model when the claim was reported
        v = verify_claim(claim.text, fetch.text or "", llm=llm, settings=settings,
                         article_date=published_at.date() if (published_at and attribution) else None)
    except LLMError as exc:
        log.warning("verify failed for %s: %s", url, exc)
        return SourceAssessment(url, kind, attribution, fetch, None, "not_checkable", "Verification could not be completed.")
    verdict, note, corroborated = v.verdict, "", False
    if kind == "named" and v.source_type != "original":
        # Exa found another outlet, not the original statement. It can corroborate, never decide.
        corroborated = v.verdict == "supported"
        verdict = "not_checkable"
        note = ("Other reporting confirms the statement, but the original source (official statement, transcript "
                "or full interview) could not be found." if corroborated else
                "Only secondary reporting was found, not the original source.")
    elif kind == "named" and verdict != "supported":
        # An Exa match is a GUESS at the document the article refers to. Live example: a ministry briefing from
        # BEFORE a trip was "distorted" against a report written after it. Only a match that confirms the claim
        # counts; any mismatch says nothing about the article.
        verdict = "not_checkable"
        note = ("A possible original source was found but does not cover this claim." if v.verdict == "not_mentioned" else
                "A possible original source was found but differs from the claim; it could not be confirmed to be "
                "the document the article refers to.")
    return SourceAssessment(url, kind, attribution, fetch, v, verdict, note, corroborated)


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
