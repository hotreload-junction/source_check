import json
from datetime import datetime, timezone
from pathlib import Path

import httpx

from app.signals import RdapClient, RdapResult, SourcePage, analyze_infrastructure, extract_ids

FX = Path(__file__).parent / "fixtures" / "signals"


def test_extract_ids_finds_all_kinds():
    ids = extract_ids((FX / "pravda_like.html").read_text())
    assert ids.analytics_ids == ["G-EXAMPLE01", "GTM-AB12CD", "UA-1234567-2"]
    assert ids.adsense_ids == ["ca-pub-0000000000000001"]


def test_extract_ids_ignores_placeholders_and_words():
    ids = extract_ids((FX / "placeholders.html").read_text())
    assert ids.all == []
    assert extract_ids(None).all == [] and extract_ids("").all == []


def _rdap_stub(ages):
    def f(domain):
        a = ages.get(domain)
        return RdapResult(domain, "2026-09-20T00:00:00Z" if a is not None else None, a)
    return f


def test_cluster_by_shared_id_and_reasons():
    a = '<script>gtag("config","G-SHARED001")</script>'
    b = '<script>gtag("config","G-SHARED001")</script><ins data-ad-client="ca-pub-1111111111"></ins>'
    c = '<ins data-ad-client="ca-pub-1111111111"></ins>'  # joins via AdSense, transitive
    d = '<script>gtag("config","G-OTHER0002")</script>'
    res = analyze_infrastructure(
        "www.art.com", a,
        [SourcePage("b.net", b), SourcePage("c.org", c), SourcePage("d.gov", d), SourcePage("art.com", a)],
        _rdap_stub({"art.com": 3000, "b.net": 55}),
    )
    by = {x["domain"]: x for x in res.domains}
    assert list(by) == ["art.com", "b.net", "c.org", "d.gov"]  # self-citation skipped
    assert by["art.com"]["is_article_domain"] and not by["b.net"]["is_article_domain"]
    assert {by[k]["cluster_id"] for k in ("art.com", "b.net", "c.org")} == {"k1"}
    assert by["d.gov"]["cluster_id"] is None
    assert by["b.net"]["age_days"] == 55 and by["d.gov"]["age_days"] is None
    assert res.has_shared_infra
    assert "art.com and b.net share G-SHARED001" in res.shared_id_reasons()
    assert all(e["type"] == "shares_infra" and e["source"].startswith("d:") for e in res.edges)
    assert len(res.edges) == 2


def test_no_shared_ids_no_cluster():
    res = analyze_infrastructure("a.com", "<p>x</p>", [SourcePage("b.com", None)], None)
    assert not res.has_shared_infra and res.edges == []
    assert all(d["cluster_id"] is None and d["age_days"] is None for d in res.domains)


def test_platforms_get_no_ids_or_age_lookup():
    calls = []
    res = analyze_infrastructure(
        "a.com", "G-SHARED001", [SourcePage("x.com", "G-SHARED001 UA-1234567-1"), SourcePage("t.me")],
        lambda d: calls.append(d) or RdapResult(d, None, None),
    )
    assert "x.com" not in calls and "t.me" not in calls
    assert not res.has_shared_infra


def test_rdap_exception_is_neutral():
    def boom(d):
        raise RuntimeError("x")
    res = analyze_infrastructure("a.com", None, [SourcePage("b.com")], boom)
    assert all(d["age_days"] is None for d in res.domains)


def _client(handler, now="2026-11-14T00:00:00+00:00"):
    return RdapClient(httpx.Client(transport=httpx.MockTransport(handler)),
                      now=lambda: datetime.fromisoformat(now))


def test_rdap_parses_registration_and_caches():
    hits = []

    def handler(req):
        hits.append(str(req.url))
        return httpx.Response(200, json=json.loads((FX / "rdap_ok.json").read_text()))

    c = _client(handler)
    r = c.lookup("news.example.net")  # subdomain -> registrable domain
    assert hits == ["https://rdap.org/domain/example.net"]
    assert r.created_at == "2026-09-20T00:00:00Z" and r.age_days == 55
    c.lookup("example.net")
    assert len(hits) == 1


def test_rdap_failures_are_unknown_not_young():
    for resp in (httpx.Response(404), httpx.Response(429), httpx.Response(200, json={"events": []}),
                 httpx.Response(200, text="not json")):
        r = _client(lambda req, resp=resp: resp).lookup("a.com")
        assert r.age_days is None and r.created_at is None and r.error

    def timeout(req):
        raise httpx.ConnectTimeout("t")
    assert _client(timeout).lookup("a.com").age_days is None


def test_extract_ids_yandex_metrika():
    html = ("<script>ym(97376635, 'init', {});</script><noscript><img src='https://mc.yandex.ru/watch/97376635'>"
            "</noscript><script>new Ya.Metrika({id:52150180});</script>")
    assert extract_ids(html).analytics_ids == ["ym-52150180", "ym-97376635"]
    assert extract_ids("<p>ym(123, x) and item.ym(97376635, 1)</p>").analytics_ids == []


def test_copy_domains_skip_rdap_and_copy_to_copy_shared_ids():
    art = '<script>gtag("config","G-ART00001")</script>'
    c1 = '<script>gtag("config","G-ART00001")</script><script>gtag("config","G-SCRAP002")</script>'
    c2 = '<script>gtag("config","G-SCRAP002")</script>'
    c3 = '<script>gtag("config","G-OTHER003")</script>'
    looked_up = []

    def rdap(dom):
        looked_up.append(dom)
        return RdapResult(dom, "2026-10-01T00:00:00Z", 10)

    res = analyze_infrastructure(
        "guardian.example", art,
        [SourcePage("mirror.example", c1, is_copy=True),
         SourcePage("scraper2.example", c2, is_copy=True),
         SourcePage("young-scraper.example", c3, is_copy=True),
         SourcePage("mirror.edgeone.app", art, is_copy=True)],
        rdap=rdap,
    )
    by = {d["domain"]: d for d in res.domains}
    assert looked_up == ["guardian.example"]                     # no RDAP for copy-only or CDN domains
    assert by["young-scraper.example"]["age_days"] is None
    assert [s.id for s in res.shared_ids] == ["G-ART00001"]      # copy-to-copy G-SCRAP002 dropped; CDN ignored
    assert by["edgeone.app"]["analytics_ids"] == []


def test_sibling_subdomain_copy_keeps_host_and_shares_id_with_article():
    html = "<script>ym(97376635, 'init', {});</script>"
    res = analyze_infrastructure(
        "canada.news-pravda.com", html,
        [SourcePage("usa.news-pravda.com", html, is_copy=True)],
        rdap=None,
    )
    assert [d["domain"] for d in res.domains] == ["news-pravda.com", "usa.news-pravda.com"]
    assert res.shared_id_reasons() == ["news-pravda.com and usa.news-pravda.com share ym-97376635"]
