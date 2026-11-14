"""Markdown helpers for claim location and source attachment (DECISIONS Q18).

The LLM sees a *plain view* of the article (links/emphasis stripped, whitespace collapsed); we map its
verbatim spans back to offsets in `article.text_markdown` (Unicode code points, as the contract says).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable
from urllib.parse import urlsplit

from rapidfuzz import fuzz

IMAGE_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
# [text](url "title"); link text may contain one level of nested [] (e.g. image inside link)
LINK_RE = re.compile(r"(?<!!)\[((?:[^\[\]]|\[[^\]]*\])*)\]\(\s*<?(https?://[^)\s>]+)>?(?:\s+\"[^\"]*\")?\s*\)")
_SENT_BREAK = re.compile(r"(?<=[.!?…])[\"”’»)\]]*\s+(?=[\"“‘«(\[]?[A-Z0-9А-ЯЁ])|\n")


@dataclass
class PlainView:
    text: str
    to_md: list[int]               # plain index -> markdown index
    link_spans: list[tuple[int, int]]  # markdown spans of whole [text](url) tokens


def build_plain(md: str) -> PlainView:
    out: list[str] = []
    to_md: list[int] = []
    link_spans: list[tuple[int, int]] = []
    i, n = 0, len(md)
    prev_space = True
    at_line_start = True
    while i < n:
        m = IMAGE_RE.match(md, i)
        if m:
            i = m.end()
            continue
        m = LINK_RE.match(md, i)
        if m:
            link_spans.append((m.start(), m.end()))
            # recurse on the link text so nested emphasis is handled; map to positions inside the token
            inner = build_plain(m.group(1))
            base = m.start() + 1
            for ch, off in zip(inner.text, inner.to_md):
                if ch == " " and prev_space:
                    continue
                out.append(ch); to_md.append(base + off); prev_space = ch == " "
            i = m.end()
            at_line_start = False
            continue
        ch = md[i]
        if ch == "\n":
            at_line_start = True
        if at_line_start and ch in "#>-+ " and (ch != "-" and ch != "+" or md[i + 1:i + 2] == " "):
            i += 1  # heading / quote / list markers
            continue
        if ch.isspace():
            if not prev_space:
                out.append(" "); to_md.append(i); prev_space = True
            i += 1
            continue
        at_line_start = False
        if ch == "*" or (ch == "_" and not (md[i - 1:i].isalnum() and md[i + 1:i + 2].isalnum())):
            i += 1
            continue
        out.append(ch); to_md.append(i); prev_space = False
        i += 1
    return PlainView("".join(out).rstrip(), to_md, link_spans)


def normalize_for_match(s: str) -> str:
    s = s.replace("*", "")
    s = re.sub(r"(?<![A-Za-z0-9])_|_(?![A-Za-z0-9])", "", s)
    s = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", s)  # if the model kept link markup anyway
    return re.sub(r"\s+", " ", s).strip()


def locate(plain: PlainView, claim: str, threshold: float = 88.0) -> tuple[int, int, float] | None:
    """Find `claim` in the plain view: exact first, else fuzzy alignment. -> (start, end, score) in plain."""
    needle = normalize_for_match(claim)
    if len(needle) < 15:
        return None
    pos = plain.text.find(needle)
    if pos >= 0:
        return pos, pos + len(needle), 100.0
    al = fuzz.partial_ratio_alignment(needle, plain.text, score_cutoff=threshold)
    if al is None:
        return None
    return al.dest_start, al.dest_end, float(al.score)


def to_markdown_span(md: str, plain: PlainView, ps: int, pe: int) -> tuple[int, int]:
    start = plain.to_md[ps]
    end = plain.to_md[pe - 1] + 1
    for a, b in plain.link_spans:  # never cut a link token in half
        if a < start < b:
            start = a
        if a < end < b:
            end = b
    return start, end


def paragraph_bounds(md: str, start: int, end: int) -> tuple[int, int]:
    p0 = md.rfind("\n\n", 0, start)
    p1 = md.find("\n\n", end)
    return (p0 + 2 if p0 >= 0 else 0), (p1 if p1 >= 0 else len(md))


def sentence_bounds(md: str, start: int, end: int) -> tuple[int, int]:
    p0, p1 = paragraph_bounds(md, start, end)
    s0 = p0
    for m in _SENT_BREAK.finditer(md, p0, end):   # not endpos=start: the lookahead must see the claim
        if m.end() <= start:
            s0 = m.end()
    m = _SENT_BREAK.search(md, end, p1)
    s1 = m.start() if m else p1
    return s0, max(s1, end)


@dataclass
class LinkRef:
    url: str
    anchor_text: str
    scope: str  # "sentence" | "paragraph"


def links_in(md: str, lo: int, hi: int) -> list[tuple[str, str]]:
    return [(m.group(2), m.group(1)) for m in LINK_RE.finditer(md, lo, hi)]


def attach_links(md: str, start: int, end: int, article_url: str = "") -> list[LinkRef]:
    """Links in the claim's sentence; if none, links in its paragraph (Q18). Deduplicated, in text order."""
    s0, s1 = sentence_bounds(md, start, end)
    found = links_in(md, s0, s1)
    scope = "sentence"
    if not found:
        p0, p1 = paragraph_bounds(md, start, end)
        found, scope = links_in(md, p0, p1), "paragraph"
    seen, refs = set(), []
    art = article_url.split("#")[0].rstrip("/")
    for url, anchor in found:
        key = url.split("#")[0].rstrip("/")
        if key in seen or key == art or is_navigation_link(url, article_url):
            continue
        seen.add(key)
        refs.append(LinkRef(url, plain_anchor(anchor), scope))
    return refs


def _reg_domain(url: str) -> str:
    from ..fetch.urls import registrable_domain   # local import: fetch imports nothing from pipeline, keep it so
    return registrable_domain(url)


_DIGIT = re.compile(r"\d")


def is_navigation_link(url: str, article_url: str) -> bool:
    """A link to a topic / section / tag page of the article's OWN site (`/world/gaza`, `/ukraine`,
    `/sport/lewis-hamilton`, `/hub/russia-ukraine`). It is navigation, not a citation, so it is ignored instead of
    turning the claim into a `self_citation`. Same-site links to ARTICLES (a date or id in the path, a .html page, or a
    slug of four or more words) stay self-citations."""
    if not article_url:
        return False
    try:
        if _reg_domain(url) != _reg_domain(article_url):
            return False
        parts = urlsplit(url)
    except ValueError:
        return False
    segs = [p for p in parts.path.split("/") if p]
    if not segs:
        return True                       # the homepage
    if len(segs) > 3 or _DIGIT.search(parts.path) or "." in segs[-1]:
        return False
    return len(re.split(r"[-_]+", segs[-1])) < 4


def plain_anchor(text: str) -> str:
    return build_plain(text).text.strip()


_SENTENCE_SPLIT = re.compile(r"(?<=[.!?…])[\"”’»)\]]*\s+(?=[\"“‘«(\[]?[A-ZА-ЯЁ0-9])")
MIN_UNIT_CHARS = 30


def _dedupe_key(text: str) -> str:
    return re.sub(r"[\W_]+", " ", text).casefold().strip()


def sentence_units(md: str, max_chars: int = 24_000, max_units: int = 110,
                   keep: Callable[[str], bool] | None = None, dedupe: bool = True) -> list[tuple[int, int, str]]:
    """Sentences of the article as (md_start, md_end, plain_text), in document order.

    Lines come from the MARKDOWN (a share-button row or a byline stays its own unit instead of being glued to the
    lede), and the offsets are exact: the claim is a slice of the markdown, never fuzzy-located.
    `keep`: only sentences it accepts count (the article-body filter); applied BEFORE the size caps, so a long
    sidebar cannot use up the budget. `dedupe`: a sentence repeated on the page (headline + teaser + body) is kept
    once, at its first place, so it cannot get two different verdicts."""
    units: list[tuple[int, int, str]] = []
    seen: set[str] = set()
    total = 0
    offset = 0
    for line in md.split("\n"):
        line_start = offset
        offset += len(line) + 1
        if not line.strip():
            continue
        view = build_plain(line)
        if len(view.text) < MIN_UNIT_CHARS:
            continue
        pos = 0
        for part in _SENTENCE_SPLIT.split(view.text):
            i = view.text.find(part, pos)
            pos = i + len(part)
            if len(part) < MIN_UNIT_CHARS:
                continue
            if keep is not None and not keep(part):
                continue
            if dedupe:
                k = _dedupe_key(part)
                if k in seen:
                    continue
                seen.add(k)
            a, b = to_markdown_span(line, view, i, i + len(part))
            units.append((line_start + a, line_start + b, part))
            total += len(part)
            if total > max_chars or len(units) >= max_units:
                return units
    return units


SYNDICATION_THRESHOLD = 0.4


def syndication_share(article_md: str, claim_text: str, page_text: str, *, sentences: int = 14) -> float | None:
    """Share of the article's OTHER sentences that also occur on `page_text`: ~1.0 for a wire copy / syndication of the
    same piece, ~0 for an outlet that wrote its own report. None when the article gives too little to compare."""
    from rapidfuzz import fuzz

    own = build_plain(claim_text).text[:40]
    others = [t for _, _, t in sentence_units(article_md) if len(t) >= 70 and own not in t][:sentences]
    if len(others) < 3 or not page_text:
        return None
    page = build_plain(page_text[:150_000]).text
    hits = sum(1 for t in others if fuzz.partial_ratio(t[:160], page) >= 92)
    return hits / len(others)


# ---- article body (DECISIONS: extraction scope) --------------------------------------------------------------------
BODY_MATCH = 90.0          # partial_ratio of a sentence against the extracted main text
MIN_BODY_CHARS = 150       # less than this is a failed extraction, not a short article: no filter then


def _ws(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def article_body_filter(html: str | None) -> Callable[[str], bool] | None:
    """-> predicate "this sentence belongs to the article's main text", or None when the body cannot be told apart.

    The markdown (with links, needed for sourcing) is the whole page: menus, "Top news" teasers, related-article
    headlines and sidebars. trafilatura (precision mode) finds the main text in the raw HTML; a markdown sentence is
    part of the article when it appears in that text (fuzzy, punctuation and markup differ). Measured on 8 live
    articles (Guardian x2, Conversation, ZeroHedge, RT, NaturalNews, Pravda x2): no body sentence lost, all sidebar and
    teaser sentences dropped. No HTML / extraction failed -> None (the whole page is used, as before)."""
    if not html:
        return None
    try:
        import trafilatura
        body = trafilatura.extract(html, include_comments=False, include_tables=False, favor_precision=True) or ""
    except Exception:      # a parser failure must never stop an analysis
        return None
    body = _ws(body)
    if len(body) < MIN_BODY_CHARS:
        return None

    def keep(sentence: str) -> bool:
        return fuzz.partial_ratio(_ws(sentence), body) >= BODY_MATCH

    return keep
