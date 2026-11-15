"""AI overview (display only): a plain-English summary of the FINISHED check, for the article and for each claim.

The verdicts are final before this runs; the model only puts them into words. It never sees the article text, only the
results (claim, verdict, the pages read, their verified quotes and explanations), so it has nothing to add facts from.
Nothing here is read by verdict.py: the label, the signals and the eval are identical with or without it.

Code guards (a rejected text is never shown):
- the claim counts are written by code ("Of 6 central claims: 3 supported by a source, ..."), the model is told not to count;
- every number and every capitalised name (except the first word of a sentence) must appear in the input;
- a claim may only be described in its own category: a `supported` claim is never called false or exaggerated, a
  `not_checkable` one never called false or true, a `not_mentioned` / `unsourced` one never called false or accurate;
- "propaganda", "disinformation", "lies" ... only when the label is propaganda_pattern;
- every claim id a sentence is tagged with must be a central claim; no claim id inside the text.
Article text rejected -> only the code-written count sentence (`source: "rule"`). A claim text rejected -> `overview: null`
(the frontend shows the verdict explanation). A failed call never breaks the analysis.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from ..config import Settings
from ..llm import LLMClient, LLMError

log = logging.getLogger("citetrace.overview")

OVERVIEW_PROMPT_VERSION = "o1"
MAX_ARTICLE_CHARS = 900
MAX_CLAIM_CHARS = 450
MAX_CHECKS_PER_CLAIM = 6

PROMPT = """You write the reader-facing summary of a finished fact-check. You get the results: every claim of a news article with its verdict, the pages it was checked against and what they say. You do NOT judge anything yourself: the verdicts are final and rest on verified verbatim quotes. Your job is to put them into plain, honest English.

Verdict meanings:
- supported: a source confirms the claim.
- distorted: a source says something related, but the claim exaggerates, overstates or changes it.
- contradicted: a source states the opposite.
- not_mentioned: the cited source does not say this.
- unsourced: the article gives no source and none was found.
- not_checkable: it could not be checked (paywall, unreachable, social media, the original could not be found). This is NEUTRAL: never call such a claim false, doubtful or true.

Write:
"claims": for EVERY claim id one or two short sentences: what the pages say about it and why the verdict is what it is. Name pages by their domain. Be concrete: which detail matches, which differs (for example "the article says all engines, the source says 32 of 33").
"article": two or three sentences about the article as a whole, only about claims that are NOT marked "reader question only": which points hold up, which are overstated, contradicted or unsupported, and what that means for a reader. Mention the warning signals only as given. Tag every sentence with the ids of the claims it is about ([] if none).

Rules:
- Use ONLY facts from the input. No outside knowledge, no new names, numbers or dates.
- Do not count claims ("three of the claims ..."): the counts are shown separately.
- Never contradict, soften or sharpen a verdict.
- Do not use the words propaganda, disinformation, misinformation, lies or fake unless the ARTICLE VERDICT is propaganda_pattern, and then only "propaganda pattern".
- Do not write claim ids (c1, c2 ...) inside the text.
- Calm, neutral tone. No exclamation marks.

Answer with JSON only: {"article": [{"text": "...", "claims": ["c1"]}], "claims": {"c1": "..."}}"""

COUNT_WORDS = {
    "supported": "supported by a source",
    "distorted": "overstated or distorted",
    "contradicted": "contradicted by a source",
    "not_mentioned": "not found in the cited source",
    "unsourced": "without any source",
    "not_checkable": "could not be checked (neutral)",
}

_SHARP = r"false|untrue|wrong|incorrect|refut\w*|debunk\w*|fabricat\w*|contradict\w*"
_BENT = r"exaggerat\w*|overstat\w*|distort\w*|misleading|inaccurate"
_POSITIVE = r"accurate|correct|holds? up|(is|are) true"
_FORBIDDEN = {      # words that would describe a claim in another category than its verdict
    "supported": re.compile(rf"\b({_SHARP}|{_BENT})\b", re.I),
    "distorted": re.compile(r"\b(fabricat\w*|debunk\w*)\b", re.I),
    "contradicted": None,
    "not_mentioned": re.compile(rf"\b({_SHARP}|{_POSITIVE})\b", re.I),
    "unsourced": re.compile(rf"\b({_SHARP}|{_POSITIVE})\b", re.I),
    "not_checkable": re.compile(rf"\b({_SHARP}|{_BENT}|{_POSITIVE})\b", re.I),
}
_LOADED = re.compile(r"\b(propaganda|disinformation|misinformation|lies|lying|liars?|fake)\b", re.I)
_CLAIM_ID = re.compile(r"\bc\d+\b")
_NUM = re.compile(r"\d[\d.,]*%?")
_WORD = re.compile(r"\S+")
_SENT = re.compile(r"(?<=[.!?])\s+")


@dataclass
class Overview:
    article: dict                                      # Result.overview
    claims: dict[str, str | None] = field(default_factory=dict)   # claim id -> Claim.overview


def _norm(s: str) -> str:
    return s.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"').lower()


def _clip(s: str, n: int) -> str:
    s = " ".join(str(s).split())
    return s if len(s) <= n else s[: n - 1] + "…"


def count_sentence(claims: list[dict]) -> str:
    central = [c for c in claims if c.get("central", True)]
    if not central:
        return "No central claims were found."
    parts = [f"{n} {w}" for v, w in COUNT_WORDS.items() if (n := sum(1 for c in central if c["verdict"] == v))]
    return f"Of {len(central)} central claim{'' if len(central) == 1 else 's'}: {', '.join(parts)}."


def unknown_token(text: str, haystack: str) -> str | None:
    """The first number or capitalised name (not the first word of a sentence) that the input does not contain."""
    hay = _norm(haystack)
    for n in _NUM.findall(text):
        key = n.strip(".,")
        if key and key not in hay:
            return n
    for sent in _SENT.split(text):
        for m in list(_WORD.finditer(sent))[1:]:
            w = m.group().strip("\"'“”‘’()[],.;:!?")
            if len(w) < 2 or not w[0].isupper():
                continue
            key = re.sub(r"'s$", "", _norm(w))
            if key not in hay:
                return w
    return None


def check_text(text: str, haystack: str, verdicts: set[str], label: str, max_chars: int) -> str | None:
    """None when the text may be shown, else why not."""
    if not text.strip():
        return "empty"
    if len(text) > max_chars:
        return "too long"
    if _CLAIM_ID.search(text):
        return "claim id in the text"
    if label != "propaganda_pattern" and (m := _LOADED.search(text)):
        return f"loaded word: {m.group()}"
    if len(verdicts) == 1:                  # a text about claims of one category must stay inside that category
        v = next(iter(verdicts))
        rx = _FORBIDDEN.get(v)
        if rx is not None and (m := rx.search(text)):
            return f"'{m.group()}' does not fit the verdict {v}"
    if (tok := unknown_token(text, haystack)) is not None:
        return f"not in the input: {tok}"
    return None


def _claim_block(c: dict, rows: dict[str, dict], threshold: float) -> str:
    head = f"CLAIM {c['id']} | verdict: {c['verdict']}"
    if not c.get("central", True):
        head += " | reader question only, not part of the article verdict"
    lines = [head, f"TEXT: {_clip(c['text'], 500)}"]
    if c.get("check_text"):
        lines.append(f"MEANING: {_clip(c['check_text'], 500)}")
    lines.append(f"WHY THIS VERDICT: {_clip(c.get('explanation') or '-', 400)}")
    found = set(c.get("found_source_ids") or [])
    checks = (c.get("source_checks") or [])[:MAX_CHECKS_PER_CLAIM]
    for k in checks:
        r = rows.get(k["source_id"], {})
        where = r.get("domain") or r.get("attribution_text") or "an unnamed page"
        how = "found by search" if (k["source_id"] in found or r.get("kind") == "named") else "cited by the article"
        s = f"- {where} ({how}): counts as {k['verdict']}"
        if k.get("page_verdict") and k["page_verdict"] != k["verdict"]:
            s += f", the page itself: {k['page_verdict']}"
        if k.get("quote_original") and (k.get("fuzzy_score") or 0) >= threshold:     # only quotes the code verified
            s += f'; quote: "{_clip(k.get("quote_translation") or k["quote_original"], 300)}"'
        if k.get("explanation"):
            s += f"; {_clip(k['explanation'], 300)}"
        if k.get("note") and k.get("note") != k.get("explanation"):
            s += f"; note: {_clip(k['note'], 200)}"
        lines.append(s)
    if not checks:
        named = [rows[i]["attribution_text"] for i in c.get("source_ids", []) if rows.get(i, {}).get("attribution_text")]
        lines.append("- no page was read for this claim" + (f" (named source: {'; '.join(named)})" if named else ""))
    return "\n".join(lines)


def write_overview(title: str, claims: list[dict], sources: list[dict], decision: dict, *, llm: LLMClient,
                   settings: Settings) -> Overview:
    """Never raises. `decision` = verdict.decide(...) output ({"signals", "verdict"})."""
    counts = count_sentence(claims)
    rule = Overview({"text": counts, "source": "rule", "model": None, "prompt_version": OVERVIEW_PROMPT_VERSION},
                    {c["id"]: None for c in claims})
    if not settings.ai_overview or not claims:
        return rule
    try:
        return _write(title or "", claims, sources, decision, counts, rule, llm, settings)
    except Exception as exc:                          # a display extra must never break the analysis
        log.warning("overview failed: %s", exc)
        return rule


def _write(title: str, claims: list[dict], sources: list[dict], decision: dict, counts: str, rule: Overview,
           llm: LLMClient, settings: Settings) -> Overview:
    rows = {r["id"]: r for r in sources}
    label = decision["verdict"]["label"]
    blocks = {c["id"]: _claim_block(c, rows, settings.fuzzy_threshold) for c in claims}
    fired = [f"{k}: {'; '.join(v.get('reasons') or [v.get('detail') or ''])}" for k, v in decision["signals"].items() if v.get("fired")]
    user = (f"TITLE: {title or '(none)'}\n"
            f"ARTICLE VERDICT: {label} ({decision['verdict']['summary']})\n"
            f"WARNING SIGNALS FIRED: {' | '.join(fired) or 'none'}\n\n" + "\n\n".join(blocks.values()))
    try:
        res = llm.chat_json(settings.light_model, PROMPT, user, max_tokens=3000)
    except LLMError as exc:
        log.info("overview call failed: %s", exc)
        return rule
    data = res.data if isinstance(res.data, dict) else {}

    by_id = {c["id"]: c for c in claims}
    got = data.get("claims") if isinstance(data.get("claims"), dict) else {}
    out_claims: dict[str, str | None] = {}
    for cid, c in by_id.items():
        t = got.get(cid)
        if not isinstance(t, str):
            out_claims[cid] = None
            continue
        t = " ".join(t.split())
        why = check_text(t, f"{title}\n{blocks[cid]}", {c['verdict']}, label, MAX_CLAIM_CHARS)
        if why:
            log.info("claim overview for %s not used: %s", cid, why)
        out_claims[cid] = None if why else t

    central = {cid for cid, c in by_id.items() if c.get("central", True)}
    items = data.get("article") if isinstance(data.get("article"), list) else []
    sentences: list[str] = []
    for it in items:
        if not isinstance(it, dict) or not isinstance(it.get("text"), str):
            sentences = []
            break
        tags = [t for t in it.get("claims") or [] if isinstance(t, str)] if isinstance(it.get("claims"), list) else []
        if any(t not in central for t in tags):
            log.info("article overview not used: tagged with %s", tags)
            sentences = []
            break
        t = " ".join(it["text"].split())
        why = check_text(t, user, {by_id[t2]["verdict"] for t2 in tags}, label, MAX_ARTICLE_CHARS)
        if why:
            log.info("article overview not used: %s", why)
            sentences = []
            break
        sentences.append(t)
    text = " ".join(sentences)
    if not sentences or len(text) > MAX_ARTICLE_CHARS:
        return Overview(rule.article, out_claims)
    return Overview({"text": f"{counts} {text}", "source": "ai", "model": res.model, "prompt_version": OVERVIEW_PROMPT_VERSION},
                    out_claims)
