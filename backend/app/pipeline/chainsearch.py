"""Source of the source WITHOUT a link (depth >= 2).

A depth-1 source often says where ITS information comes from in words only: "The New York Times reported ...",
"journalists reported ...", "according to a study by ...". When the passage the claim rests on links nothing usable
(`depth2.passage_links` is empty), this module

1. reads the passage and names the source it points to (one cheap LLM call, "source": null when the passage states
   the matter as plain fact or the origin is the page's own publisher),
2. looks for that page with the same machinery as an attributed claim in the article (plan -> our Exa searches + the
   Exa Agent side by side -> fetch -> quote-first verify), the statement being the SENTENCE OF THE PASSAGE,
3. returns the best page whose verbatim, fuzzy-checked quote supports the statement, or just the name when nothing
   was found (the reader still sees that the source names a further source).

Everything found this way is a page we SEARCHED for, not one the source links: it is a `kind: "named"` row and, like
every searched page, stays out of the infrastructure / network / circle signals (`run._search_ids`). Nothing here can
change an article label.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

from ..config import Settings
from ..llm import LLMClient, LLMError
from .assess import SourceAssessment, _assess_attributed, _FetchCache
from .extract import ExtractedClaim
from .textutil import build_plain

log = logging.getLogger("citetrace.chainsearch")

MAX_STATEMENT_CHARS = 600
MAX_NAME_CHARS = 160

PROMPT = """You read ONE passage of a web page (the PAGE). The page is cited as a source for a CLAIM. The passage links to nothing, but it may still say where ITS OWN information comes from.

Answer with JSON only: {"source": "..."} or {"source": null}.

"source" = the outlet, person, institution, document or study that the passage presents as the origin of the matter the CLAIM states, as the passage names it: "The New York Times reported ...", "journalists reported ...", "according to a study by ...", "a Reuters report", "officials said ...", "a 2024 study in The Lancet". A role is fine ("journalists", "US officials", "the researchers"); add the name or outlet if the passage names it anywhere. Prefer the origin of THE CLAIM'S matter over other attributions in the passage.

null when: the passage states the matter as plain fact; the origin is the page's own publisher or its own reporters ("our correspondent", "we have learned", the outlet that runs this PAGE DOMAIN); the passage only names who SAID something at an event the page itself reports first-hand; or you are not sure. Do not invent sources. The page may be in any language."""


@dataclass
class CitedSource:
    who: str                              # how the passage names the source ("The New York Times", "journalists")
    statement: str                        # the sentence of the passage that was searched for
    found: SourceAssessment | None = None # best verified page, None = named but not found


def named_source(sentence: str, paragraph: str, claim_text: str, page_domain: str, *, llm: LLMClient,
                 model: str) -> str | None:
    """Who the passage names as the origin of the claim's matter, or None. Never raises."""
    try:
        res = llm.chat_json(model, PROMPT,
                            f"PAGE DOMAIN: {page_domain}\nCLAIM: {build_plain(claim_text).text[:400]}\n"
                            f"PASSAGE: {paragraph[:1500]}\nSENTENCE: {sentence[:500]}", max_tokens=300)
    except LLMError as exc:
        log.info("naming the cited source failed: %s", exc)
        return None
    who = res.data.get("source") if isinstance(res.data, dict) else None
    if not isinstance(who, str):
        return None
    who = " ".join(who.split())
    if len(who) < 3 or who.lower() in ("null", "none", "unknown", "n/a"):
        return None
    return who[:MAX_NAME_CHARS]


def find_cited(parent_url: str, parent_domain: str, parent_text: str, sentence: str, paragraph: str, claim_text: str, *,
               cache: _FetchCache, llm: LLMClient, settings: Settings, published_at: datetime | None = None,
               step_cache=None, exa_client=None, who: str | None = None) -> CitedSource | None:
    """Name the source the passage points to (unless `who` is given) and look for its page. None = the passage names
    no further source."""
    who = who or named_source(sentence, paragraph, claim_text, parent_domain, llm=llm, model=settings.model_extract)
    if who is None:
        return None
    statement = sentence[:MAX_STATEMENT_CHARS]
    claim = ExtractedClaim(id="chain", text=statement, char_start=0, char_end=len(statement), attributions=[who],
                           context=paragraph[:1500])
    try:
        a = _assess_attributed(claim, parent_url, cache=cache, llm=llm, settings=settings, exa_client=exa_client,
                               published_at=published_at, step_cache=step_cache, article_text=parent_text)
    except Exception as exc:      # a search problem must never break the analysis: the source is just not found
        log.warning("search for the cited source %r failed: %s", who, type(exc).__name__)
        return CitedSource(who, statement)
    good = [s for s in a.sources if s.url and s.fetch is not None and s.fetch.status == "ok" and s.verify is not None
            and s.verify.verdict == "supported" and s.verify.has_evidence]
    # the venue's own page and a page with the quoted words first, then the better fuzzy match
    good.sort(key=lambda s: (-int(s.venue_hosted), -int(s.phrase_hit), -(s.verify.fuzzy_score or 0.0)))
    return CitedSource(who, statement, good[0] if good else None)
