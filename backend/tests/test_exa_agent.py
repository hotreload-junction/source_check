import json

import httpx

from app import cache as cachemod
from app.pipeline import exa_agent
from app.pipeline.exa_agent import build_body, find_sources

ART = "https://news.test/a"


def _client(runs, created):
    def handler(request):
        if request.method == "POST":
            created.append(json.loads(request.content))
            r = runs.pop("post", None)
            return r if r is not None else httpx.Response(200, json={"id": "run1"})
        return httpx.Response(200, json=runs["get"])
    return httpx.Client(transport=httpx.MockTransport(handler))


def _done(sources, grounding=()):
    return {"status": "completed", "costDollars": {"total": 0.025},
            "output": {"structured": {"found": bool(sources), "sources": sources},
                       "grounding": [{"citations": [{"url": u} for u in grounding]}]}}


def test_build_body_has_schema_and_excludes_article_domain(monkeypatch):
    b = build_body("Claim text.", "para", ART, "news.test", None, effort="low", attributed=False)
    assert b["effort"] == "low" and "news.test" in b["query"] and b["outputSchema"]["required"] == ["found", "sources"]


def test_find_sources_filters_same_domain_and_caches(monkeypatch):
    monkeypatch.setattr(exa_agent, "POLL_INTERVAL_S", 0)
    created = []
    runs = {"get": _done([{"url": "https://orig.test/p", "kind": "original", "quote": "q"},
                          {"url": "https://news.test/other", "kind": "original", "quote": None},
                          {"url": "ftp://x.test/y", "kind": "original"}], grounding=["https://c.test/z"])}
    cache = cachemod.MemoryCache()
    body = build_body("C.", "p", ART, "news.test", None, effort="low", attributed=False)
    r = find_sources(body, api_key="k", article_url=ART, client=_client(runs, created), cache=cache)
    assert [h.url for h in r.hits] == ["https://orig.test/p", "https://c.test/z"] and r.hits[0].kind == "original"
    assert r.cost == 0.025 and r.status == "completed" and not r.cached
    again = find_sources(body, api_key="k", article_url=ART, client=_client({}, created), cache=cache)   # no network call
    assert again.cached and [h.url for h in again.hits] == [h.url for h in r.hits] and len(created) == 1


def test_find_sources_failures_are_neutral(monkeypatch):
    monkeypatch.setattr(exa_agent, "POLL_INTERVAL_S", 0)
    body = build_body("C.", "p", ART, "news.test", None, effort="low", attributed=False)
    assert find_sources(body, api_key="", article_url=ART).status == "disabled"
    failed = find_sources(body, api_key="k", article_url=ART, cache=cachemod.MemoryCache(),
                          client=_client({"get": {"status": "failed"}}, []))
    assert failed.hits == [] and failed.status == "failed"
    down = find_sources(body, api_key="k", article_url=ART, cache=cachemod.MemoryCache(),
                        client=_client({"post": httpx.Response(500)}, []))
    assert down.hits == [] and down.status == "unavailable"


def test_find_sources_timeout_is_not_cached(monkeypatch):
    monkeypatch.setattr(exa_agent, "POLL_INTERVAL_S", 0)
    cache = cachemod.MemoryCache()
    body = build_body("C.", "p", ART, "news.test", None, effort="low", attributed=False)
    r = find_sources(body, api_key="k", article_url=ART, timeout_s=0.05, cache=cache,
                     client=_client({"get": {"status": "running"}}, []))
    assert r.status == "timeout" and cache.get("agent", cachemod.make_key("agent", body)) is None
