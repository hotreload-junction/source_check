"""F10: the article as the reader's browser shows it (browser extension, POST /api/analyses/page).

The extension sends the page's rendered HTML (`document.documentElement.outerHTML`). It is read with the same
trafilatura settings as the server-side fallback fetch (markdown, links kept), so the claim -> source -> verify
pipeline runs unchanged. The HTML is also what the infrastructure signals read (analytics / AdSense IDs) and where the
publication date comes from.

Trust: this text comes from the client, not from our own fetch. It is only ever used for the analysis it was sent
with: the cache key carries a hash of the extracted text (api.page_key), so a page sent by one reader can never be
served to someone who asks for the same URL.
"""
from __future__ import annotations

import logging

from .fetcher import FetchResult, _finish
from .meta import parse_published

log = logging.getLogger("citetrace.page")
MAX_PAGE_HTML_CHARS = 6_000_000       # a long news page with inline scripts is ~1-3 MB; more is not an article


def page_to_article(url: str, html: str, title: str | None = None) -> FetchResult:
    """-> the article as `FetchResult` (status ok, or blocked / paywall with a reason the reader can act on)."""
    import trafilatura

    try:
        text = trafilatura.extract(html, output_format="markdown", include_links=True, include_comments=False,
                                   include_tables=False, favor_recall=True, url=url) or ""
        meta = trafilatura.extract_metadata(html)
    except Exception as exc:          # malformed markup must never take the API down
        log.info("page HTML unreadable for %s: %s", url, type(exc).__name__)
        return FetchResult(url, "link", "blocked", via="browser", error="the page HTML could not be read")
    title = (title or "").strip() or getattr(meta, "title", None) or None
    res = _finish(url, "link", text, "browser", html=html, title=title, final_url=url)
    res.published_at = parse_published(html)
    if res.status != "ok" and res.error and "too short" in res.error:
        res.error = "no article text found on this page (is it an article, and fully loaded?)"
    return res
