"""Fetch layer (B2): Context.dev first, trafilatura fallback; Telegram web preview; neutral statuses.

Everything unfetchable ends in a status that is NEUTRAL for the article verdict (AGENTS.md section 7):
`unreachable`, `paywall`, `blocked`, `not_fetched` (social / self-citation). Nothing here raises for
network problems; callers get a FetchResult.
"""
from __future__ import annotations

import base64
import html as htmllib
import logging
import re
import threading
import time
import zlib
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

import httpx

from .meta import parse_published
from .urls import InvalidURL, classify_url, normalize_url

log = logging.getLogger("citetrace.fetch")

# bump when the way we READ pages changes (text cleaning, extraction): old cached pages are then ignored
FETCH_CACHE_VERSION = 2          # 2: invisible characters are stripped from page text
CONTEXT_DEV_URL = "https://api.context.dev/v1/web/scrape"
MAX_TEXT_CHARS = 200_000
MIN_TEXT_CHARS = 200
USER_AGENT = "Mozilla/5.0 (compatible; CiteTraceBot/0.1; +research)"
PAYWALL_MARKERS = (
    "subscribe to read", "subscribe to continue", "subscription required", "sign in to read",
    "become a member to", "register to continue", "for subscribers only", "already a subscriber",
)


def _pack_html(html_text: str) -> str:
    return base64.b64encode(zlib.compress(html_text.encode("utf-8"), 6)).decode("ascii")


def _unpack_html(packed: str) -> str:
    return zlib.decompress(base64.b64decode(packed)).decode("utf-8")


def _result_to_cache(r: "FetchResult") -> dict:
    d = {k: getattr(r, k) for k in ("url", "kind", "status", "via", "text", "title", "final_url", "subtype", "error", "published_at", "extra")}
    d["html"] = _pack_html(r.html) if r.html else None
    return d


def _result_from_cache(d: dict) -> "FetchResult":
    d = dict(d)
    d["html"] = _unpack_html(d["html"]) if d.get("html") else None
    return FetchResult(**d)


class RateLimiter:
    """Sliding-window limiter shared by all fetches (Context.dev key: 10 requests/min, measured live).

    `acquire(max_wait)` returns False instead of blocking beyond `max_wait` seconds, so callers can fall
    back to trafilatura rather than stall a live demo.
    """

    def __init__(self, per_minute: int = 10, clock=time.monotonic, sleep=time.sleep):
        self.per_minute = per_minute
        self._clock, self._sleep = clock, sleep
        self._hits: deque[float] = deque()
        self._blocked_until = 0.0
        self._lock = threading.Lock()

    def penalize(self, seconds: float) -> None:
        """Server said 429 with Retry-After: stop sending until then."""
        with self._lock:
            self._blocked_until = max(self._blocked_until, self._clock() + seconds)

    def acquire(self, max_wait: float = 0.0) -> bool:
        deadline = self._clock() + max_wait
        while True:
            with self._lock:
                now = self._clock()
                while self._hits and now - self._hits[0] >= 60:
                    self._hits.popleft()
                wait = max(self._blocked_until - now, 0.0)
                if not wait and len(self._hits) < self.per_minute:
                    self._hits.append(now)
                    return True
                if not wait:
                    wait = 60 - (now - self._hits[0])
            if self._clock() + wait > deadline:
                return False
            self._sleep(min(wait, 1.0))


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
    published_at: str | None = None  # ISO 8601 UTC, from the page's meta tags / JSON-LD
    extra: dict = field(default_factory=dict)


class Fetcher:
    def __init__(self, *, context_dev_key: str = "", timeout: float = 20.0,
                 client: httpx.Client | None = None, retries: int = 2, backoff_s: float = 1.0,
                 limiter: RateLimiter | None = None, article_wait_s: float = 45.0, source_timeout_s: float | None = None,
                 cache=None):
        self.key = context_dev_key
        self.timeout = timeout
        self.retries = retries
        self.backoff_s = backoff_s
        self.limiter = limiter or RateLimiter()
        self.article_wait_s = article_wait_s
        self.cache = cache          # None = process-wide default step cache
        self.source_timeout_s = source_timeout_s or timeout
        self.client = client or httpx.Client(
            timeout=timeout, follow_redirects=True, headers={"User-Agent": USER_AGENT}
        )

    @classmethod
    def from_settings(cls, settings=None, cache=None) -> "Fetcher":
        from ..config import get_settings

        s = settings or get_settings()
        return cls(context_dev_key=s.context_dev_api_key, timeout=s.fetch_timeout_s,
                   limiter=RateLimiter(s.context_dev_rpm), source_timeout_s=s.source_timeout_s, cache=cache)

    # ------------------------------------------------------------------ public
    # ----------------------------------------------------------------- step cache
    def _cache(self):
        from .. import cache as cachemod

        return self.cache if self.cache is not None else cachemod.get_default()

    @staticmethod
    def _ckey(role: str, url: str) -> str:
        from .. import cache as cachemod

        try:
            url = normalize_url(url)
        except InvalidURL:
            pass
        return cachemod.make_key("fetch", FETCH_CACHE_VERSION, role, url)

    def _cached_fetch(self, role: str, url: str, compute) -> FetchResult:
        from .. import cache as cachemod

        cache, key = self._cache(), self._ckey(role, url)
        hit = cache.get("fetch", key)
        if hit is not None:
            return _result_from_cache(hit)
        res = compute()
        # successes are kept; failures only briefly (FAILURE_TTL_S) so a replay is identical but a later retry can win
        cache.put("fetch", key, _result_to_cache(res), None if res.status == "ok" else cachemod.FAILURE_TTL_S)
        return res

    def fetch_article(self, url: str) -> FetchResult:
        return self._cached_fetch("article", url, lambda: self._fetch_article(url))

    def _fetch_article(self, url: str) -> FetchResult:
        """The analysed article itself: always fetched, raw HTML kept for infra signals.

        Markdown (Context.dev) and the raw HTML GET are independent, so they run side by side; measured live,
        doing them one after the other put 40 s on the critical path for AP."""
        with ThreadPoolExecutor(max_workers=2) as pool:
            html_future = pool.submit(self._raw_html, url)
            res = self._fetch_web(url, kind="link", wait=self.article_wait_s)
            if res.html is None:
                res.html = html_future.result()
        if res.html is None and self.key:  # blocked for direct GET: one full-page Context.dev call
            res.html = self._context_full_html(url, self.article_wait_s)
        res.published_at = parse_published(res.html)
        return res

    def fetch_source(self, url: str, article_url: str, *, with_html: bool = True) -> FetchResult:
        kind = classify_url(url, article_url)
        if kind in ("self", "social"):                       # never fetched: nothing to cache
            return self._fetch_source(url, article_url, with_html=with_html)
        res = self._cached_fetch("source", url, lambda: self._fetch_source(url, article_url, with_html=False))
        if with_html and res.status == "ok" and res.html is None:
            res.html = self.fetch_html(url)
        return res

    def _fetch_source(self, url: str, article_url: str, *, with_html: bool = True) -> FetchResult:
        """`with_html=False`: skip the raw-HTML GET (only infra signals need it; fetch it later with
        `fetch_html`, off the critical path of verification)."""
        kind = classify_url(url, article_url)
        if kind == "self":
            return FetchResult(url, "self", "not_fetched", subtype="self_citation")
        if kind == "social":
            return FetchResult(url, "social", "not_fetched", subtype="social_media")
        if kind == "telegram":
            return self._fetch_telegram(url)
        # sources: no 5xx retries (3 attempts stacked to 25 s in a live run); the direct GET below is the fallback
        res = self._fetch_web(url, kind="link", ctx_timeout=self.source_timeout_s, retries=0)
        if with_html and res.status == "ok" and res.html is None:
            res.html = self._raw_html(url)  # full HTML for infra signals (B5); None if blocked -> neutral
        return res

    def fetch_linked_page(self, url: str) -> FetchResult:
        """A page of the article's OWN site that the article links (a self-citation). `fetch_source` never reads those (a
        self-citation is no independent evidence), but the chain can be followed one step: what does THAT page cite?"""
        res = self._cached_fetch("source", url, lambda: self._fetch_web(url, kind="link", ctx_timeout=self.source_timeout_s, retries=0))
        res.kind, res.subtype = "self", "self_citation"
        return res

    def fetch_html(self, url: str) -> str | None:
        cache, key = self._cache(), self._ckey("html", url)
        hit = cache.get("html", key)
        if hit is not None:
            return _unpack_html(hit) if hit else None
        from .. import cache as cachemod

        html_text = self._raw_html(url)
        if html_text:
            cache.put("html", key, _pack_html(html_text))
        else:      # "no HTML" (blocked) is an answer too: replay it, retry after FAILURE_TTL_S
            cache.put("html", key, "", cachemod.FAILURE_TTL_S)
        return html_text

    # --------------------------------------------------------------- web pages
    def _fetch_web(self, url: str, kind: str, wait: float = 0.0, ctx_timeout: float | None = None,
                   retries: int | None = None) -> FetchResult:
        if self.key:
            res = self._context_dev(url, kind, wait, ctx_timeout, retries)
            if res.status == "ok":
                return res
            log.warning("context.dev failed for %s (%s), falling back", url, res.error)
            fallback = self._trafilatura(url, kind)
            fallback.extra["context_dev_error"] = res.error
            if fallback.status != "ok" and res.error and "Timeout" in res.error:
                # Sites that block direct requests (president.gov.ua) and are slow for Context.dev: once its
                # server finished, the page sits in its cache and a retry answers in <1s (measured: 73s -> 0.4s).
                retry = self._context_dev(url, kind, wait, ctx_timeout, retries)
                if retry.status == "ok":
                    return retry
            return fallback
        return self._trafilatura(url, kind)

    def _context_dev(self, url: str, kind: str, wait: float = 0.0, timeout: float | None = None,
                     retries: int | None = None) -> FetchResult:
        timeout = timeout or self.timeout
        retries = self.retries if retries is None else retries
        body = {
            "url": url,
            "formats": {"markdown": True},
            "markdownParams": {"includeLinks": True, "includeImages": False},
            "sharedParams": {"mainContentOnly": True},
        }
        r: httpx.Response | None = None
        err = ""
        for attempt in range(retries + 1):
            if attempt:
                time.sleep(self.backoff_s * attempt)
            if not self.limiter.acquire(wait):
                return FetchResult(url, kind, "unreachable", error="context_dev: local rate limit")
            try:
                r = self.client.post(
                    CONTEXT_DEV_URL, json=body, headers={"Authorization": f"Bearer {self.key}"},
                    timeout=timeout + 5,
                )
            except httpx.TimeoutException as exc:
                # A timeout already cost the full timeout; retrying costs it again (3x25s seen live on
                # president.gov.ua). Give up at once, the caller falls back to trafilatura.
                return FetchResult(url, kind, "unreachable", error=f"context_dev: {type(exc).__name__}")
            except httpx.HTTPError as exc:
                r, err = None, f"context_dev: {type(exc).__name__}"
                continue
            if r.status_code == 429:  # rate limited: no retry, caller falls back; pause everyone
                try:
                    self.limiter.penalize(float(r.headers.get("retry-after", 60)))
                except ValueError:
                    self.limiter.penalize(60)
                return FetchResult(url, kind, "blocked", error="context_dev http 429")
            if r.status_code in (500, 502, 503, 504, 408):  # transient: retry
                err = f"context_dev http {r.status_code}"
                continue
            break
        if r is None:
            return FetchResult(url, kind, "unreachable", error=err)
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
            html_out = None  # mainContentOnly strips <head> scripts; raw HTML comes from _raw_html
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

    def _context_full_html(self, url: str, wait: float) -> str | None:
        """Whole page HTML (incl. <head> analytics scripts) via Context.dev, for pages we cannot GET directly."""
        if not self.limiter.acquire(wait):
            return None
        body = {"url": url, "formats": {"html": True}, "sharedParams": {"mainContentOnly": False}}
        try:
            r = self.client.post(CONTEXT_DEV_URL, json=body, headers={"Authorization": f"Bearer {self.key}"},
                                 timeout=self.timeout + 5)
            if r.status_code == 429:
                self.limiter.penalize(float(r.headers.get("retry-after", 60)))
            if r.status_code != 200:
                return None
            return (r.json().get("html") or {}).get("data") or None
        except (httpx.HTTPError, ValueError):
            return None

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


# A page that stops with one of these was cut off (live: Yahoo Finance ends at "Story Continues"). Whatever the
# article says after the cut is unknown, so absence of a claim proves nothing.
CUTOFF_MARKERS = ("story continues", "continue reading", "continue with the full", "read the full story", "read more on",
                  "to read the full", "sign in to continue", "subscribe to continue", "view the full article")


def ends_cut_off(text: str) -> bool:
    tail = text[-400:].lower()
    return any(m in tail for m in CUTOFF_MARKERS)


# Zero-width and byte-order characters some sites sprinkle into their text (DW: U+2060, U+200B). They are invisible but
# make the same sentence differ between two fetches, which made the claim extraction pick different sentences.
_INVISIBLE = re.compile("[\u200b\u200c\u200d\u2060\ufeff\u00ad]")


def clean_text(text: str) -> str:
    return _INVISIBLE.sub("", text.replace("\u00a0", " "))


def _finish(url: str, kind: str, text: str, via: str, *, html: str | None, title: str | None,
            final_url: str | None) -> FetchResult:
    text = clean_text(text or "").strip()
    low = text.lower()
    if len(text) < MIN_TEXT_CHARS:
        status = "paywall" if any(p in low for p in PAYWALL_MARKERS) else "blocked"
        return FetchResult(url, kind, status, via=via, text=text or None, html=html, title=title,
                           final_url=final_url, error=f"extracted text too short ({len(text)} chars)")
    if ends_cut_off(text):
        return FetchResult(url, kind, "paywall", via=via, text=text, html=html, title=title, final_url=final_url,
                           error="text ends with a continuation marker (page is cut off)")
    if any(p in low for p in PAYWALL_MARKERS) and len(text) < 1500:
        return FetchResult(url, kind, "paywall", via=via, text=text, html=html, title=title,
                           final_url=final_url, error="paywall marker on short text")
    return FetchResult(url, kind, "ok", via=via, text=text[:MAX_TEXT_CHARS], html=html, title=title,
                       final_url=final_url)
