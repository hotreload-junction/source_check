"""Evidence search for attributed claims ("Person X said ...", "in a May interview with Fox Business, he ...").

The article gives no link, so the pipeline has to FIND the statement and then verify it like any source:

1. plan   a cheap model reads the claim AND its paragraph and says who spoke, where (outlet / institution), when, in
          what medium, and which verbatim words of the quote are quoted (the paragraph is where "In a May interview
          with Fox Business" usually sits, not in the claim sentence);
2. search Exa, in parallel: the planned queries (speaker + venue + month), the venue's own domain, and the exact
          quoted words (Exa `includeText`). Measured on a real case: all three put the Fox Business interview first,
          the old "claim sentence" query did not, and a 60-day date filter then threw the May interview away;
3. rank   pages from the named venue first, then pages that contain the exact quote, then official-looking sites.

Nothing the planner says is trusted blindly: quoted phrases must really occur in the text, domains are validated,
dates are parsed, and a wrong guess only costs a search that returns nothing (the verify step has the last word).
"""
from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from ..fetch.urls import registrable_domain
from ..llm import LLMError
from .exa import NamedSourceHit, find_candidates
from .textutil import build_plain

log = logging.getLogger("citetrace.evidence")

DEFAULT_LOOKBACK_DAYS = 180     # no occasion named: a statement from the last half year
WINDOW_PAD_DAYS = 14            # around a named month / date
_DOMAIN_RE = re.compile(r"^(?:[a-z0-9-]+\.)+[a-z]{2,}$")
_GENERIC_SLD = ("gov", "com", "co", "org", "net", "edu", "ac", "mil")

PLAN_PROMPT = """A news article attributes a claim to a person or organisation and gives no link. Plan how to find the ORIGINAL statement.

You get the CLAIM, its PARAGRAPH (the occasion is often named there, e.g. "In a May interview with Fox Business") and the ARTICLE DATE. Answer with JSON only:
{"speaker": "...", "venue": "...", "medium": "interview|speech|statement|report|testimony|post|other", "date_from": "YYYY-MM-DD or null", "date_to": "YYYY-MM-DD or null", "domains": ["..."], "queries": ["..."], "phrases": ["..."]}

- "speaker": who said or published it, by name (resolve "he" / "the founder" from the paragraph).
- "venue": the outlet, programme, institution or event the article names for it (e.g. "Fox Business", "UN Security Council"); null if none is named.
- "date_from"/"date_to": the period the statement was made, as absolute dates computed from the ARTICLE DATE ("a May interview" in an article of November 2026 -> 2026-05-01 to 2026-05-31; "on Tuesday", "last week" likewise); null if the article does not say when.
- "domains": up to 3 website domains where the original would be published: the venue's own site and the speaker's official site. Only domains you are confident exist.
- "queries": up to 3 plain keyword searches (4 to 9 words, no quotation marks) that would find the original: speaker + venue + month/year + topic. Different angles, not rewordings.
- "phrases": up to 2 VERBATIM fragments of 2 to 5 words copied from words the article QUOTES as the speaker's own; [] if nothing is quoted."""


@dataclass
class SearchPlan:
    speaker: str | None = None
    venue: str | None = None
    medium: str | None = None
    date_from: date | None = None
    date_to: date | None = None
    domains: list[str] = field(default_factory=list)
    queries: list[str] = field(default_factory=list)
    phrases: list[str] = field(default_factory=list)

    @property
    def occasion(self) -> str | None:
        """Human-readable occasion for the verify prompt, or None when the article names none."""
        parts = []
        if self.medium and self.medium != "other":
            parts.append(self.medium)
        if self.venue:
            parts.append(f"at/with {self.venue}")
        if self.date_from and self.date_to:
            parts.append(f"between {self.date_from.isoformat()} and {self.date_to.isoformat()}")
        elif self.date_from:
            parts.append(f"around {self.date_from.isoformat()}")
        return " ".join(parts) or None


@dataclass
class Candidate:
    hit: NamedSourceHit
    venue_hosted: bool = False      # page is on the venue's own site (the interview outlet's own report / video page)
    phrase_hit: bool = False        # Exa found it through the exact quoted words
    order: int = 0

    @property
    def score(self) -> tuple:
        """Sort key, best first. A page that CONTAINS the quoted words is the strongest hint there is (measured: seven
        irrelevant pages of the speaker's own site used to push the one outlet that quotes the sentence out of the
        verified slots); being on the venue's site is what makes such a page the original, so it comes first among them."""
        return (-int(self.phrase_hit and self.venue_hosted), -int(self.phrase_hit), -int(self.venue_hosted),
                -self.hit.primary_hint, self.order)


def _clean_domain(d: object) -> str | None:
    d = str(d).strip().lower().removeprefix("https://").removeprefix("http://").removeprefix("www.").split("/")[0]
    labels = d.split(".")
    if not _DOMAIN_RE.match(d) or (len(labels) == 2 and labels[0] in _GENERIC_SLD):      # "gov.ua" is a registry, not a site
        return None
    return d


def _parse_date(v: object, end_of_month: bool = False) -> date | None:
    if not isinstance(v, str) or not v.strip() or v.strip().lower() == "null":
        return None
    v = v.strip()
    try:
        if re.fullmatch(r"\d{4}-\d{2}", v):          # a bare month
            d = date.fromisoformat(v + "-01")
            return (d.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1) if end_of_month else d
        return date.fromisoformat(v[:10])
    except ValueError:
        return None


def _words(s: str) -> str:
    s = s.lower().replace("’", "'").replace("‘", "'").replace("ʼ", "'")        # typographic apostrophes
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s']", " ", s)).strip()


def exa_phrase(phrase: str, max_words: int = 5) -> str | None:
    """The part of a quoted phrase Exa can match exactly: the longest run of plain words (no apostrophes, hyphens or
    clause punctuation inside, which sites spell differently), at most 5 of them, at least 2."""
    best: list[str] = []
    run: list[str] = []
    for raw in phrase.replace("’", "'").replace("‘", "'").split():
        core = raw.strip("\"“”'.,;:!?()[]")
        if not re.fullmatch(r"\w+", core):               # an apostrophe or hyphen inside the word: break the run
            run = []
            continue
        run.append(core.lower())
        if len(run) > len(best):
            best = list(run)
        if raw[-1] in ".,;:!?)\"”":                          # a clause ends here: the next word is not contiguous text
            run = []
    best = best[:max_words]
    return " ".join(best) if len(best) >= 2 else None


def parse_plan(data: dict, context: str) -> SearchPlan:
    """Validate the model's answer. `context` = claim + paragraph: a phrase must really occur in it."""
    def text(key: str, limit: int = 80) -> str | None:
        v = data.get(key)
        return re.sub(r"\s+", " ", v).strip()[:limit] if isinstance(v, str) and v.strip() and v.strip().lower() != "null" else None

    haystack = _words(context)
    phrases = []
    for ph in data.get("phrases") if isinstance(data.get("phrases"), list) else []:
        if not isinstance(ph, str):
            continue
        n = len(_words(ph).split())
        usable = exa_phrase(ph)
        if 2 <= n <= 5 and _words(ph) in haystack and usable and usable not in phrases:     # no invented quotes
            phrases.append(usable)
    queries = []
    for q in data.get("queries") if isinstance(data.get("queries"), list) else []:
        if isinstance(q, str):
            q = re.sub(r"\s+", " ", q.replace('"', " ")).strip()
            if 8 <= len(q) <= 140 and q not in queries:
                queries.append(q)
    domains = []
    for d in data.get("domains") if isinstance(data.get("domains"), list) else []:
        d = _clean_domain(d)
        if d and d not in domains:
            domains.append(d)
    d_from, d_to = _parse_date(data.get("date_from")), _parse_date(data.get("date_to"), end_of_month=True)
    if d_from and d_to and d_to < d_from:
        d_from, d_to = None, None
    medium = text("medium", 20)
    return SearchPlan(text("speaker"), text("venue"), medium.lower() if medium else None, d_from, d_to,
                      domains[:3], queries[:3], phrases[:2])


def plan_search(attribution: str, claim_text: str, context: str, article_date: datetime | None, *, llm, model: str) -> SearchPlan:
    """One cheap-model call per attributed claim (cached by the step cache). Never raises: on any failure the plan is
    just the old behaviour, a query built from the speaker and the claim."""
    claim_plain = build_plain(claim_text).text
    para_plain = build_plain(context).text if context else ""
    fallback = SearchPlan(speaker=attribution, queries=[f"{attribution} {claim_plain}"[:140]])
    when = article_date.date().isoformat() if article_date else "unknown"
    try:
        res = llm.chat_json(model, PLAN_PROMPT,
                            f"ARTICLE DATE: {when}\nATTRIBUTED TO: {attribution}\nCLAIM: {claim_plain[:400]}\nPARAGRAPH: {para_plain[:1500]}",
                            max_tokens=1200)
    except LLMError as exc:
        log.info("search plan failed: %s", exc)
        return fallback
    plan = parse_plan(res.data, f"{claim_plain} {para_plain}")
    if not plan.queries:
        plan.queries = fallback.queries
    plan.speaker = plan.speaker or attribution
    return plan


# Platforms where ANYONE publishes. A post on truthsocial.com / x.com / youtube.com / t.me is only the speaker's own
# statement when it is the speaker's account, which a domain match cannot tell: measured, a fan account reposting a
# headline ("President Trump reveals ...") counted as "Trump's own Truth Social post". The model's source_type decides.
UGC_DOMAINS = {"truthsocial.com", "x.com", "twitter.com", "facebook.com", "instagram.com", "youtube.com", "youtu.be", "t.me",
               "telegram.me", "reddit.com", "tiktok.com", "linkedin.com", "medium.com", "substack.com", "bsky.app", "threads.net",
               "vk.com", "ok.ru", "wordpress.com", "blogspot.com", "stocktwits.com", "quora.com", "wikipedia.org"}


def venue_key(venue: str | None) -> str:
    return re.sub(r"[^a-z0-9]", "", (venue or "").lower())


def is_venue_domain(url: str, plan: SearchPlan) -> bool:
    """Page is hosted by the venue the article names: planned domains, or a domain whose name IS the venue
    (foxbusiness.com for "Fox Business") so a wrong domain guess by the model cannot hide it."""
    reg = registrable_domain(url)
    if reg in UGC_DOMAINS:
        return False
    if reg in {registrable_domain(d) for d in plan.domains}:
        return True
    label, key = reg.split(".")[0], venue_key(plan.venue)
    return bool(key) and len(key) >= 4 and (label == key or (len(label) >= 5 and (label in key or key in label)))


def search_window(plan: SearchPlan, article_date: datetime | None) -> tuple[datetime, datetime] | None:
    """The period a general (unrestricted) search looks at. The occasion the article names wins over the default."""
    if plan.date_from:
        end = plan.date_to or plan.date_from
        return (datetime.combine(plan.date_from, datetime.min.time()) - timedelta(days=WINDOW_PAD_DAYS),
                datetime.combine(end, datetime.min.time()) + timedelta(days=WINDOW_PAD_DAYS))
    if article_date:
        return article_date - timedelta(days=DEFAULT_LOOKBACK_DAYS), article_date + timedelta(days=2)
    return None


def in_window(published: str | None, window: tuple[datetime, datetime] | None) -> bool:
    """False only when Exa dates the page clearly outside the window (unknown date -> keep; verify decides)."""
    if not published or not window:
        return True
    try:
        d = datetime.fromisoformat(published.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return True
    return window[0] <= d <= window[1] + timedelta(days=1)


def gather_candidates(plan: SearchPlan, claim_text: str, article_url: str, article_date: datetime | None, *, api_key: str,
                      exa_client=None, step_cache=None, limit: int = 5, include_secondary: bool = False) -> list[Candidate]:
    """Run all planned searches in parallel and return the ranked, de-duplicated candidate pages."""
    speaker = plan.speaker or ""
    window = search_window(plan, article_date)
    kw = dict(api_key=api_key, client=exa_client, exclude_url=article_url, cache=step_cache)
    short = " ".join(x for x in (speaker, plan.venue, plan.medium if plan.medium != "other" else None) if x)[:120]
    jobs: list[tuple[str, dict]] = []
    for q in plan.queries[:3]:
        jobs.append(("query", dict(query=q, window=window, limit=4)))
    if plan.domains:
        for q in plan.queries[:2]:
            jobs.append(("domain", dict(query=q, include_domains=plan.domains, limit=3)))
    for ph in plan.phrases[:2]:
        jobs.append(("phrase", dict(query=short or speaker or ph, include_text=ph, limit=4)))
    if not jobs:
        return []
    with ThreadPoolExecutor(max_workers=min(len(jobs), 6), thread_name_prefix="exa") as pool:
        results = list(pool.map(lambda j: find_candidates(speaker or claim_text[:60], claim_text, **kw, **j[1]), jobs))
    seen: dict[str, Candidate] = {}
    order = 0
    for (kind, params), hits in zip(jobs, results):
        for h in hits:
            c = seen.get(h.url)
            if c is None:
                c = seen[h.url] = Candidate(h, order=order)
                order += 1
            c.phrase_hit = c.phrase_hit or kind == "phrase"
            c.venue_hosted = c.venue_hosted or is_venue_domain(h.url, plan)
    keep = []
    for c in seen.values():
        # Worth an LLM verification: the venue's own pages, pages with the exact quote, official-looking sites; another
        # outlet only when asked for (it can corroborate but never decide). Venue / exact-quote pages are kept whatever
        # Exa dates them; everything else must fit the period.
        if c.venue_hosted or c.phrase_hit:
            keep.append(c)
        elif (include_secondary or c.hit.primary_hint >= 1) and in_window(c.hit.published, window):
            keep.append(c)
    keep.sort(key=lambda c: c.score)
    return keep[:limit]
