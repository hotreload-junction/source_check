"""Copies of the article on other domains (M3): the network behind a Pravda-style article.

The infrastructure signal compares the analytics / AdSense IDs and the age of the domains BEHIND an article's sources.
A network article usually cites only itself, so there is nothing to compare, while the same text sits on a dozen mirror
sites run by the same operator. Exa `findSimilar` on the article URL finds those pages; code keeps only the ones that
really repeat the article's text (`looks_like_copy`), fetches their raw HTML, and hands them to the same infrastructure
analysis as every cited source (shared IDs -> cluster, domain age). A copy is not evidence of anything by itself:
wire services are syndicated everywhere. Only the infrastructure rules decide.
"""
from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from urllib.parse import urlsplit

from ..fetch.urls import registrable_domain
from .exa import find_copies
from .textutil import looks_like_copy

log = logging.getLogger("citetrace.copies")

MAX_COPIES = 6          # distinct domains kept (RDAP + HTML fetch each)
_CANON_RE = re.compile(r'<link\b[^>]*rel=["\']?canonical["\']?[^>]*href=["\']([^"\']+)["\']', re.I)
_CANON_ALT_RE = re.compile(r'<link\b[^>]*href=["\']([^"\']+)["\'][^>]*rel=["\']?canonical["\']?', re.I)
_OG_URL_RE = re.compile(r'<meta\b[^>]*property=["\']?og:url["\']?[^>]*content=["\']([^"\']+)["\']', re.I)


def _host(url: str) -> str:
    return (urlsplit(url).hostname or "").lower().removeprefix("www.")


def _is_canonical_mirror(html: str | None, article_domain: str) -> bool:
    """True when an external copy carries a canonical/og:url pointing to the article's domain (CDN/reverse proxy)."""
    if not html:
        return False
    for rx in (_CANON_RE, _CANON_ALT_RE, _OG_URL_RE):
        m = rx.search(html[:40_000])
        if m:
            try:
                if registrable_domain(m.group(1).strip()) == article_domain:
                    return True
            except Exception:
                continue
    return False


@dataclass
class CopyPage:
    url: str
    domain: str
    html: str | None


def find_article_copies(article_url: str, article_md: str, *, api_key: str, fetcher, exa_client=None, step_cache=None,
                        max_copies: int = MAX_COPIES) -> list[CopyPage]:
    """Pages on OTHER domains (or sibling network subdomains) that repeat the article. Never raises."""
    try:
        own = registrable_domain(article_url)
        art_host = _host(article_url)
        kw = {"exclude_domain": art_host} if (art_host and art_host != own) else {}
        hits = find_copies(article_url, api_key=api_key, client=exa_client, cache=step_cache, **kw)
        by_domain: dict[str, str] = {}
        sibling_kept = False
        for h in hits:
            hit_url = h.get("url") or ""
            hit_host = _host(hit_url)
            if not hit_host or hit_host == art_host:
                continue
            d = registrable_domain(hit_url)
            if d == own and sibling_kept:
                continue
            key = hit_host if d == own else d
            if key not in by_domain and h.get("text") and looks_like_copy(article_md, h["text"], ""):
                by_domain[key] = hit_url
                if d == own:
                    sibling_kept = True
        urls = list(by_domain.items())[:max_copies]
        if not urls:
            return []
        with ThreadPoolExecutor(max_workers=len(urls), thread_name_prefix="copy") as pool:
            htmls = list(pool.map(lambda du: _html(fetcher, du[1]), urls))
        out: list[CopyPage] = []
        for (domain, url), html in zip(urls, htmls):
            if registrable_domain(domain) != own and _is_canonical_mirror(html, own):
                continue
            out.append(CopyPage(url, domain, html))
        return out
    except Exception as exc:       # a failed side search must never break the analysis
        log.warning("copy search failed: %s", type(exc).__name__)
        return []


def _html(fetcher, url: str) -> str | None:
    try:
        return fetcher.fetch_html(url)
    except Exception:
        return None
