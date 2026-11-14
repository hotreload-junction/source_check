"""B3: central-claim extraction: numbered sentences + MAJORITY VOTE, selection by code.

Why not "ask the model for the 6 most central claims": measured on real articles, that ranking task is not
reproducible even at temperature 0 (claim sets of repeated runs on IDENTICAL text overlapped only 0.35 to 0.7 on
average). Borderline sentences are taken or skipped at random, and one different claim changes the sources, the
verdicts and finally the article label. Instead:

1. code splits the article into numbered sentences (exact markdown offsets, no fuzzy matching, no copying by the model);
2. the model is asked K times, independently, which sentences are checkable claims (a classification, not a ranking);
3. a sentence counts when MORE THAN HALF of the votes name it; the first `max_claims` of them in document order win.

Held out (DW, Al Jazeera, Guardian, TASS; 5 repeats each): mean overlap 1.00 with K=5 against 0.66 for the single ranked call.
Sources stay code-only: links and attributions come from the sentence / paragraph, never from the model's guess.
"""
from __future__ import annotations

import collections
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Callable

from ..config import Settings, get_settings
from ..llm import LLMClient, LLMError
from .textutil import LinkRef, attach_links, paragraph_bounds, sentence_units

log = logging.getLogger("citetrace.extract")

MAX_ARTICLE_CHARS = 24_000

SYSTEM_PROMPT = """You decide which sentences of a news article are CHECKABLE FACTUAL CLAIMS for a source-checking tool.

A checkable claim is a concrete assertion a source could confirm or refute: an event, a number or statistic, a quoted statement attributed to someone, a statement about what a document, report or official said. NOT checkable: opinions, rhetorical questions, predictions, general background, headlines, bylines, navigation/share/related-article/advertising text.

The article is given as numbered sentences. Answer with JSON only: {"claims": [{"id": <sentence number>, "attributions": ["..."]}]}
List the sentences that are checkable claims. "attributions" = the people or organisations the sentence presents as the SOURCE of the claim, exactly as the article names them: whoever is reported as saying, warning, claiming, announcing, confirming, reporting or suspecting it ("X said ...", "according to Y", "Z warned that ...", "officials suspect ..."), the document or report it cites, the speaker of a quotation. The source counts when it is named only by role ("the Nvidia founder", "the minister", "anonymous US officials"): then give the person's name if the article names them anywhere, else the role as written. A sentence that continues what the previous sentence said or quoted ("it", "he", "she", "they", a quotation) has the same source as that sentence. [] ONLY when the sentence states the matter as plain fact with no source behind it. Do not invent sources. The article may be in any language."""


@dataclass
class ExtractedClaim:
    id: str
    text: str                      # verbatim slice of article_md[char_start:char_end]
    char_start: int
    char_end: int
    links: list[LinkRef] = field(default_factory=list)
    attributions: list[str] = field(default_factory=list)
    locate_score: float = 100.0    # kept for audit compatibility: units are exact slices, never fuzzy-located
    votes: int = 0                 # how many of the K readings named this sentence
    context: str = ""              # the claim's paragraph (markdown): where the occasion ("In a May interview with ...") is named


@dataclass
class ExtractionResult:
    claims: list[ExtractedClaim]
    model: str
    dropped: list[str] = field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    credits: float = 0.0
    n_votes: int = 0               # readings that answered (K minus failures)


def _parse_vote(data: dict, n_units: int) -> dict[int, tuple[str, ...]]:
    raw = data.get("claims")
    if not isinstance(raw, list):
        raise LLMError("extraction output has no 'claims' list")
    out: dict[int, tuple[str, ...]] = {}
    for item in raw:
        cid = item.get("id") if isinstance(item, dict) else item
        try:
            cid = int(cid)
        except (TypeError, ValueError):
            continue
        if not 0 <= cid < n_units:
            continue
        attrs = item.get("attributions") if isinstance(item, dict) else []
        attrs = tuple(a.strip() for a in attrs if isinstance(a, str) and a.strip()) if isinstance(attrs, list) else ()
        out[cid] = attrs
    return out


def extract_claims(article_md: str, article_url: str = "", *, llm: LLMClient | None = None,
                   settings: Settings | None = None, body_filter: Callable[[str], bool] | None = None) -> ExtractionResult:
    """`body_filter`: only sentences of the article's main text are candidates (textutil.article_body_filter), so
    related-article teasers and sidebars can never become central claims. None = the whole page."""
    s = settings or get_settings()
    llm = llm or LLMClient.from_settings(s)
    units = sentence_units(article_md, MAX_ARTICLE_CHARS, keep=body_filter) if body_filter else []
    if not units:     # no filter, or it kept nothing (then it is not trustworthy for this page)
        units = sentence_units(article_md, MAX_ARTICLE_CHARS)
    if not units:
        return ExtractionResult([], s.model_extract)
    numbered = "\n".join(f"[{i}] {text}" for i, (_, _, text) in enumerate(units))
    k = max(1, s.extract_votes)

    def read(i: int):
        # The reading index makes the K requests DIFFERENT (the AI router returns byte-identical requests from its
        # cache, which would turn five votes into one) and stays fixed, so every reading replays from our step cache.
        return llm.chat_json(s.model_extract, SYSTEM_PROMPT,
                             f"Reading {i + 1} of {k}.\nARTICLE (url: {article_url or 'n/a'}):\n\n{numbered}", max_tokens=4000)

    with ThreadPoolExecutor(max_workers=k, thread_name_prefix="vote") as pool:
        futures = [pool.submit(read, i) for i in range(k)]
        results = []
        for f in futures:
            try:
                results.append(f.result())
            except LLMError as exc:
                log.warning("one extraction reading failed: %s", exc)
    votes = []
    for r in results:
        try:
            votes.append(_parse_vote(r.data, len(units)))
        except LLMError as exc:
            log.warning("one extraction reading unusable: %s", exc)
    if not votes:
        raise LLMError("claim extraction failed: no usable reading")

    counts = collections.Counter(i for v in votes for i in v)
    keep = sorted(i for i, c in counts.items() if c * 2 > len(votes))[: s.max_claims]    # strict majority, document order
    claims: list[ExtractedClaim] = []
    for i in keep:
        start, end, _ = units[i]
        claims.append(ExtractedClaim(
            id=f"c{len(claims) + 1}", text=article_md[start:end], char_start=start, char_end=end,
            links=attach_links(article_md, start, end, article_url), attributions=list(_attributions(votes, i)),
            votes=counts[i], context=article_md[slice(*paragraph_bounds(article_md, start, end))][:2000]))
    return ExtractionResult(
        claims, results[0].model if results else s.model_extract, [],
        sum(r.prompt_tokens for r in results), sum(r.completion_tokens for r in results), sum(r.credits for r in results),
        n_votes=len(votes))


_TITLES = re.compile(r"^(?:(?:the|mr|mrs|ms|miss|dr|prof|professor|sen|senator|rep|representative|president|vice president|prime minister|"
                     r"minister|secretary|commerce secretary|chairman|chair|ceo|gen|general|col|colonel|sir|dame|king|queen)\.?\s+)+", re.I)


def _name_key(name: str) -> str:
    """Same person under different spellings: "Senator Tim Scott" / "Tim Scott" / "Sen. Tim Scott" share one key."""
    return re.sub(r"[^\w\s]", "", _TITLES.sub("", name.strip())).casefold().strip()


def _attributions(votes: list[dict[int, tuple[str, ...]]], sentence: int) -> tuple[str, ...]:
    """Sources that a strict majority of the readings which picked this sentence agree on, in first-named order.

    Per PERSON (title-insensitive), not per list: five readings that spell a name differently, phrase the same source
    slightly differently, or disagree whether the sentence names one at all used to flip a claim between `unsourced`
    and `not_checkable` from run to run."""
    readings = [v[sentence] for v in votes if sentence in v]
    seen_by_key: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    support: collections.Counter = collections.Counter()
    for attrs in readings:
        for key in {_name_key(n) for n in attrs if _name_key(n)}:
            support[key] += 1
        for n in attrs:
            if _name_key(n):
                seen_by_key[_name_key(n)][n] += 1
    keep = {k for k, c in support.items() if c * 2 > len(readings)}
    order: list[str] = []
    for attrs in readings:                       # the order the readings gave them in; the first reading that has it wins
        for n in attrs:
            k = _name_key(n)
            if k in keep and k not in {_name_key(o) for o in order}:
                forms = seen_by_key[k]           # the spelling most readings used (ties: the longer, then alphabetical)
                order.append(max(forms, key=lambda f: (forms[f], len(f), f)))
    return tuple(order)
