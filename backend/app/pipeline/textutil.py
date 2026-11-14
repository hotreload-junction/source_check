"""Markdown helpers for claim location and source attachment (DECISIONS Q18).

The LLM sees a *plain view* of the article (links/emphasis stripped, whitespace collapsed); we map its
verbatim spans back to offsets in `article.text_markdown` (Unicode code points, as the contract says).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

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
        if key in seen or key == art:
            continue
        seen.add(key)
        refs.append(LinkRef(url, plain_anchor(anchor), scope))
    return refs


def plain_anchor(text: str) -> str:
    return build_plain(text).text.strip()
