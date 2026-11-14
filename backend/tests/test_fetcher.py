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
        if request.url.host != "api.context.dev":   # raw HTML comes from a direct GET
            return httpx.Response(200, text=fx("article.html"))
        seen["auth"] = request.headers["authorization"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=json.loads(fx("contextdev_ok.json")))
    f = make(handler, key="k123")
    r = f.fetch_article(ART)
    assert r.status == "ok" and r.via == "context_dev" and r.title == "Test Article"
    assert "G-ABC123" in r.html          # <head> script survives because we do not use mainContentOnly HTML
    assert seen["body"]["formats"] == {"markdown": True}
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
        if request.url.host != "api.context.dev":
            return httpx.Response(200, text=fx("article.html"))
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


def test_article_html_falls_back_to_full_context_dev_when_direct_get_blocked():
    bodies = []

    def handler(request):
        if request.url.host == "api.context.dev":
            body = json.loads(request.content)
            bodies.append(body)
            if "html" in body["formats"]:
                return httpx.Response(200, json={"html": {"success": True, "data": "<head>G-FULL12345</head>"}})
            return httpx.Response(200, json=json.loads(fx("contextdev_ok.json")))
        return httpx.Response(403)
    r = make(handler, key="k").fetch_article(ART)
    assert r.status == "ok" and r.html == "<head>G-FULL12345</head>"
    assert bodies[1]["sharedParams"]["mainContentOnly"] is False


def test_source_html_is_direct_get_and_none_when_blocked():
    def handler(request):
        if request.url.host == "api.context.dev":
            return httpx.Response(200, json=json.loads(fx("contextdev_ok.json")))
        return httpx.Response(403)
    r = make(handler, key="k").fetch_source("https://example.org/report", ART)
    assert r.status == "ok" and r.html is None     # neutral: signals skip this domain


def test_context_dev_timeout_is_not_retried_and_falls_back():
    cd_calls = []

    def handler(request):
        if request.url.host == "api.context.dev":
            cd_calls.append(1)
            raise httpx.ReadTimeout("slow")
        return httpx.Response(200, text=fx("article.html"), headers={"content-type": "text/html"})
    r = make(handler, key="k").fetch_source("https://example.org/report", ART)
    assert r.status == "ok" and r.via == "trafilatura" and len(cd_calls) == 1
    assert "ReadTimeout" in r.extra["context_dev_error"]


def test_timeout_then_blocked_fallback_gets_one_cached_retry():
    cd_calls = []
    ok = json.loads(fx("contextdev_ok.json"))

    def handler(request):
        if request.url.host == "api.context.dev":
            cd_calls.append(1)
            if len(cd_calls) == 1:
                raise httpx.ReadTimeout("slow")      # server still fetching
            return httpx.Response(200, json=ok)      # second call: answered from Context.dev's cache
        return httpx.Response(403)                    # site blocks direct requests
    r = make(handler, key="k").fetch_source("https://example.org/report", ART)
    assert r.status == "ok" and r.via == "context_dev" and len(cd_calls) == 2


def test_no_retry_when_fallback_succeeded_or_error_was_not_a_timeout():
    cd_calls = []

    def handler(request):
        if request.url.host == "api.context.dev":
            cd_calls.append(1)
            raise httpx.ReadTimeout("slow")
        return httpx.Response(200, text=fx("article.html"), headers={"content-type": "text/html"})
    assert make(handler, key="k").fetch_source("https://example.org/report", ART).via == "trafilatura"
    assert len(cd_calls) == 1


def test_article_markdown_and_html_are_fetched_concurrently():
    import threading
    import time
    started, order = {}, []

    def handler(request):
        started[request.url.host] = time.time()
        if request.url.host == "api.context.dev":
            time.sleep(0.3)
            order.append("ctx")
            return httpx.Response(200, json=json.loads(fx("contextdev_ok.json")))
        order.append("html")
        return httpx.Response(200, text=fx("article.html"))
    r = make(handler, key="k").fetch_article(ART)
    assert r.status == "ok" and "G-ABC123" in r.html
    assert order == ["html", "ctx"]                                   # the quick GET finished while Context.dev was busy
    assert abs(started["api.context.dev"] - started[ART.split("/")[2]]) < 0.2   # both started together


def test_fetch_source_without_html_skips_the_raw_get():
    gets = []

    def handler(request):
        if request.url.host == "api.context.dev":
            return httpx.Response(200, json=json.loads(fx("contextdev_ok.json")))
        gets.append(1)
        return httpx.Response(200, text=fx("article.html"))
    f = make(handler, key="k")
    assert f.fetch_source("https://example.org/report", ART, with_html=False).html is None and gets == []
    assert f.fetch_html("https://example.org/report") is not None and gets == [1]


def test_sources_use_the_shorter_context_dev_timeout_articles_do_not():
    seen = []
    ok = json.loads(fx("contextdev_ok.json"))

    def handler(request):
        if request.url.host == "api.context.dev":
            seen.append(request.extensions["timeout"]["read"])
            return httpx.Response(200, json=ok)
        return httpx.Response(200, text=fx("article.html"))
    f = Fetcher(context_dev_key="k", timeout=30, source_timeout_s=8, backoff_s=0,
                client=httpx.Client(transport=httpx.MockTransport(handler)))
    f.fetch_source("https://example.org/report", ART, with_html=False)
    f.fetch_article(ART)
    assert seen == [13, 35]        # source: 8+5, article: 30+5


def test_sources_do_not_retry_5xx_but_articles_still_do():
    cd = []
    ok = json.loads(fx("contextdev_ok.json"))

    def handler(request):
        if request.url.host == "api.context.dev":
            cd.append(1)
            return httpx.Response(503) if len(cd) % 3 != 0 else httpx.Response(200, json=ok)
        return httpx.Response(200, text=fx("article.html"), headers={"content-type": "text/html"})
    f = make(handler, key="k")
    assert f.fetch_source("https://example.org/report", ART, with_html=False).via == "trafilatura"
    assert len(cd) == 1                                                  # one try, then the direct GET
    cd.clear()
    assert f.fetch_article(ART).via == "context_dev" and len(cd) == 3   # 503, 503, 200


def test_text_cut_off_at_a_continuation_marker_is_paywall_not_ok():
    body = "<html><body><article><p>" + ("The company said profits rose strongly in the third quarter. " * 20) + "</p><p>Story Continues</p></article></body></html>"
    r = make(lambda req: httpx.Response(200, text=body, headers={"content-type": "text/html"})).fetch_source("https://example.org/x", ART)
    assert r.status == "paywall" and "cut off" in r.error
    full = body.replace("Story Continues", "The outlook remains positive.")
    assert make(lambda req: httpx.Response(200, text=full, headers={"content-type": "text/html"})).fetch_source("https://example.org/x", ART).status == "ok"


def test_invisible_characters_are_removed_from_page_text():
    from app.fetch.fetcher import clean_text
    assert clean_text("Would \u2060Ukraine \u200bwithdraw\ufeff from\u00a0Donbas?\u00ad") == "Would Ukraine withdraw from Donbas?"
    body = "<html><body><article><p>" + ("The ministry said \u2060on Tuesday that the programme will expand\u200b coverage to forty districts. " * 8) + "</p></article></body></html>"
    r = make(lambda req: httpx.Response(200, text=body, headers={"content-type": "text/html"})).fetch_source("https://example.org/x", ART)
    assert r.status == "ok" and "\u2060" not in r.text and "\u200b" not in r.text and "said on Tuesday" in r.text


def test_redirect_to_social_is_neutral_and_skips_raw_html():
    def handler(request):
        assert request.url.host == "api.context.dev"
        payload = {
            "url": "https://short.example/go",
            "markdown": {"requested": True, "success": True, "data": "Post text with enough characters " * 20},
            "metadata": {"title": "Post", "finalUrl": "https://x.com/user/status/1"},
        }
        return httpx.Response(200, json=payload)

    r = make(handler, key="k").fetch_source("https://short.example/go", ART)
    assert (r.kind, r.status, r.subtype, r.final_url) == ("social", "not_fetched", "social_media", "https://x.com/user/status/1")
    assert r.html is None


def test_repeated_continue_reading_cards_do_not_trigger_paywall():
    from app.fetch.fetcher import ends_cut_off

    cards = (
        ("Speaker bio paragraph with plenty of background context on global talks. " * 8)
        + "\nContinue reading\n"
        + ("Another talk summary card in the page body. " * 12)
        + "\nContinue reading\n"
        + ("More unrelated listing text across the page. " * 10)
        + "\nContinue reading"
    )
    assert ends_cut_off(cards) is False
    assert ends_cut_off(("Main article paragraph with real content. " * 12) + "\nStory Continues") is True
