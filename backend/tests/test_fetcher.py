import json
from pathlib import Path

import httpx

from app.fetch import Fetcher
from app.fetch.fetcher import RateLimiter, parse_telegram_preview

FX = Path(__file__).parent / "fixtures" / "fetch"
ART = "https://news.test/a"


def fx(name):
    return (FX / name).read_text()


def make(handler, key="", **kw):
    return Fetcher(context_dev_key=key, backoff_s=0, **kw, client=httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True))


def test_social_and_self_are_neutral_and_not_fetched():
    def boom(request):
        raise AssertionError("must not hit the network")
    f = make(boom)
    s = f.fetch_source("https://x.com/u/status/1", ART)
    assert (s.status, s.kind, s.subtype) == ("not_fetched", "social", "social_media")
    s = f.fetch_source("https://www.news.test/other", ART)
    assert (s.status, s.kind, s.subtype) == ("not_fetched", "self", "self_citation")


def test_trafilatura_fallback_keeps_links_and_html():
    f = make(lambda r: httpx.Response(200, text=fx("article.html"), headers={"content-type": "text/html"}))
    r = f.fetch_source("https://example.org/report", ART)
    assert r.status == "ok" and r.via == "trafilatura"
    assert "programme will expand" in r.text
    assert "https://example.org/report" in r.text  # link kept in markdown
    assert r.html and "<article>" in r.html


def test_context_dev_ok_and_request_shape():
    seen = {}

    def handler(request):
        seen["auth"] = request.headers["authorization"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=json.loads(fx("contextdev_ok.json")))
    f = make(handler, key="k123")
    r = f.fetch_article(ART)
    assert r.status == "ok" and r.via == "context_dev" and r.title == "Test Article"
    assert "G-ABC123" in r.html
    assert seen["auth"] == "Bearer k123"
    # verified against the live API: unknown top-level keys (e.g. "timeout") are rejected with 400
    assert set(seen["body"]) == {"url", "formats", "markdownParams", "sharedParams"}
    assert seen["body"]["formats"]["markdown"] is True
    assert seen["body"]["markdownParams"]["includeLinks"] is True


def test_context_dev_failure_falls_back():
    def handler(request):
        if request.url.host == "api.context.dev":
            return httpx.Response(200, json=json.loads(fx("contextdev_blocked.json")))
        return httpx.Response(200, text=fx("article.html"), headers={"content-type": "text/html"})
    r = make(handler, key="k").fetch_source("https://example.org/report", ART)
    assert r.status == "ok" and r.via == "trafilatura"


def test_http_statuses_map_to_neutral():
    for code, want in [(404, "unreachable"), (410, "unreachable"), (500, "unreachable"),
                       (402, "paywall"), (401, "paywall"), (403, "blocked"), (429, "blocked")]:
        r = make(lambda req, c=code: httpx.Response(c)).fetch_source("https://example.org/x", ART)
        assert r.status == want, code
        assert r.text is None


def test_network_error_is_unreachable_not_exception():
    def handler(request):
        raise httpx.ConnectError("dns")
    r = make(handler).fetch_source("https://dead.example/x", ART)
    assert r.status == "unreachable"


def test_paywall_page_detected():
    r = make(lambda req: httpx.Response(200, text=fx("paywall.html"), headers={"content-type": "text/html"})
             ).fetch_source("https://example.org/x", ART)
    assert r.status == "paywall"


def test_telegram_single_post_and_channel():
    assert parse_telegram_preview(fx("telegram.html"), "chan_a", "10") == "First post\nsecond line & more"
    assert "[a link](https://example.org/x)" in parse_telegram_preview(fx("telegram.html"), "chan_a", "11")
    both = parse_telegram_preview(fx("telegram.html"), "chan_a", None)
    assert "First post" in both and "Eleventh" in both

    urls = []

    def handler(request):
        urls.append(str(request.url))
        return httpx.Response(200, text=fx("telegram.html"))
    r = make(handler).fetch_source("https://t.me/chan_a/10", ART)
    assert r.status == "ok" and r.via == "telegram" and r.kind == "telegram"
    assert urls == ["https://t.me/s/chan_a/10"] and r.text.startswith("First post")


def test_telegram_without_preview_is_neutral():
    r = make(lambda req: httpx.Response(200, text="<html></html>")).fetch_source("https://t.me/private_x/3", ART)
    assert r.status == "blocked"


def test_context_dev_retries_transient_then_succeeds():
    calls = []

    def handler(request):
        calls.append(1)
        if len(calls) < 3:
            return httpx.Response(502 if len(calls) == 1 else 503)
        return httpx.Response(200, json=json.loads(fx("contextdev_ok.json")))
    r = make(handler, key="k").fetch_article(ART)
    assert r.status == "ok" and r.via == "context_dev" and len(calls) == 3


def test_fallback_records_context_dev_error():
    def handler(request):
        if request.url.host == "api.context.dev":
            return httpx.Response(401)
        return httpx.Response(200, text=fx("article.html"), headers={"content-type": "text/html"})
    r = make(handler, key="bad").fetch_source("https://example.org/report", ART)
    assert r.via == "trafilatura" and "401" in r.extra["context_dev_error"]


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def now(self):
        return self.t

    def sleep(self, s):
        self.t += s


def test_rate_limiter_window_and_max_wait():
    c = FakeClock()
    rl = RateLimiter(3, clock=c.now, sleep=c.sleep)
    assert [rl.acquire() for _ in range(3)] == [True] * 3
    assert rl.acquire(0) is False            # no waiting allowed -> fall back
    assert rl.acquire(10) is False           # would need ~60s
    assert rl.acquire(70) is True            # waits for the window to slide
    assert c.t >= 59


def test_rate_limiter_penalize():
    c = FakeClock()
    rl = RateLimiter(10, clock=c.now, sleep=c.sleep)
    rl.penalize(42)
    assert rl.acquire(0) is False
    c.t = 43
    assert rl.acquire(0) is True


def test_429_falls_back_without_retry_and_pauses_context_dev():
    cd_calls = []

    def handler(request):
        if request.url.host == "api.context.dev":
            cd_calls.append(1)
            return httpx.Response(429, headers={"retry-after": "42"})
        return httpx.Response(200, text=fx("article.html"), headers={"content-type": "text/html"})
    f = make(handler, key="k")
    for _ in range(3):
        r = f.fetch_source("https://example.org/report", ART)
        assert r.status == "ok" and r.via == "trafilatura"
    assert len(cd_calls) == 1               # later fetches skip Context.dev while penalised


def test_sources_never_wait_but_article_does():
    c = FakeClock()
    rl = RateLimiter(1, clock=c.now, sleep=c.sleep)
    ok = json.loads(fx("contextdev_ok.json"))

    def handler(request):
        if request.url.host == "api.context.dev":
            return httpx.Response(200, json=ok)
        return httpx.Response(200, text=fx("article.html"), headers={"content-type": "text/html"})
    f = make(handler, key="k", limiter=rl, article_wait_s=70)
    assert f.fetch_source("https://example.org/a", ART).via == "context_dev"
    assert f.fetch_source("https://example.org/b", ART).via == "trafilatura"   # slot used, no waiting
    assert f.fetch_article(ART).via == "context_dev"                           # article waits for a slot
