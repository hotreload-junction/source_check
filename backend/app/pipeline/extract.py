"""B3: central-claim extraction: numbered sentences + MAJORITY VOTE, selection by code.

Why not "ask the model for the 6 most central claims": measured on real articles, that ranking task is not
reproducible even at temperature 0 (claim sets of repeated runs on IDENTICAL text overlapped only 0.35 to 0.7 on
average). Borderline sentences are taken or skipped at random, and one different claim changes the sources, the
verdicts and finally the article label. Instead:

1. code splits the article into numbered sentences (exact markdown offsets, no fuzzy matching, no copying by the model);
2. the model is asked K times, independently, which sentences are checkable claims (a classification, not a ranking);
3. a sentence counts when MORE THAN HALF of the votes name it; of those, sentences that name a source (link or
   attribution) are preferred, `min_unsourced_claims` slots stay reserved for unsourced ones; document order inside.

EXTRACT_MODE=summary (default) replaces step 2 and the preference of step 3: the model SUMMARISES the article into 5-8 key
points, each citing the numbered sentences that state it (plus a few concrete "details" and the sources the sentences name).
Centrality comes from the summary (a classifier labels ~85% of all sentences checkable and cannot rank), reproducibility from
K=3 readings: a sentence counts when more than half cite it, the best sentence per key point is taken (one claim per assertion),
and 2 slots go to checkable details, unsourced ones first. The claim stays a verbatim article sentence with exact offsets.
EXTRACT_MODE=votes is the classifier + majority vote described above (also the fallback when the summary gives nothing).

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

_NO_COMMENT_RE = re.compile(
    r"^(?:[\w\s.'’\-]{1,80})\b(?:"
    r"has not (?:yet )?(?:commented|responded|replied)(?:\s+(?:on|to)\b)?|"
    r"did not (?:immediately )?respond to (?:a )?requests?\s+for comment|"
    r"declined to comment|could not immediately be reached for comment"
    r")\b",
    re.I,
)
_QUOTE_OR_BULLET_START = ('"', "\u201c", "\u2018", "\u00ab", "\u25aa", "\u2022")

SYSTEM_PROMPT = """You decide which sentences of a news article are CHECKABLE FACTUAL CLAIMS for a source-checking tool.

A checkable claim is a concrete assertion a source could confirm or refute: an event, a number or statistic, a quoted statement attributed to someone, a statement about what a document, report or official said. NOT checkable: opinions, rhetorical questions, predictions, general background, routine no-comment lines ("X has not commented", "X did not immediately respond to a request for comment"), headlines, bylines, navigation/share/related-article/advertising text.

The article is given as numbered sentences. Answer with JSON only: {"claims": [{"id": <sentence number>, "attributions": ["..."]}]}
List the sentences that are checkable claims. "attributions" = the people or organisations the sentence presents as the SOURCE of the claim, exactly as the article names them: whoever is reported as saying, warning, claiming, announcing, confirming, reporting or suspecting it ("X said ...", "according to Y", "Z warned that ...", "officials suspect ..."), the document or report it cites, the speaker of a quotation. The source counts when it is named only by role ("the Nvidia founder", "the minister", "the team", "the researchers", "anonymous US officials"): then give the person's or study's name if the article names them anywhere, else the role as written. A sentence that continues what the previous sentence said or quoted ("it", "he", "she", "they", a quotation, or a bulleted example from a work or speaker) ALWAYS has the same source as that sentence. [] ONLY when the sentence states the matter as plain fact with no source behind it. Do not invent sources. The article may be in any language."""


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
    check_text: str | None = None  # self-contained wording used to SEARCH and VERIFY (statement.py); `text` stays what is shown


@dataclass
class ExtractionResult:
    claims: list[ExtractedClaim]
    model: str
    dropped: list[str] = field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    credits: float = 0.0
    n_votes: int = 0               # readings that answered (K minus failures)
    units: list[tuple[int, int, str]] = field(default_factory=list)   # the numbered sentences (focus.py reuses them)
    alternates: list[int] = field(default_factory=list)   # next-best sentence indices (summary mode): refill when a pick is not checkable
    alternate_attrs: dict[int, list[str]] = field(default_factory=dict)   # their attributions


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


SUMMARY_PROMPT = """You pick the CENTRAL CHECKABLE CLAIMS of a news article for a source-checking tool, by summarising the article.

The article is given as numbered sentences. Write 5 to 8 KEY POINTS: the central assertions the article makes or rests its argument on, most central first. Each key point is one short sentence in your own words plus the numbers of the article sentences that STATE it (1 to 3 numbers; a sentence must state the point itself, not merely be on the same topic; list the sentence that states it most directly FIRST).
Prefer: the article's main claims, concrete facts and figures that carry the argument, and statements the article attributes to a named person, report or document.
Leave out: personal anecdotes and feelings of individual interviewees, descriptions of one person's life, opinion, speculation, predictions, general background, rhetorical questions, routine no-comment lines ("X has not commented"), headlines, bylines, navigation, share, related-article and advertising text.

Then list up to 4 DETAILS: further sentences (not cited in a key point) that state a concrete, checkable fact, such as a figure, a date, an event or a statement attributed to someone, even though they are not central.

"sources": for every sentence you cited (in key points and details) that presents a person, organisation, document or report as the SOURCE of what it says ("X said ...", "according to Y", "a study by Z found ..."), give those names exactly as the article names them (a role such as "the researchers" is fine; add the person's or study's name if the article names it anywhere). A sentence that continues what the previous sentence said or quoted has the same source as that sentence. Leave out sentences that state something as plain fact.
The article may be in any language.

Answer with JSON only: {"points": [{"point": "...", "sentences": [12, 13]}], "details": [31, 47], "sources": [{"id": 12, "attributions": ["..."]}]}"""

ABSENT_RANK = 8          # a reading that did not cite a sentence counts as this key-point rank (later = less central)


@dataclass
class _Reading:
    points: list[list[int]] = field(default_factory=list)      # key points, most central first: the sentences citing each
    details: list[int] = field(default_factory=list)
    attrs: dict[int, tuple[str, ...]] = field(default_factory=dict)


def _ids(raw, n_units: int, limit: int) -> list[int]:
    out: list[int] = []
    for v in raw if isinstance(raw, list) else []:
        try:
            i = int(v)
        except (TypeError, ValueError):
            continue
        if 0 <= i < n_units and i not in out:
            out.append(i)
    return out[:limit]


def _parse_summary(data: dict, n_units: int) -> _Reading:
    pts = data.get("points")
    if not isinstance(pts, list):
        raise LLMError("summary output has no 'points' list")
    r = _Reading()
    for p in pts[:10]:
        ids = _ids(p.get("sentences") if isinstance(p, dict) else None, n_units, 3)
        if ids:
            r.points.append(ids)
    r.details = _ids(data.get("details"), n_units, 6)
    for item in data.get("sources") if isinstance(data.get("sources"), list) else []:
        if not isinstance(item, dict):
            continue
        ids = _ids([item.get("id")], n_units, 1)
        names = item.get("attributions")
        if ids and isinstance(names, list):
            r.attrs[ids[0]] = tuple(a.strip() for a in names if isinstance(a, str) and a.strip())
    if not r.points:
        raise LLMError("summary output has no usable key point")
    return r


def select_by_summary(readings: list[_Reading], texts: dict[int, str], unsourced: Callable[[int], bool], *, limit: int,
                      detail_slots: int) -> tuple[list[int], collections.Counter, list[int]]:
    """Claims from K summary readings: centrality from the key-point order, ONE sentence per key point, then checkable details.
    Returns (sentence indices in document order, votes per sentence, alternates: the next-best sentences in rank order for
    refilling a slot). Pure code, deterministic."""
    k = len(readings)
    support: collections.Counter = collections.Counter()
    ranks: dict[int, list[int]] = collections.defaultdict(list)
    poss: dict[int, list[int]] = collections.defaultdict(list)
    together: dict[int, set[int]] = collections.defaultdict(set)
    detail_votes: collections.Counter = collections.Counter()
    for r in readings:
        seen: set[int] = set()
        for rank, ids in enumerate(r.points):
            for pos, i in enumerate(ids):
                if i not in seen:
                    support[i] += 1
                    seen.add(i)
                ranks[i].append(rank)
                poss[i].append(pos)
                together[i].update(ids)
        for i in set(r.details):
            detail_votes[i] += 1

    def centrality(i: int) -> tuple:
        mean_rank = (sum(ranks[i]) + (k - support[i]) * ABSENT_RANK) / k
        return (mean_rank, sum(poss[i]) / len(poss[i]), i)

    wanted = max(0, min(detail_slots, limit - 1))
    core = sorted((i for i, c in support.items() if c * 2 > k), key=centrality)
    picked: list[int] = []
    covered: set[int] = set()
    picked_texts: list[str] = []
    for i in core:
        if len(picked) >= limit - wanted:
            break
        if i in covered or (texts.get(i) and _is_near_dup(texts[i], picked_texts)):
            continue
        picked.append(i)
        covered |= together[i]              # the other sentences of the same key point are the same assertion
        picked_texts.append(texts.get(i, ""))
    taken = set(picked)
    # details: checkable facts that are not central; unsourced ones first (they test the article's sourcing), then by votes
    pool = [i for i, c in detail_votes.items() if c * 2 > k and i not in taken and i not in covered]
    pool.sort(key=lambda i: (not unsourced(i), -detail_votes[i], i))
    for i in pool:
        if len(picked) >= limit:
            break
        if texts.get(i) and _is_near_dup(texts[i], picked_texts):
            continue
        picked.append(i)
        picked_texts.append(texts.get(i, ""))
    votes = collections.Counter({i: max(support[i], detail_votes[i]) for i in picked})
    chosen = set(picked)
    alternates: list[int] = []
    for i in [*core, *pool]:
        if i in chosen or i in covered or i in alternates:
            continue
        if texts.get(i) and _is_near_dup(texts[i], [texts.get(j, "") for j in (*picked, *alternates)]):
            continue
        alternates.append(i)
    return sorted(picked), votes, alternates[:6]


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
    if s.extract_mode == "summary":
        res = _extract_summary(article_md, article_url, units, numbered, llm, s)
        if res is not None:
            return res
        log.warning("summary extraction gave nothing usable: falling back to the classifier vote")
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
    majority = sorted(i for i, c in counts.items()
                      if c * 2 > len(votes) and not _NO_COMMENT_RE.search(units[i][2].strip()))
    links = {i: attach_links(article_md, units[i][0], units[i][1], article_url) for i in majority}
    attrs = {i: list(_attributions(votes, i)) for i in majority}
    _carry_attributions(units, article_md, majority, attrs, votes)
    texts = {i: units[i][2] for i in majority}
    keep = prefer_sourced(majority, {i for i in majority if links[i] or attrs[i]}, s.max_claims,
                          s.min_unsourced_claims, links=links, attrs=attrs, texts=texts)
    claims: list[ExtractedClaim] = []
    for i in keep:
        start, end, _ = units[i]
        claims.append(ExtractedClaim(
            id=f"c{len(claims) + 1}", text=article_md[start:end], char_start=start, char_end=end,
            links=links[i], attributions=attrs[i],
            votes=counts[i], context=article_md[slice(*paragraph_bounds(article_md, start, end))][:2000]))
    return ExtractionResult(
        claims, results[0].model if results else s.model_extract, [],
        sum(r.prompt_tokens for r in results), sum(r.completion_tokens for r in results), sum(r.credits for r in results),
        n_votes=len(votes), units=units)


def _extract_summary(article_md: str, article_url: str, units: list[tuple[int, int, str]], numbered: str, llm: LLMClient,
                     s: Settings) -> ExtractionResult | None:
    """Summary-based extraction (see the module docstring). None = no usable reading or no claim: the caller falls back."""
    k = max(1, s.summary_votes)

    def read(i: int):
        # the reading index keeps the K requests different (and fixed, so every reading replays from the step cache)
        return llm.chat_json(s.model_extract, SUMMARY_PROMPT,
                             f"Reading {i + 1} of {k}.\nARTICLE (url: {article_url or 'n/a'}):\n\n{numbered}", max_tokens=4000)

    with ThreadPoolExecutor(max_workers=k, thread_name_prefix="summary") as pool:
        futures = [pool.submit(read, i) for i in range(k)]
        results = []
        for f in futures:
            try:
                results.append(f.result())
            except LLMError as exc:
                log.warning("one summary reading failed: %s", exc)
    readings: list[_Reading] = []
    for r in results:
        try:
            readings.append(_parse_summary(r.data, len(units)))
        except LLMError as exc:
            log.warning("one summary reading unusable: %s", exc)
    if not readings:
        return None
    texts = {i: units[i][2] for i in range(len(units))}
    attr_votes = [rd.attrs for rd in readings]

    def unsourced(i: int) -> bool:
        start, end, _ = units[i]
        return not attach_links(article_md, start, end, article_url) and not _attributions(attr_votes, i)

    ok = lambda i: not _NO_COMMENT_RE.search(texts[i].strip())
    filtered = [_Reading([[i for i in ids if ok(i)] for ids in rd.points if any(ok(i) for i in ids)],
                         [i for i in rd.details if ok(i)], rd.attrs) for rd in readings]
    keep, counts, alternates = select_by_summary(filtered, texts, unsourced, limit=s.max_claims, detail_slots=s.summary_detail_slots)
    if not keep:
        return None
    links = {i: attach_links(article_md, units[i][0], units[i][1], article_url) for i in keep}
    attrs = {i: list(_attributions(attr_votes, i)) for i in keep}
    _carry_attributions(units, article_md, keep, attrs, attr_votes)
    claims = [ExtractedClaim(id=f"c{n + 1}", text=article_md[units[i][0]:units[i][1]], char_start=units[i][0], char_end=units[i][1],
                             links=links[i], attributions=attrs[i], votes=counts[i],
                             context=article_md[slice(*paragraph_bounds(article_md, units[i][0], units[i][1]))][:2000])
              for n, i in enumerate(keep)]
    return ExtractionResult(
        claims, results[0].model if results else s.model_extract, [],
        sum(r.prompt_tokens for r in results), sum(r.completion_tokens for r in results), sum(r.credits for r in results),
        n_votes=len(readings), units=units, alternates=alternates,
        alternate_attrs={i: list(_attributions(attr_votes, i)) for i in alternates})


def _carry_attributions(units: list[tuple[int, int, str]], article_md: str, majority: list[int],
                        attrs: dict[int, list[str]], votes: list[dict[int, tuple[str, ...]]]) -> None:
    """Carry attribution onto unattributed continuation quotes / bulleted examples from the preceding sentence in the same
    paragraph (or the immediately preceding non-heading paragraph when none precedes it in-paragraph)."""
    paras = [paragraph_bounds(article_md, s, e) for s, e, _ in units]
    resolved: dict[int, tuple[str, ...]] = {}
    for idx in range(len(units)):
        if idx in attrs and attrs[idx]:
            resolved[idx] = tuple(attrs[idx])
        elif any(idx in v for v in votes):
            r = _attributions(votes, idx)
            if r:
                resolved[idx] = r
    for i in majority:
        if attrs[i]:
            continue
        start, end, plain = units[i]
        raw = article_md[start:end].lstrip()
        if not (plain.lstrip().startswith(_QUOTE_OR_BULLET_START) or raw.startswith(">")):
            continue
        p_cur = paras[i]
        inherited: tuple[str, ...] = ()
        for j in range(i - 1, -1, -1):
            if paras[j] != p_cur:
                break
            if j in resolved:
                inherited = resolved[j]
                break
        if not inherited:
            prev_p = None
            for j in range(i - 1, -1, -1):
                if paras[j] == p_cur:
                    continue
                if prev_p is None:
                    gap = article_md[paras[j][1]:p_cur[0]]
                    if re.search(r"^\s*#", gap, re.M):
                        break
                    prev_p = paras[j]
                elif paras[j] != prev_p:
                    break
                if j in resolved:
                    inherited = resolved[j]
                    break
        if inherited:
            attrs[i] = list(inherited)
            resolved[i] = inherited


def _is_near_dup(text: str, chosen: list[str]) -> bool:
    from rapidfuzz import fuzz

    return any(fuzz.token_set_ratio(text, prev) >= 78 for prev in chosen)


def prefer_sourced(majority: list[int], sourced: set[int], limit: int, min_unsourced: int, *,
                   links: dict[int, list[LinkRef]] | None = None, attrs: dict[int, list[str]] | None = None,
                   texts: dict[int, str] | None = None) -> list[int]:
    """Pick at most `limit` claims from the majority-voted sentences (document order in, document order out).
    Sentences that name a source (sentence link or attribution) come first across diverse sources while skipping near
    duplicates; `min_unsourced` slots stay reserved for unsourced ones. Pure code, deterministic."""
    if len(majority) <= limit:
        return list(majority)
    if links is None and attrs is None and texts is None:
        unsourced = [i for i in majority if i not in sourced]
        reserve = min(max(0, min_unsourced), len(unsourced), limit)
        take_sourced = [i for i in majority if i in sourced][: limit - reserve]
        take_unsourced = unsourced[: limit - len(take_sourced)]
        return sorted(take_sourced + take_unsourced)

    unsourced = [i for i in majority if i not in sourced]
    reserve = min(max(0, min_unsourced), len(unsourced), limit)
    sourced_cap = limit - reserve

    def skeys(i: int) -> set[str]:
        keys = {l.url.split("#")[0].rstrip("/") for l in (links or {}).get(i, ()) if l.scope == "sentence"}
        keys |= {_name_key(a) for a in (attrs or {}).get(i, ()) if _name_key(a)}
        return keys

    direct = [i for i in majority if i in sourced and skeys(i)]
    secondary = [i for i in majority if i in sourced and not skeys(i)]
    text_of = lambda idx: (texts or {}).get(idx, "")

    picked: list[int] = []
    picked_set: set[int] = set()
    picked_texts: list[str] = []
    seen_keys: set[str] = set()

    def add(idx: int) -> None:
        picked.append(idx)
        picked_set.add(idx)
        if text_of(idx):
            picked_texts.append(text_of(idx))
        seen_keys.update(skeys(idx))

    for i in direct:
        if len(picked) >= sourced_cap:
            break
        if (skeys(i) - seen_keys) and not (text_of(i) and _is_near_dup(text_of(i), picked_texts)):
            add(i)
    for pool in (direct, secondary):
        for i in pool:
            if len(picked) >= sourced_cap:
                break
            if i not in picked_set and not (text_of(i) and _is_near_dup(text_of(i), picked_texts)):
                add(i)
    for pool in (direct, secondary):
        for i in pool:
            if len(picked) >= sourced_cap:
                break
            if i not in picked_set:
                add(i)

    for i in unsourced:
        if len(picked) >= limit:
            break
        if not (text_of(i) and _is_near_dup(text_of(i), picked_texts)):
            add(i)
    for i in unsourced:
        if len(picked) >= limit:
            break
        if i not in picked_set:
            add(i)
    return sorted(picked)


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
