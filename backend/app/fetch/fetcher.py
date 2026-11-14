"""Fetch layer (B2): Context.dev first, trafilatura fallback; Telegram web preview; neutral statuses.

Everything unfetchable ends in a status that is NEUTRAL for the article verdict (AGENTS.md section 7):
`unreachable`, `paywall`, `blocked`, `not_fetched` (social / self-citation). Nothing here raises for
network problems; callers get a FetchResult.
"""
from __future__ import annotations

import html as htmllib
import logging
import re
from dataclasses import dataclass, field

import httpx

from .urls import classify_url

log = logging.getLogger("citetrace.fetch")

CONTEXT_DEV_URL = "https://api.context.dev/v1/web/scrape"
MAX_TEXT_CHARS = 200_000
MIN_TEXT_CHARS = 200
USER_AGENT = "Mozilla/5.0 (compatible; CiteTraceBot/0.1; +research)"
PAYWALL_MARKERS = (
    "subscribe to read", "subscribe to continue", "subscription required", "sign in to read",
    "become a member to", "register to continue", "for subscribers only", "already a subscriber",
)


@dataclass
class FetchResult:
    url: str
    kind: str                      # contract Source.kind: link | telegram | social | self
    status: str                    # ok | unreachable | paywall | blocked | not_fetched
    via: str | None = None         # context_dev | trafilatura | telegram
    text: str | None = None        # markdown, links kept
    html: str | None = None        # raw HTML (infra signals regex, B5)
    title: str | None = None
    final_url: str | None = None
    subtype: str | None = None     # self_citation | social_media (claim subtype)
    error: str | None = None
    extra: dict = field(default_factory=dict)


class Fetcher:
    def __init__(self, *, context_dev_key: str = "", timeout: float = 20.0,
                 client: httpx.Client | None = None):
        self.key = context_dev_key
        self.timeout = timeout
        self.client = client or httpx.Client(
            timeout=timeout, follow_redirects=True, headers={"User-Agent": USER_AGENT}
        )

    # ------------------------------------------------------------------ public
    def fetch_article(self, url: str) -> FetchResult:
        """The analysed article itself: always fetched, raw HTML kept for infra signals."""
        res = self._fetch_web(url, kind="link")
        if res.html is None and res.status == "ok":
            res.html = self._raw_html(url)
        return res

    def fetch_source(self, url: str, article_url: str) -> FetchResult:
        kind = classify_url(url, article_url)
        if kind == "self":
            return FetchResult(url, "self", "not_fetched", subtype="self_citation")
        if kind == "social":
            return FetchResult(url, "social", "not_fetched", subtype="social_media")
        if kind == "telegram":
            return self._fetch_telegram(url)
        return self._fetch_web(url, kind="link")

    # --------------------------------------------------------------- web pages
    def _fetch_web(self, url: str, kind: str) -> FetchResult:
        if self.key:
            res = self._context_dev(url, kind)
            if res.status == "ok":
                return res
            log.info("context.dev failed for %s (%s), falling back", url, res.error)
        return self._trafilatura(url, kind)

    def _context_dev(self, url: str, kind: str) -> FetchResult:
        body = {
            "url": url,
            "formats": {"markdown": True, "html": True},
            "markdownParams": {"includeLinks": True, "includeImages": False},
            "sharedParams": {"mainContentOnly": True},
            "timeout": {"milliseconds": int(self.timeout * 1000), "behavior": "fail"},
        }
        try:
            r = self.client.post(
                CONTEXT_DEV_URL, json=body, headers={"Authorization": f"Bearer {self.key}"},
                timeout=self.timeout + 5,
            )
        except httpx.HTTPError as exc:
            return FetchResult(url, kind, "unreachable", error=f"context_dev: {type(exc).__name__}")
        if r.status_code != 200:
            return FetchResult(url, kind, _status_for_http(r.status_code), error=f"context_dev http {r.status_code}")
        try:
            data = r.json()
            md = data.get("markdown") or {}
            if not md.get("success"):
                code = md.get("error_code", "")
                status = "blocked" if code in ("WEBSITE_BLOCKED", "WEBSITE_ACCESS_ERROR") else "unreachable"
                return FetchResult(url, kind, status, error=f"context_dev {code or 'markdown failed'}")
            text = md.get("data") or ""
            html_out = (data.get("html") or {}).get("data")
            meta = data.get("metadata") or {}
        except (ValueError, AttributeError):
            return FetchResult(url, kind, "unreachable", error="context_dev: bad response")
        res = _finish(url, kind, text, "context_dev", html=html_out, title=meta.get("title"),
                      final_url=meta.get("finalUrl"))
        return res

    def _trafilatura(self, url: str, kind: str) -> FetchResult:
        try:
            r = self.client.get(url)
        except httpx.HTTPError as exc:
            return FetchResult(url, kind, "unreachable", error=f"{type(exc).__name__}")
        if r.status_code >= 400:
            return FetchResult(url, kind, _status_for_http(r.status_code), error=f"http {r.status_code}")
        ctype = r.headers.get("content-type", "")
        if ctype and "html" not in ctype and "xml" not in ctype and "text" not in ctype:
            return FetchResult(url, kind, "blocked", error=f"unsupported content-type {ctype}")
        import trafilatura

        raw = r.text
        text = trafilatura.extract(
            raw, output_format="markdown", include_links=True, include_comments=False,
            include_tables=False, favor_recall=True, url=str(r.url),
        ) or ""
        meta = trafilatura.extract_metadata(raw)
        return _finish(url, kind, text, "trafilatura", html=raw,
                       title=getattr(meta, "title", None), final_url=str(r.url))

    def _raw_html(self, url: str) -> str | None:
        try:
            r = self.client.get(url)
            return r.text if r.status_code < 400 else None
        except httpx.HTTPError:
            return None

    # ---------------------------------------------------------------- telegram
    def _fetch_telegram(self, url: str) -> FetchResult:
        """Public channels only, via https://t.me/s/<channel>[/<post id>] (no login needed)."""
        m = re.match(r"https?://(?:www\.)?(?:t|telegram)\.me/(?:s/)?([A-Za-z0-9_]{3,})(?:/(\d+))?", url)
        if not m:
            return FetchResult(url, "telegram", "unreachable", error="unrecognised telegram URL")
        channel, post = m.group(1), m.group(2)
        try:
            r = self.client.get(f"https://t.me/s/{channel}" + (f"/{post}" if post else ""))
        except httpx.HTTPError as exc:
            return FetchResult(url, "telegram", "unreachable", error=type(exc).__name__)
        if r.status_code >= 400:
            return FetchResult(url, "telegram", _status_for_http(r.status_code), error=f"http {r.status_code}")
        text = parse_telegram_preview(r.text, channel, post)
        if not text:
            # private channel or no web preview -> neutral, not evidence against anyone
            return FetchResult(url, "telegram", "blocked", via="telegram", error="no public preview")
        return FetchResult(url, "telegram", "ok", via="telegram", text=text[:MAX_TEXT_CHARS],
                           html=r.text, final_url=str(r.url))


def parse_telegram_preview(page: str, channel: str, post: str | None) -> str:
    """Extract message text from a t.me/s page. If `post` is given, only that message."""
    blocks = re.split(r'(?=<div class="tgme_widget_message_wrap)', page)
    messages: list[tuple[str, str]] = []
    for b in blocks:
        pid = re.search(r'data-post="([^"]+)"', b)
        body = re.search(r'<div class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>', b, re.S)
        if pid and body:
            messages.append((pid.group(1), _html_to_text(body.group(1))))
    if post:
        want = f"{channel}/{post}".lower()
        messages = [m for m in messages if m[0].lower() == want]
    return "\n\n".join(t for _, t in messages if t).strip()


def _html_to_text(fragment: str) -> str:
    fragment = re.sub(r"<br\s*/?>", "\n", fragment)
    fragment = re.sub(r'<a [^>]*href="([^"]+)"[^>]*>(.*?)</a>', r"[\2](\1)", fragment, flags=re.S)
    fragment = re.sub(r"<[^>]+>", "", fragment)
    return htmllib.unescape(fragment).strip()


# ---------------------------------------------------------------------- helpers
def _status_for_http(code: int) -> str:
    if code in (401, 402):
        return "paywall"
    if code in (403, 429, 451):
        return "blocked"
    return "unreachable"  # 404/410/5xx/timeouts: dead link, neutral


def _finish(url: str, kind: str, text: str, via: str, *, html: str | None, title: str | None,
            final_url: str | None) -> FetchResult:
    text = (text or "").strip()
    low = text.lower()
    if len(text) < MIN_TEXT_CHARS:
        status = "paywall" if any(p in low for p in PAYWALL_MARKERS) else "blocked"
        return FetchResult(url, kind, status, via=via, text=text or None, html=html, title=title,
                           final_url=final_url, error=f"extracted text too short ({len(text)} chars)")
    if any(p in low for p in PAYWALL_MARKERS) and len(text) < 1500:
        return FetchResult(url, kind, "paywall", via=via, text=text, html=html, title=title,
                           final_url=final_url, error="paywall marker on short text")
    return FetchResult(url, kind, "ok", via=via, text=text[:MAX_TEXT_CHARS], html=html, title=title,
                       final_url=final_url)
