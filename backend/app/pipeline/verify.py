"""B4: verify one claim against one fetched source. QUOTE FIRST, verdict second (AGENTS.md section 7).

The LLM must return the evidence passage verbatim. Code fuzzy-matches it against the fetched source text
(rapidfuzz, FUZZY_THRESHOLD). An unverifiable quote can never carry a verdict: `supported` AND the negative
verdicts are downgraded to `not_checkable` (neutral). Nothing the LLM says about a source is trusted unchecked.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date

from rapidfuzz import fuzz

from ..config import Settings, get_settings
from ..llm import LLMClient, LLMError
from .textutil import build_plain

log = logging.getLogger("citetrace.verify")

MAX_SOURCE_CHARS = 60_000        # what the model sees; the fuzzy check runs on the full text
MIN_QUOTE_CHARS = 25             # shorter "quotes" match by accident
LLM_VERDICTS = {"supported", "distorted", "contradicted", "not_mentioned"}

SYSTEM_PROMPT = """You check whether a SOURCE text supports a CLAIM taken from a news article. Be strict and literal.

First find the passage in the SOURCE that is most relevant to the claim and copy it VERBATIM (character for character, in the source's own language, at most 3 sentences). Only then give the verdict.

Verdicts:
- "supported": the passage you quote itself makes the CORE assertion of the claim (same event, figure, speaker and meaning). A passage on the same topic that does not say this is NOT support. Peripheral details the claim adds that the source simply does not mention (the weekday, a background figure such as a population, a place name) do NOT matter, as long as nothing in the source conflicts with them.
- "distorted": the source covers the same matter, but something it states CONFLICTS with the claim or changes its meaning: a different number, date or name, the wrong speaker, a stronger or weaker wording ("may" turned into "will"), a dropped qualifier that changes the sense, an opinion presented as fact. A detail that is merely missing from the source is NOT distortion.
- "contradicted": the source says the opposite of the claim.
- "not_mentioned": the core assertion is not in the source (also when the source only touches a loosely related topic). Use an empty quote.

Rules:
- If an ARTICLE DATE is given, the claim was reported then. When the SOURCE is about a clearly different occasion or time (another year, an earlier meeting, an older statement, a different office-holder), it does not support the claim: answer "not_mentioned".
- If an OCCASION is given, it is what the article itself names for the claim (e.g. an interview with a named outlet in a named month). A source from THAT occasion is the right one even when it is older than the article; a source from another occasion is not.
- When the claim says that someone SAID, WARNED, ANNOUNCED or CLAIMED something, the passage you quote must show THAT person saying it (their own words, or a report of what they said). A question put to them, or the interviewer's own framing, is not the statement: if only that is there, answer "not_mentioned".
- Never invent or "clean up" the quote. If you cannot copy a passage that exists in the SOURCE, use "not_mentioned" with an empty quote.
- A claim that is merely plausible or consistent with the source is NOT supported; the passage must say it.
- "quote_translation": English translation of the quote, or null if the quote is already English.
- "language": ISO 639-1 code of the quote (e.g. "en", "ru").
- "explanation": ONE plain-English sentence on why this verdict.
- "source_type": what kind of text the SOURCE is.
  "original" = first-hand material: an official statement or press release, the report/document itself, a transcript, or an interview published as questions and answers. Also first-hand: a page published by the outlet or institution that a NAMED VENUE line gives as the place of the statement, when it reproduces what was said there (that outlet's own interview report, transcript or video page).
  "secondary" = a news article, blog or summary that reports or quotes what someone else said or published (even if it quotes accurately).
  "unclear" = cannot tell.

Answer with JSON only, keys in this order:
{"quote_original": "...", "quote_translation": null, "language": "en", "verdict": "...", "explanation": "...", "source_type": "original|secondary|unclear"}"""


@dataclass
class VerifyResult:
    verdict: str                    # contract ClaimVerdict (after code-side guards)
    llm_verdict: str | None         # what the model said before guards
    quote_original: str | None
    quote_translation: str | None
    language: str | None
    fuzzy_score: float | None       # 0-100 vs the fetched source text; None if no quote
    explanation: str
    model: str
    prompt_version: str
    downgraded: bool = False
    source_type: str = "unclear"     # original | secondary | unclear (LLM judgement, informational for linked sources)

    @property
    def has_evidence(self) -> bool:
        return bool(self.quote_original) and self.verdict in ("supported", "distorted", "contradicted")


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.replace(" ", " ")).strip()


def fuzzy_score(quote: str, source_text: str) -> float:
    """partial_ratio of the quote against the source (contract: Evidence.fuzzy_score)."""
    plain = _norm(build_plain(source_text).text)
    return float(fuzz.partial_ratio(_norm(quote), plain)) if plain else 0.0


def verify_claim(claim_text: str, source_text: str, *, llm: LLMClient | None = None,
                 settings: Settings | None = None, article_date: date | None = None, occasion: str | None = None,
                 venue: str | None = None) -> VerifyResult:
    s = settings or get_settings()
    llm = llm or LLMClient.from_settings(s)
    plain_claim = build_plain(claim_text).text
    shown = build_plain(source_text).text
    truncated = len(shown) > MAX_SOURCE_CHARS
    res = llm.chat_json(
        s.model_verify, SYSTEM_PROMPT,
        (f"ARTICLE DATE: {article_date.isoformat()}\n" if article_date else "")
        + (f"OCCASION: {occasion}\n" if occasion else "")
        + (f"NAMED VENUE: {venue}\n" if venue else "")
        + ("\n" if (article_date or occasion or venue) else "")
        + f"CLAIM:\n{plain_claim}\n\nSOURCE:\n{shown[:MAX_SOURCE_CHARS]}",
    )
    d = res.data
    verdict = d.get("verdict") if d.get("verdict") in LLM_VERDICTS else None
    if verdict is None:
        raise LLMError(f"verify output has no valid verdict: {str(d)[:120]}")
    quote = d.get("quote_original") if isinstance(d.get("quote_original"), str) else ""
    quote = _norm(quote)
    translation = d.get("quote_translation") if isinstance(d.get("quote_translation"), str) else None
    lang = d.get("language") if isinstance(d.get("language"), str) else None
    expl = d.get("explanation") if isinstance(d.get("explanation"), str) else ""
    stype = d.get("source_type") if d.get("source_type") in ("original", "secondary", "unclear") else "unclear"
    out = VerifyResult(verdict, verdict, quote or None, translation, lang, None, expl.strip(),
                       res.model, s.prompt_version, source_type=stype)

    if verdict == "not_mentioned":
        out.quote_original = out.quote_translation = out.language = None
        if truncated:  # the passage may sit in the part the model never saw
            return _downgrade(out, "Source was too long to read completely, so absence cannot be shown.")
        return out

    # supported / distorted / contradicted all need a quote that really is in the source
    if len(quote) < MIN_QUOTE_CHARS:
        return _downgrade(out, "The model gave no usable evidence passage.")
    out.fuzzy_score = fuzzy_score(quote, source_text)
    if out.fuzzy_score < s.fuzzy_threshold:
        log.info("quote not found in source (score %.0f < %.0f): %r", out.fuzzy_score, s.fuzzy_threshold, quote[:80])
        return _downgrade(out, "The quoted passage could not be found in the source text.")
    return out


def _downgrade(r: VerifyResult, reason: str) -> VerifyResult:
    r.verdict = "not_checkable"
    r.downgraded = True
    r.explanation = f"{reason} (model said: {r.llm_verdict})"
    return r
