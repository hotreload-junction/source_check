"""Provenance laundering facts: the article passes on what a Telegram/social channel posted (facts only).

Two observations, both deterministic (no LLM):
- `declared_reposts`: the page itself names a Telegram channel as THE source of the article, on a line of its own
  ("Source: Telegram "rybar_in_english"", "Источник: Telegram-канал ..."). This is how the Pravda network marks the
  channel posts it republishes. A sentence that quotes a Telegram post ("he wrote on Telegram") is not such a line.
- `channel_sourced_claims`: claims whose only cited links are Telegram/social posts. Faithfully copying a channel post
  makes the claim "supported" by that post, which says nothing about whether the post was true; this records it.

Thresholds (how many claims, combined with what) live in verdict.py.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from ..pipeline.textutil import build_plain

# "Source:" in the languages the Pravda network publishes in most (the label precedes the channel name)
_SOURCE_LABEL = re.compile(
    r"^\s*(?:source|sources|источник|джерело|quelle|fuente|fonte|źródło|zdroj|kilde|källa|bron|lähde|forrás|izvor|"
    r"πηγή|kaynak|sursa|šaltinis|avots|allikas|originally published (?:on|by)|first published (?:on|by)|via)\s*:\s*(?P<rest>.*)$", re.I)
_TELEGRAM_WORD = re.compile(r"\b(?:telegram|телеграм|телеграмм|tg)\b", re.I)
_TELEGRAM_LINK = re.compile(r"https?://(?:www\.)?(?:t\.me|telegram\.me|telegram\.dog)/", re.I)
_MD_ESCAPE = re.compile(r"\\([\\`*_{}\[\]()#+\-.!|])")    # markdown backslash escapes: ukraine\_watch -> ukraine_watch
MAX_LINE_CHARS = 200    # a declaration is short; a long paragraph that starts with "Source:" is prose


@dataclass(frozen=True)
class DeclaredRepost:
    line: str          # the declaration as plain text, e.g. 'Source: Telegram "ukraine_watch"'
    char_start: int    # offset of the line in article.text_markdown


def declared_reposts(article_md: str) -> list[DeclaredRepost]:
    out: list[DeclaredRepost] = []
    offset = 0
    for line in article_md.split("\n"):
        start = offset
        offset += len(line) + 1
        if not line.strip():
            continue
        plain = _MD_ESCAPE.sub(r"\1", build_plain(line.replace("\\_", "\x00")).text).replace("\x00", "_").strip()
        if len(plain) > MAX_LINE_CHARS:
            continue
        m = _SOURCE_LABEL.match(plain)
        if not m:
            continue
        rest = m.group("rest")
        if _TELEGRAM_WORD.search(rest) or _TELEGRAM_LINK.search(line):
            out.append(DeclaredRepost(plain, start))
    return out


CHANNEL_KINDS = frozenset({"telegram", "social"})


def channel_sourced_claims(claims: list[dict], sources: list[dict]) -> tuple[list[str], int]:
    """-> (ids of claims whose cited LINKS are all Telegram/social posts, number of claims that cite any link).

    Only links the article itself gives count (depth-1 sources with a URL); stubs for named people and pages a
    search found are not the article's citations."""
    by_id = {s["id"]: s for s in sources}
    channel, linked = [], 0
    for c in claims:
        cited = [by_id[i] for i in c.get("source_ids", []) if i in by_id]
        links = [s for s in cited if s.get("url") and s.get("depth", 1) == 1 and s.get("kind") not in ("named", "self")]
        if not links:
            continue
        linked += 1
        if all(s.get("kind") in CHANNEL_KINDS for s in links):
            channel.append(c["id"])
    return channel, linked
