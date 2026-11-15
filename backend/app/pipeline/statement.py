"""Self-contained check sentence: the claim as a fact-checker can SEARCH for and VERIFY it without having read the article.

A claim is a verbatim article sentence, so it is often not understandable alone ("Another 31% expect turbulence", "high: more
than 22.5g of total sugars per 100g", "In fact, studies consistently show ..."). The search and the verify model get exactly
that text, which makes both unsharp. One model call per claim decides whether the sentence NEEDS its context and, only if it
does, rewrites it with as few changes as possible. The shown claim, its highlight and its offsets never change; the rewrite
is stored as `Claim.check_text`.

Why it is built this restrictively (measured on cached Guardian / WEF / NHS / Healthline claims):
- a free "make it self-contained" rewrite added editorial content ("despite the relative lack of attention on women") and
  turned 3 of 5 verifiable claims from `supported` into `not_mentioned` against the same page;
- gated by "needs context" and limited to a minimal edit, the verdicts against the same pages did not change (4 of 4 with a
  page in the cache) and no unrelated page became `supported`.
Code guards back that up: the rewrite must keep >= 70% of the sentence's words, may be at most 200 characters longer, and may
contain no number or capitalised name that the article does not contain (a model adding "World Economic Forum" from its own
knowledge is rejected). A rejected, failed or unchanged rewrite means: the original sentence is used.

The same call says whether the sentence is CHECKABLE (opinion, prediction, anecdote, feeling, transition -> not). A claim
that is not checkable is replaced by the next-best candidate of the extraction (`ExtractionResult.alternates`), at most
2 rounds; with no alternate left it stays (it will end up neutral anyway).
"""
from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

from ..config import Settings
from ..llm import LLMClient, LLMError
from .extract import ExtractedClaim, ExtractionResult
from .textutil import attach_links, build_plain, paragraph_bounds

log = logging.getLogger("citetrace.statement")

LEAD_CHARS = 700
MIN_KEPT_WORDS = 0.7
MAX_EXTRA_CHARS = 200
MAX_REFILL_ROUNDS = 2

PROMPT = """You help a fact-checker. You get ONE sentence of a news article with its context and decide whether the sentence can be searched for and verified ON ITS OWN.

You get the article TITLE, the article's OPENING, the SENTENCE, its PARAGRAPH and the PRECEDING PARAGRAPH.

"needs_context": true ONLY if the sentence cannot be searched for or verified on its own because it depends on the surrounding text: a pronoun or reference whose referent is outside the sentence ("it", "they", "the report", "this year" without a year), a connector that stands for earlier content ("Another 31%", "In fact", "However", "Meanwhile"), or a fragment such as a list item without a subject. false if a reader who has not seen the article could search for and verify it as it stands (even if it contains an opinion or is long).

"statement": if needs_context is false, return the sentence UNCHANGED (plain text, no markdown). If true, change AS LITTLE AS POSSIBLE: replace or add only the words needed to resolve the dependency, copy everything else word for word and in the same order. Use ONLY information from the TITLE, the OPENING, the SENTENCE, the PARAGRAPH or the PRECEDING PARAGRAPH. No commentary, no contrast, no extra detail, no correction of the article, no new names, numbers or dates. Keep the language of the article.

"checkable": false for opinion, prediction, rhetorical question, a personal anecdote or feeling, or a pure transition; true for a concrete assertion a source could confirm or refute.

Answer with JSON only: {"needs_context": true, "statement": "...", "checkable": true}"""

_NUM = re.compile(r"\d[\d.,]*%?")
_CAP = re.compile(r"\b[A-Z][\w'’-]{2,}")
_WORD = re.compile(r"\w+")


def _norm(s: str) -> str:
    return s.replace("’", "'").replace("‘", "'").lower()


def guard(original: str, statement: str, article_text: str) -> str | None:
    """None when the rewrite is acceptable, else the reason it is not."""
    if not statement.strip():
        return "empty"
    kept = {w.lower() for w in _WORD.findall(original)}
    new_words = {w.lower() for w in _WORD.findall(statement)}
    if kept and sum(1 for w in kept if w in new_words) / len(kept) < MIN_KEPT_WORDS:
        return "drops too much of the sentence"
    if len(statement) > len(original) + MAX_EXTRA_CHARS:
        return "too long"
    haystack = _norm(article_text)
    for tok in [*_NUM.findall(statement), *_CAP.findall(statement)]:
        key = re.sub(r"'s$", "", _norm(tok).strip(".,"))
        if key and key not in haystack:
            return f"not in the article: {tok}"
    return None


def _ask(claim_text: str, paragraph: str, previous: str, title: str, lead: str, *, llm: LLMClient, settings: Settings) -> dict | None:
    user = (f"TITLE: {title or '(none)'}\n\nOPENING: {lead}\n\nSENTENCE: {build_plain(claim_text).text}\n\n"
            f"PARAGRAPH: {build_plain(paragraph).text[:1500]}\n\nPRECEDING PARAGRAPH: {build_plain(previous).text[:1200] or '(none)'}")
    try:
        data = llm.chat_json(settings.model_extract, PROMPT, user, max_tokens=1500).data
    except LLMError as exc:
        log.info("check sentence failed: %s", exc)
        return None
    return data if isinstance(data, dict) else None


def _previous_paragraph(md: str, start: int, end: int) -> str:
    p0, _ = paragraph_bounds(md, start, end)
    if p0 <= 2:
        return ""
    q0, q1 = paragraph_bounds(md, p0 - 2, p0 - 2)
    return md[q0:q1]


def contextualize(ex: ExtractionResult, article_md: str, title: str, *, llm: LLMClient, settings: Settings,
                  article_url: str = "") -> ExtractionResult:
    """Fill `check_text` of every claim that needs its context, replace claims that are not checkable by the next-best
    candidates. Never raises: any problem leaves the claims as they were."""
    if not settings.contextualize_claims or not ex.claims:
        return ex
    try:
        return _contextualize(ex, article_md, title, llm, settings, article_url)
    except Exception as exc:                      # a bonus step must never break the analysis
        log.warning("contextualize failed: %s", exc)
        return ex


def _contextualize(ex: ExtractionResult, md: str, title: str, llm: LLMClient, s: Settings, article_url: str) -> ExtractionResult:
    lead = build_plain(md[:LEAD_CHARS * 2]).text[:LEAD_CHARS]
    whole = f"{title}\n{md}"
    limit = len(ex.claims)
    alternates = list(ex.alternates)

    def as_claim(i: int) -> ExtractedClaim:
        start, end, _ = ex.units[i]
        return ExtractedClaim(id="", text=md[start:end], char_start=start, char_end=end,
                              links=attach_links(md, start, end, article_url), attributions=list(ex.alternate_attrs.get(i, [])), votes=0,
                              context=md[slice(*paragraph_bounds(md, start, end))][:2000])

    def one(c: ExtractedClaim) -> tuple[ExtractedClaim, dict | None]:
        return c, _ask(c.text, c.context, _previous_paragraph(md, c.char_start, c.char_end), title, lead, llm=llm, settings=s)

    kept: list[ExtractedClaim] = []
    todo = list(ex.claims)
    for _round in range(MAX_REFILL_ROUNDS + 1):
        with ThreadPoolExecutor(max_workers=max(1, min(len(todo), 6)), thread_name_prefix="statement") as pool:
            answers = list(pool.map(one, todo))
        dropped = 0
        for c, a in answers:
            if a is not None and a.get("checkable") is False and dropped < len(alternates):
                dropped += 1
                continue                              # replaced below by the next-best candidate (only when one is left)
            if a is not None and a.get("needs_context") is True and isinstance(a.get("statement"), str):
                reason = guard(build_plain(c.text).text, a["statement"], whole)
                if reason is None and a["statement"].strip() != build_plain(c.text).text.strip():
                    c = replace(c, check_text=a["statement"].strip())
                else:
                    log.info("check sentence for %r not used: %s", c.text[:50], reason or "unchanged")
            kept.append(c)
        if not dropped or _round == MAX_REFILL_ROUNDS:
            break
        todo = []
        while alternates and len(todo) < dropped:
            todo.append(as_claim(alternates.pop(0)))
    kept = sorted(kept, key=lambda c: c.char_start)[:limit]
    for n, c in enumerate(kept):                      # fresh ids in document order
        c.id = f"c{n + 1}"
    return replace(ex, claims=kept, alternates=alternates)
