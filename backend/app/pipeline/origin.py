"""Root or relay: where does a claim that could not be traced to a checkable source START?

For every claim that ends without evidence and without a cited page (`unsourced`, or `not_checkable` with only a named
attribution and no URL), the strong model (MODEL_VERIFY) answers ONE question: is the information's origin inside what
the article gives us, or somewhere the article does not say?

- "root": nothing further to trace. The speaker's own words, opinion, intention, decision or announcement presented as
  their statement (an interview, a quotation); a first-hand experience or observation (the interviewee, the reporter on
  the scene); an organisation's own data or decision reported as such; anonymous sources the outlet itself spoke to.
  -> `not_checkable` (neutral, AGENTS.md section 7), explanation starts with "Root source:".
- "relay": the information comes from somewhere else and the article does not say where ("doctors claim", "studies
  show", "experts warn", a statistic or finding asserted as fact without its origin).
  -> `unsourced` (counts as a bad claim; the two-signal rule in verdict.py still decides the article label).

Quote first, verdict second: the model must copy the words of the PARAGRAPH that show the origin (`article_span`); code
fuzzy-matches them against that paragraph (FUZZY_THRESHOLD). No match, a malformed answer or an LLM error -> the claim
keeps its verdict. Doubtful cases are told to the model as "root", so an uncertain answer never penalises the article.
Claims whose cited page was unreachable, paywalled, social or self-cited are never sent here: those stay neutral.
Neither are claims for which a page was READ and quoted verbatim (`Claim.source_checks`): pages that confirm or refute the
claim ("Sources disagree", a corroborating report without the original) are evidence, and a vague wording in the article
must not overwrite what they show. Their neutral verdict and explanation stay.
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from ..config import Settings
from ..llm import LLMClient, LLMError
from .textutil import build_plain, paragraph_bounds
from .verify import fuzzy_score

log = logging.getLogger("citetrace.origin")

MIN_SPAN_CHARS = 8
ELIGIBLE_VERDICTS = ("unsourced", "not_checkable")

SYSTEM_PROMPT = """You decide where a piece of information in a news article ORIGINATES. The CLAIM below could not be traced to any checkable source: the article links nothing for it and a search found no page that confirms or refutes it. Your job is NOT to judge whether the claim is true. Decide only whether the chain of sources legitimately ENDS here, or whether it breaks off.

"root" = the information starts with the source the article itself names or with the article's own reporting, so there is nothing further to trace:
- a person's or organisation's own words, opinion, intention, decision or announcement, presented as what they said (a quotation or reported speech, e.g. in an interview the outlet conducted);
- a first-hand experience or observation: what the interviewee went through, what the reporter saw on the scene;
- an organisation's own data, document or decision, reported as theirs;
- anonymous sources the outlet itself spoke to, attributed as such ("officials who spoke on condition of anonymity").

"relay" = the information starts somewhere else than what the article names, and the article does not say where:
- vague groups or hearsay as the source: "doctors claim", "experts warn", "scientists say", "studies show", "it is said", "many believe";
- facts about the world asserted as true (figures, statistics, scientific findings, events nobody in the article witnessed) without naming where they come from;
- a named speaker passing on facts that come from someone else, when the article adopts them as fact rather than presenting them as that speaker's statement.

Judge the claim AS THE ARTICLE PRESENTS IT: "X said Y" in X's own interview is root for the statement; the article stating Y as fact because "experts" say so is relay. If you are unsure, answer "root".

"article_span": copy VERBATIM from the PARAGRAPH (character for character, at most one sentence) the words that show the origin, e.g. "told us in an interview", "doctors claim", or the bare assertion itself.
"origin_holder": in a few words, who or what the information originates with (root) or would have to originate with (relay).
"reason": ONE plain-English sentence.

Answer with JSON only:
{"origin": "root|relay", "origin_holder": "...", "article_span": "...", "reason": "..."}"""


@dataclass
class OriginResult:
    origin: str              # root | relay
    holder: str
    span: str
    reason: str
    fuzzy_score: float
    model: str


def has_verified_page(cd: dict) -> bool:
    """A page was read for this claim and its verbatim, fuzzy-checked quote confirms, distorts or contradicts it."""
    return any(k.get("quote_original") and k.get("page_verdict") in ("supported", "distorted", "contradicted")
               for k in cd.get("source_checks") or ())


def eligible(cd: dict, rows_by_id: dict[str, dict]) -> bool:
    """No evidence, no subtype (self / social stay neutral), no page that was read and quoted (found pages that agree or
    disagree stay as they are), and no cited page with a URL (unreachable stays neutral)."""
    if cd.get("verdict") not in ELIGIBLE_VERDICTS or cd.get("evidence") or cd.get("subtype") or has_verified_page(cd):
        return False
    return all(rows_by_id.get(sid, {}).get("url") is None for sid in cd.get("source_ids", ()))


def classify(claim_text: str, paragraph: str, attributions, *, llm: LLMClient, settings: Settings) -> OriginResult | None:
    """One call to the verify model. None = no usable answer (the claim keeps its verdict)."""
    user = (f"ATTRIBUTION the article gives: {'; '.join(attributions) if attributions else 'none'}\n\n"
            f"CLAIM:\n{build_plain(claim_text).text}\n\nPARAGRAPH:\n{build_plain(paragraph).text[:3000]}")
    try:
        res = llm.chat_json(settings.model_verify, SYSTEM_PROMPT, user, max_tokens=800)
    except LLMError as exc:
        log.warning("origin check failed: %s", exc)
        return None
    d = res.data
    origin = d.get("origin")
    span = d.get("article_span") if isinstance(d.get("article_span"), str) else ""
    if origin not in ("root", "relay") or len(span.strip()) < MIN_SPAN_CHARS:
        return None
    score = fuzzy_score(span, paragraph)
    if score < settings.fuzzy_threshold:
        log.info("origin span not in paragraph (score %.0f): %r", score, span[:80])
        return None
    holder = d.get("origin_holder") if isinstance(d.get("origin_holder"), str) else ""
    reason = d.get("reason") if isinstance(d.get("reason"), str) else ""
    return OriginResult(origin, holder.strip(), span.strip(), reason.strip(), score, res.model)


def explain(r: OriginResult) -> tuple[str, str]:
    """(verdict, explanation) for a usable answer."""
    who = r.holder or "the source the article names"
    if r.origin == "root":
        return "not_checkable", (f"Root source: the information starts with {who}, so there is nothing further to trace. "
                                 f"{r.reason}").strip()
    return "unsourced", (f"Not the root source: this information comes from {who}, but the article does not say where, "
                         f"and no original was found. {r.reason}").strip()


def check_origins(claim_dicts: list[dict], assessments, rows: list[dict], article_md: str, *, llm: LLMClient,
                  settings: Settings) -> list[tuple[int, str, str, OriginResult]]:
    """Changes to apply: (claim index, verdict, explanation, result). Never raises; reads, never mutates its inputs."""
    rows_by_id = {r["id"]: r for r in rows}
    todo = [i for i, cd in enumerate(claim_dicts) if cd is not None and eligible(cd, rows_by_id)]
    if not todo:
        return []

    def one(i: int):
        cd = claim_dicts[i]
        p0, p1 = paragraph_bounds(article_md, cd["char_start"], cd["char_end"])
        attrs = list(getattr(assessments[i].claim, "attributions", None) or []) if i < len(assessments) else []
        return i, classify(cd["text"], article_md[p0:p1], attrs, llm=llm, settings=settings)

    changes = []
    with ThreadPoolExecutor(max_workers=min(len(todo), 6), thread_name_prefix="origin") as pool:
        for i, r in pool.map(one, todo):
            if r is not None:
                verdict, expl = explain(r)
                changes.append((i, verdict, expl, r))
    return changes
