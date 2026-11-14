import json
from pathlib import Path

import httpx

from app.fetch import Fetcher
from app.fetch.fetcher import parse_telegram_preview

FX = Path(__file__).parent / "fixtures" / "fetch"
ART = "https://news.test/a"


def fx(name):
    return (FX / name).read_text()


def make(handler, key=""):
    return Fetcher(context_dev_key=key, client=httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True))


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
