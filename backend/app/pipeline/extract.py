"""B3: central-claim extraction (cheap LLM, JSON, temperature 0) + source attachment (code, not LLM).

The LLM only *selects* claims and names attributed sources. Offsets and links come from code, so a claim
the model paraphrased (not found in the article) is dropped instead of trusted.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from ..config import Settings, get_settings
from ..llm import LLMClient, LLMError, LLMResult
from .textutil import LinkRef, attach_links, build_plain, locate, to_markdown_span

log = logging.getLogger("citetrace.extract")

MAX_ARTICLE_CHARS = 24_000

SYSTEM_PROMPT = """You extract the CENTRAL FACTUAL CLAIMS from a news article for a source-checking tool.

A central claim is a concrete, checkable assertion the article's message depends on: an event, a number or statistic, a quoted statement attributed to someone, a statement about what a document/report/official said. Skip opinions, rhetorical questions, predictions, background everyone knows, headlines and navigation/share/"related articles"/advertising text.

Rules:
- Return at most {max_claims} claims, most central first. Fewer is fine; return [] if there is no checkable claim.
- "text" must be copied VERBATIM from the article, character for character, one sentence (or a clause of one). Never paraphrase, translate, shorten with "..." or merge sentences.
- "attributions": names of sources the claim is attributed to in its sentence or paragraph, exactly as named in the text (e.g. "the Finnish Ministry of Defence", "a report by Amnesty International", "Reuters"). Use [] when the text names none. Do not invent sources.
- The article may be in any language; keep the original language.

Answer with JSON only: {{"claims": [{{"text": "...", "attributions": ["..."]}}]}}"""


@dataclass
class ExtractedClaim:
    id: str
    text: str                      # verbatim slice of article_md[char_start:char_end]
    char_start: int
    char_end: int
    links: list[LinkRef] = field(default_factory=list)
    attributions: list[str] = field(default_factory=list)
    locate_score: float = 100.0


@dataclass
class ExtractionResult:
    claims: list[ExtractedClaim]
    model: str
    dropped: list[str] = field(default_factory=list)   # claim texts we could not find in the article
    prompt_tokens: int = 0
    completion_tokens: int = 0
    credits: float = 0.0


def extract_claims(article_md: str, article_url: str = "", *, llm: LLMClient | None = None,
                   settings: Settings | None = None) -> ExtractionResult:
    s = settings or get_settings()
    llm = llm or LLMClient.from_settings(s)
    plain = build_plain(article_md)
    shown = plain.text[:MAX_ARTICLE_CHARS]
    res: LLMResult = llm.chat_json(
        s.model_extract, SYSTEM_PROMPT.format(max_claims=s.max_claims),
        f"ARTICLE (url: {article_url or 'n/a'}):\n\n{shown}",
    )
    raw = res.data.get("claims")
    if not isinstance(raw, list):
        raise LLMError("extraction output has no 'claims' list")

    claims: list[ExtractedClaim] = []
    dropped: list[str] = []
    taken: list[tuple[int, int]] = []
    for item in raw:
        text = item.get("text") if isinstance(item, dict) else item
        if not isinstance(text, str) or not text.strip():
            continue
        hit = locate(plain, text)
        if hit is None:
            dropped.append(text)
            continue
        ps, pe, score = hit
        start, end = to_markdown_span(article_md, plain, ps, pe)
        if any(start < b and a < end for a, b in taken):   # overlaps an earlier (more central) claim
            continue
        taken.append((start, end))
        attrs = item.get("attributions") if isinstance(item, dict) else []
        attrs = [a.strip() for a in attrs if isinstance(a, str) and a.strip()] if isinstance(attrs, list) else []
        claims.append(ExtractedClaim(
            id=f"c{len(claims) + 1}", text=article_md[start:end], char_start=start, char_end=end,
            links=attach_links(article_md, start, end, article_url), attributions=attrs, locate_score=score,
        ))
        if len(claims) >= s.max_claims:
            break
    if dropped:
        log.info("dropped %d claim(s) not found verbatim in the article", len(dropped))
    return ExtractionResult(claims, res.model, dropped, res.prompt_tokens, res.completion_tokens, res.credits)
