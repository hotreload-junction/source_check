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
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from ..fetch.urls import registrable_domain
from .exa import find_copies
from .textutil import looks_like_copy

log = logging.getLogger("citetrace.copies")

MAX_COPIES = 6          # distinct domains kept (RDAP + HTML fetch each)


@dataclass
class CopyPage:
    url: str
    domain: str
    html: str | None


def find_article_copies(article_url: str, article_md: str, *, api_key: str, fetcher, exa_client=None, step_cache=None,
                        max_copies: int = MAX_COPIES) -> list[CopyPage]:
    """Pages on OTHER domains that repeat the article. Never raises: no copies is the neutral answer."""
    try:
        hits = find_copies(article_url, api_key=api_key, client=exa_client, cache=step_cache)
        own = registrable_domain(article_url)
        by_domain: dict[str, str] = {}
        for h in hits:
            d = registrable_domain(h["url"])
            if d != own and d not in by_domain and h.get("text") and looks_like_copy(article_md, h["text"], ""):
                by_domain[d] = h["url"]
        urls = list(by_domain.items())[:max_copies]
        if not urls:
            return []
        with ThreadPoolExecutor(max_workers=len(urls), thread_name_prefix="copy") as pool:
            htmls = list(pool.map(lambda du: _html(fetcher, du[1]), urls))
        return [CopyPage(url, domain, html) for (domain, url), html in zip(urls, htmls)]
    except Exception as exc:       # a failed side search must never break the analysis
        log.warning("copy search failed: %s", type(exc).__name__)
        return []


def _html(fetcher, url: str) -> str | None:
    try:
        return fetcher.fetch_html(url)
    except Exception:
        return None
