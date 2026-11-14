"""Source type (PLAN Q1): what kind of document a cited source is, decided by a fixed rule table.

Deterministic, offline, no LLM: only the URL and the raw HTML (schema.org JSON-LD, `citation_*` meta).
The first matching rule wins; the order is a decision (DECISIONS "Q1 SOURCE TYPE"):

    social > academic > press_release > official > reference > news > blog > unknown

The type says what kind of document it is, NOT whether it is reliable: kremlin.ru is `official`, TASS is a news
agency. Source quality is context for the reader and never a verdict signal on its own (DECISIONS "SOURCE QUALITY").
Every result carries the rule that fired (`reason`), so the UI and the methodology page can show why.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from ..fetch.urls import SOCIAL_DOMAINS, registrable_domain

SOURCE_TYPES = ("official", "press_release", "academic", "reference", "news", "blog", "social", "unknown")

# user-generated platforms on top of the fetch layer's social list (Q19); anyone can post there
_SOCIAL_HOSTS = SOCIAL_DOMAINS | {
    "t.me", "telegram.me", "truthsocial.com", "gab.com", "rumble.com", "bitchute.com", "odysee.com",
    "bsky.app", "weibo.com", "pinterest.com", "dzen.ru", "rutube.ru", "telegram.org",
}
_ACADEMIC_HOSTS = {
    "doi.org", "arxiv.org", "biorxiv.org", "medrxiv.org", "ssrn.com", "pubmed.ncbi.nlm.nih.gov", "ncbi.nlm.nih.gov",
    "europepmc.org", "jstor.org", "sciencedirect.com", "link.springer.com", "onlinelibrary.wiley.com",
    "tandfonline.com", "academic.oup.com", "journals.plos.org", "plos.org", "frontiersin.org", "mdpi.com",
    "thelancet.com", "nejm.org", "bmj.com", "semanticscholar.org", "zenodo.org", "researchgate.net",
    "scholar.google.com", "pnas.org", "science.org", "cell.com", "jamanetwork.com", "nber.org",
}
_PRESS_WIRE_HOSTS = {
    "prnewswire.com", "businesswire.com", "globenewswire.com", "newswire.ca", "accesswire.com", "einpresswire.com",
    "prweb.com", "presseportal.de", "ots.at", "pressat.co.uk", "openpr.com", "newswire.com", "prnewswire.co.uk",
    "cision.com", "mynewsdesk.com",
}
# path segments of press releases / official statements, on any host (lowercase, compared per segment)
_PRESS_SEGMENTS = {
    "press", "press-release", "press-releases", "pressrelease", "pressreleases", "press-room", "pressroom",
    "newsroom", "media-centre", "media-center", "mediacentre", "mediacenter", "pressemitteilung",
    "pressemitteilungen", "presse", "pressemeldung", "pressemeldungen", "communique", "communiques",
    "communiques-de-presse", "comunicados", "press-statements", "statements",
}
# official bodies whose registrable domain is a plain .org/.ru/... (public suffixes below cover the rest)
_OFFICIAL_HOSTS = {
    "un.org", "worldbank.org", "imf.org", "oecd.org", "wto.org", "osce.org", "coe.int", "icrc.org", "unhcr.org",
    "unicef.org", "ohchr.org", "iaea.org", "bund.de", "bundesregierung.de", "bundestag.de", "admin.ch",
    "gv.at", "rijksoverheid.nl", "government.nl", "elysee.fr", "kremlin.ru", "mid.ru", "duma.gov.ru",
    "whitehouse.gov", "president.gov.ua", "valtioneuvosto.fi", "regeringen.se", "regjeringen.no", "parliament.uk",
    "europarl.europa.eu", "ecb.europa.eu", "bundesbank.de", "destatis.de", "federalreserve.gov", "nato.int",
}
_OFFICIAL_SUFFIXES = (".gov", ".mil", ".int", ".europa.eu", ".gouv.fr", ".gc.ca", ".bund.de", ".admin.ch", ".gv.at")
_OFFICIAL_LABELS = {"gov", "gob", "gouv", "govt", "mil", "go"}   # second-level: gov.uk, gob.mx, go.jp ...
_REFERENCE_HOSTS = {"wikipedia.org", "britannica.com", "wikidata.org", "wikisource.org"}
# wire / news agencies: the document type is news reporting, whatever one thinks of the agency
_NEWS_AGENCY_HOSTS = {
    "reuters.com", "apnews.com", "afp.com", "dpa.com", "dpa-international.com", "tass.com", "tass.ru", "ria.ru",
    "interfax.ru", "interfax.com.ua", "ukrinform.net", "ukrinform.ua", "xinhuanet.com", "news.cn", "efe.com",
    "ansa.it", "kyodonews.net", "aa.com.tr", "yna.co.kr", "pap.pl", "ctk.cz", "stt.fi", "tt.se", "ntb.no",
    "belga.be", "anp.nl", "apa.at", "keystone-sda.ch", "pti.in", "bernama.com", "antaranews.com", "upi.com",
}
_BLOG_HOSTS = {
    "substack.com", "medium.com", "blogspot.com", "wordpress.com", "tumblr.com", "livejournal.com", "ghost.io",
    "telegra.ph", "wixsite.com", "weebly.com", "blogger.com", "hashnode.dev", "over-blog.com", "typepad.com",
}
_BLOG_SEGMENTS = {"blog", "blogs", "blogpost", "weblog"}

_NEWS_LD_TYPES = {
    "newsarticle", "reportagenewsarticle", "analysisnewsarticle", "opinionnewsarticle", "backgroundnewsarticle",
    "reviewnewsarticle", "askpublicnewsarticle", "liveblogposting",
}
_BLOG_LD_TYPES = {"blogposting", "blog"}
_ACADEMIC_LD_TYPES = {"scholarlyarticle", "medicalscholarlyarticle"}

_LD_RE = re.compile(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', re.I | re.S)
_CITATION_META_RE = re.compile(r'<meta[^>]+name=["\'](citation_doi|citation_journal_title|citation_arxiv_id)["\']', re.I)
_HEAD_LIMIT = 300_000


@dataclass(frozen=True)
class SourceType:
    type: str          # one of SOURCE_TYPES
    reason: str        # the rule that fired, plain English (shown in the UI / audit)


def classify_source_type(url: str | None, html: str | None = None) -> SourceType | None:
    """Type of the document at `url`. None for a source without a URL (unresolved named stub). Never raises."""
    if not url:
        return None
    try:
        return _classify(url, html)
    except Exception:          # malformed URL or HTML: the honest answer is "unknown", never a crash
        return SourceType("unknown", "no rule matched")


def _classify(url: str, html: str | None) -> SourceType:
    parts = urlsplit(url)
    host = (parts.hostname or "").lower().rstrip(".").removeprefix("www.")
    dom = registrable_domain(host)
    segments = [s.lower() for s in parts.path.split("/") if s]
    head = (html or "")[:_HEAD_LIMIT]
    ld = _ld_types(head) if head else set()

    if hit := _host_in(host, dom, _SOCIAL_HOSTS):
        return SourceType("social", f"user-generated platform ({hit})")

    if hit := _host_in(host, dom, _ACADEMIC_HOSTS):
        return SourceType("academic", f"scholarly publisher or repository ({hit})")
    if ld & _ACADEMIC_LD_TYPES:
        return SourceType("academic", "page declares a schema.org ScholarlyArticle")
    if head and (m := _CITATION_META_RE.search(head)):
        return SourceType("academic", f"page carries scholarly citation metadata ({m.group(1).lower()})")

    if hit := _host_in(host, dom, _PRESS_WIRE_HOSTS):
        return SourceType("press_release", f"press-release distribution service ({hit})")
    if seg := next((s for s in segments[:-1] or segments if s in _PRESS_SEGMENTS), None):
        return SourceType("press_release", f"press-release section of the site (/{seg}/)")

    if why := _official(host, dom):
        return SourceType("official", why)

    if hit := _host_in(host, dom, _REFERENCE_HOSTS):
        return SourceType("reference", f"encyclopedia or reference work ({hit})")

    if ld & _NEWS_LD_TYPES:
        return SourceType("news", f"page declares a schema.org {_pretty(ld & _NEWS_LD_TYPES)}")
    if hit := _host_in(host, dom, _NEWS_AGENCY_HOSTS):
        return SourceType("news", f"news agency ({hit})")

    if hit := _host_in(host, dom, _BLOG_HOSTS):
        return SourceType("blog", f"blog platform ({hit})")
    if ld & _BLOG_LD_TYPES:
        return SourceType("blog", "page declares a schema.org BlogPosting")
    if seg := next((s for s in segments if s in _BLOG_SEGMENTS), None):
        return SourceType("blog", f"blog section of the site (/{seg}/)")

    return SourceType("unknown", "no rule matched")


def _host_in(host: str, dom: str, hosts: set[str]) -> str | None:
    """The listed host this page is on: exact registrable domain, the full host, or any parent of it
    (`pubmed.ncbi.nlm.nih.gov`, `foo.substack.com`)."""
    if dom in hosts:
        return dom
    labels = host.split(".")
    for i in range(len(labels) - 1):
        cand = ".".join(labels[i:])
        if cand in hosts:
            return cand
    return None


def _official(host: str, dom: str) -> str | None:
    if hit := _host_in(host, dom, _OFFICIAL_HOSTS):
        return f"government or intergovernmental body ({hit})"
    dotted = "." + host
    if suf := next((s for s in _OFFICIAL_SUFFIXES if dotted.endswith(s)), None):
        return f"official domain ending {suf}"
    labels = host.split(".")
    if len(labels) >= 2 and len(labels[-1]) == 2 and labels[-2] in _OFFICIAL_LABELS:
        return f"official domain ending .{labels[-2]}.{labels[-1]}"
    return None


def _ld_types(head: str) -> set[str]:
    """Every schema.org @type in the page's JSON-LD blocks, lowercased (walks @graph and nested objects)."""
    found: set[str] = set()
    for block in _LD_RE.findall(head):
        try:
            data = json.loads(block.strip())
        except (ValueError, RecursionError):
            continue
        stack = [data]
        while stack:
            node = stack.pop()
            if isinstance(node, list):
                stack.extend(node)
            elif isinstance(node, dict):
                t = node.get("@type")
                for name in (t if isinstance(t, list) else [t]):
                    if isinstance(name, str):
                        found.add(name.rsplit("/", 1)[-1].lower())
                stack.extend(v for v in node.values() if isinstance(v, (dict, list)))
    return found


_LD_NAMES = {t.lower(): t for t in (
    "NewsArticle", "ReportageNewsArticle", "AnalysisNewsArticle", "OpinionNewsArticle", "BackgroundNewsArticle",
    "ReviewNewsArticle", "AskPublicNewsArticle", "LiveBlogPosting")}


def _pretty(types: set[str]) -> str:
    return " / ".join(sorted(_LD_NAMES.get(t, t) for t in types))
