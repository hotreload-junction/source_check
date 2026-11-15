"""Step cache: a finished article must replay byte for byte, even when the models are not deterministic."""
import json
import threading

import httpx
import pytest
from sqlalchemy.exc import OperationalError

import test_run as tr
from app import cache as cachemod
from app import db
from app.cache import DbCache, MemoryCache, NullCache, RefreshCache, make_key
from app.fetch import Fetcher
from app.llm import LLMClient
from app.pipeline.exa import find_candidates
from app.pipeline.run import analyze


def test_make_key_is_stable_and_input_sensitive():
    assert make_key("llm", "m", "sys", "user") == make_key("llm", "m", "sys", "user")
    assert make_key("llm", "m", "sys", "user") != make_key("llm", "m2", "sys", "user")
    assert make_key("llm", "m", "sys", "user") != make_key("llm", "m", "sys", "user2")
    assert make_key({"a": 1, "b": 2}) == make_key({"b": 2, "a": 1})


# ------------------------------------------------------------------------------------------------------- llm
def counting_llm(cache, answers):
    calls = []

    def handler(request):
        calls.append(1)
        body = json.dumps(answers[min(len(calls) - 1, len(answers) - 1)])
        return httpx.Response(200, json={"model": "m", "usage": {"prompt_tokens": 7, "completion_tokens": 3, "credits_charged": 0.5},
                                         "choices": [{"finish_reason": "stop", "message": {"content": body}}]})
    return LLMClient("https://r.test/v1", "k", retries=0, cache=cache, client=httpx.Client(transport=httpx.MockTransport(handler))), calls


def test_llm_replays_identical_requests_from_the_cache_without_a_call_or_cost():
    llm, calls = counting_llm(MemoryCache(), [{"n": 1}, {"n": 2}])      # a NON-deterministic model
    a = llm.chat_json("m", "sys", "user")
    b = llm.chat_json("m", "sys", "user")
    assert a.data == b.data == {"n": 1} and len(calls) == 1
    assert (a.cached, b.cached, b.credits, a.credits) == (False, True, 0.0, 0.5) and b.prompt_tokens == 7
    assert llm.chat_json("m", "sys", "other user").data == {"n": 2} and len(calls) == 2     # another input is a miss


def test_llm_errors_are_never_cached():
    mem, n = MemoryCache(), [0]

    def handler(request):
        n[0] += 1
        return httpx.Response(500) if n[0] == 1 else httpx.Response(200, json={"model": "m", "choices": [{"finish_reason": "stop", "message": {"content": "{}"}}]})
    llm = LLMClient("https://r.test/v1", "k", retries=0, cache=mem, client=httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(Exception):
        llm.chat_json("m", "s", "u")
    assert mem.data == {} and llm.chat_json("m", "s", "u").data == {}


def test_refresh_cache_recomputes_but_stores_the_new_answer():
    mem = MemoryCache()
    llm, calls = counting_llm(mem, [{"n": 1}, {"n": 2}])
    llm.chat_json("m", "s", "u")
    refreshed = LLMClient(llm.base_url, "k", retries=0, cache=RefreshCache(mem), client=llm.client)
    assert refreshed.chat_json("m", "s", "u").data == {"n": 2} and len(calls) == 2
    assert llm.chat_json("m", "s", "u").data == {"n": 2} and len(calls) == 2         # the new answer is now the frozen one


def test_default_cache_is_used_when_none_is_injected_and_null_cache_changes_nothing():
    mem = MemoryCache()
    cachemod.set_default(mem)
    llm, calls = counting_llm(None, [{"n": 1}, {"n": 2}])
    llm.chat_json("m", "s", "u"); llm.chat_json("m", "s", "u")
    assert len(calls) == 1 and len(mem.data) == 1
    cachemod.set_default(NullCache())
    llm.chat_json("m", "s", "u"); llm.chat_json("m", "s", "u")
    assert len(calls) == 3


# ------------------------------------------------------------------------------------------------------- db cache
def test_db_cache_roundtrip_overwrite_and_ttl(sqlite_db):
    c = DbCache()
    assert c.get("llm", "k1") is None
    c.put("llm", "k1", {"data": {"ru": "Привет 🌍"}})
    assert c.get("llm", "k1") == {"data": {"ru": "Привет 🌍"}}
    c.put("llm", "k1", {"data": 2})
    assert c.get("llm", "k1") == {"data": 2} and c.get("fetch", "k1") is None      # kinds are separate
    from datetime import timedelta
    from sqlalchemy import update
    from app.models import StepCacheRecord, utcnow
    with db.session_scope() as s:
        s.execute(update(StepCacheRecord).values(created_at=utcnow() - timedelta(hours=30)))
    assert DbCache(ttl_hours=24).get("llm", "k1") is None and DbCache(ttl_hours=48).get("llm", "k1") == {"data": 2}


def test_db_outage_trips_a_breaker_and_never_raises(monkeypatch):
    calls = []

    def boom():
        calls.append(1)
        raise OperationalError("select", {}, Exception("down"))

    class Broken:
        def __enter__(self):
            boom()

        def __exit__(self, *a):
            return False
    monkeypatch.setattr(db, "session_scope", lambda: Broken())
    c = DbCache(cooldown_s=60)
    assert c.get("llm", "k") is None
    c.put("llm", "k", {"x": 1})                  # no exception
    assert c.get("llm", "k") is None and len(calls) == 1      # breaker open: the database is not asked again


# ------------------------------------------------------------------------------------------------------- fetch
def fetcher_with(mem, handler):
    return Fetcher(context_dev_key="", cache=mem, backoff_s=0, client=httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True))


def test_pages_are_cached_by_normalised_url_with_html_and_date_intact():
    gets = []
    page = ("<html><head><meta property=\"article:published_time\" content=\"2026-11-12T08:00:00Z\"><title>T</title></head><body><article><p>"
            + "The ministry said on Tuesday the programme expands coverage to forty districts by 2027. " * 6 + "</p></article></body></html>")

    def handler(request):
        gets.append(str(request.url))
        return httpx.Response(200, text=page, headers={"content-type": "text/html"})
    mem = MemoryCache()
    f = fetcher_with(mem, handler)
    a = f.fetch_article("https://www.example.com/story/?utm_source=x#frag")
    n = len(gets)
    b = f.fetch_article("https://example.com/story")
    assert len(gets) == n and b.text == a.text and b.html == a.html and b.published_at == "2026-11-12T08:00:00Z" and b.status == "ok"
    refreshed = Fetcher(cache=RefreshCache(mem), client=httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True))
    refreshed.fetch_article("https://example.com/story")
    assert len(gets) > n                                                           # refresh really re-fetches


def test_failures_replay_identically_for_a_while_and_are_retried_after_the_ttl():
    mem, n = MemoryCache(), [0]

    def handler(request):
        n[0] += 1
        return httpx.Response(404) if n[0] == 1 else httpx.Response(200, text="<html><body>" + "A real page with enough words to count as text. " * 20 + "</body></html>", headers={"content-type": "text/html"})
    f = fetcher_with(mem, handler)
    assert f.fetch_source("https://dead.example/x", "https://news.test/a").status == "unreachable"
    again = f.fetch_source("https://dead.example/x", "https://news.test/a")
    assert again.status == "unreachable" and n[0] == 1                      # same failure, no second request
    for key in mem.expires:
        mem.expires[key] = 0                                                # the failure TTL runs out ...
    assert f.fetch_source("https://dead.example/x", "https://news.test/a").status == "ok"   # ... and the retry succeeds


def test_missing_html_is_remembered_too_and_a_timeout_free_replay_is_fast():
    gets = []

    def handler(request):
        gets.append(1)
        return httpx.Response(403)
    mem = MemoryCache()
    f = fetcher_with(mem, handler)
    assert f.fetch_html("https://blocked.example/x") is None and f.fetch_html("https://blocked.example/x") is None
    assert len(gets) == 1


def test_per_entry_ttl_in_the_database_cache(sqlite_db):
    c = DbCache()
    c.put("fetch", "k", {"a": 1}, ttl_s=3600)
    assert c.get("fetch", "k") == {"a": 1}
    c.put("fetch", "k", {"a": 1}, ttl_s=-1)
    assert c.get("fetch", "k") is None
    c.put("fetch", "plain", {"a": 2})
    assert c.get("fetch", "plain") == {"a": 2}


def test_source_text_and_html_are_cached_separately():
    gets = []

    def handler(request):
        gets.append(1)
        return httpx.Response(200, text=open(tr.__file__.replace("test_run.py", "fixtures/fetch/article.html")).read(),
                              headers={"content-type": "text/html"})
    mem = MemoryCache()
    f = fetcher_with(mem, handler)
    f.fetch_source("https://example.org/report", "https://news.test/a", with_html=False)
    f.fetch_html("https://example.org/report")
    g = len(gets)
    r = f.fetch_source("https://example.org/report", "https://news.test/a")            # text + html both from the cache
    assert len(gets) == g and r.status == "ok" and "G-ABC123" in r.html
    assert {k for k, _ in mem.data} == {"fetch", "html"}


# ------------------------------------------------------------------------------------------------------- exa
def test_exa_results_are_frozen_and_filters_still_apply_after_the_cache():
    n = [0]

    def handler(request):
        n[0] += 1
        return httpx.Response(200, json={"results": [{"url": "https://www.amnesty.org/r", "title": "t", "publishedDate": "2026-11-10", "text": "ignored"},
                                                     {"url": "https://news.test/own", "title": "own"}]})
    mem = MemoryCache()
    client = httpx.Client(transport=httpx.MockTransport(handler))
    kw = dict(api_key="k", client=client, cache=mem, exclude_url="https://news.test/a")
    a = find_candidates("Amnesty International", "said x", **kw)
    b = find_candidates("Amnesty International", "said x", **kw)
    assert n[0] == 1 and [h.url for h in a] == [h.url for h in b] == ["https://www.amnesty.org/r"]    # own domain still excluded
    assert all(set(v) <= {"url", "title", "publishedDate"} for v in mem.data[("exa", next(k for t, k in mem.data))])


# ------------------------------------------------------------------------------------------------------- the point
SETTINGS_VOTES = tr.SETTINGS.extract_votes


def wobbly_router(counter):
    """A model that is NOT deterministic: every fresh extraction picks a different claim subset."""
    subsets = [tr.EXTRACT, tr.EXTRACT[:2], tr.EXTRACT[1:], [tr.EXTRACT[0], tr.EXTRACT[2]]]

    def handler(request):
        body = json.loads(request.content)
        sysmsg, user = body["messages"][0]["content"], body["messages"][1]["content"]
        if "CHECKABLE FACTUAL CLAIMS" in sysmsg:
            with counter["lock"]:
                counter["extract"] += 1
                pick = subsets[(counter["extract"] // SETTINGS_VOTES) % len(subsets)]     # every K readings: another subset
            content = json.dumps({"claims": tr.ids_for(user, pick)})
        elif "check whether a SOURCE" in sysmsg:
            content = json.dumps(tr.verifier(user))
        else:
            content = json.dumps({"domains": [], "query": ""})
        return httpx.Response(200, json={"model": "m", "choices": [{"finish_reason": "stop", "message": {"content": content}}]})
    return handler


def run_once(mem, counter, refresh=False):
    sc = RefreshCache(mem) if refresh else mem
    pages = {"https://news-front.net/report-123": tr.src("https://news-front.net/report-123", tr.SRC_BAD, '<script>ga("G-SAME0001")</script>'),
             "https://ministry.gov.uk/report": tr.src("https://ministry.gov.uk/report", tr.SRC_OK, "<html></html>")}
    llm = LLMClient("https://r.test/v1", "k", retries=0, cache=sc, client=httpx.Client(transport=httpx.MockTransport(wobbly_router(counter))))
    out = analyze(tr.ARTICLE_URL, fetcher=tr.FakeFetcher(tr.art(), pages), llm=llm, settings=tr.SETTINGS,
                  rdap=tr.FakeRdap({"news-front.net": 55}), refresh=refresh)
    r = json.loads(json.dumps(out.result))
    for k in ("duration_s", "cost_usd", "llm_calls"):       # the replay really is cheaper: nothing is asked again
        r["audit"].pop(k)
    return r


def test_a_nondeterministic_model_gives_the_same_result_the_second_time_but_not_after_refresh():
    counter = {"extract": 0, "lock": threading.Lock()}
    mem = MemoryCache()
    first = run_once(mem, counter)
    second = run_once(mem, counter)
    assert counter["extract"] == SETTINGS_VOTES                # the K readings happened once; the replay never asked again
    assert second == first                                      # claims, sources, graph, signals, verdict: all identical
    uncached = run_once(MemoryCache(), counter)                 # what happens WITHOUT the cache: a different selection
    assert counter["extract"] == 2 * SETTINGS_VOTES and uncached["claims"] != first["claims"]
    run_once(mem, counter, refresh=True)
    assert counter["extract"] == 3 * SETTINGS_VOTES            # refresh recomputes
    assert run_once(mem, counter) != first                      # ... and its answer is now the frozen one


def test_force_in_the_api_means_refresh(client):
    jobs = client.app.state.jobs
    seen = []
    jobs.analyze_fn = lambda url, settings=None, emit=None, refresh=None, **k: (seen.append(refresh), (_ for _ in ()).throw(RuntimeError("stop")))[1]
    for force in (False, True):
        client.post("/api/analyses", json={"url": f"https://a{int(force)}.example/x", "force": force})
    import time
    end = time.time() + 3
    while len(seen) < 2 and time.time() < end:
        time.sleep(0.02)
    assert sorted(seen) == [False, True]


def test_rdap_answers_with_a_date_are_cached_failures_are_not():
    from app.pipeline.run import _CachedRdap
    mem = MemoryCache()
    inner = tr.FakeRdap({"known.com": 55})
    c = _CachedRdap(inner, mem)
    a, b = c.lookup("known.com"), c.lookup("www.known.com")
    assert a == b and a.age_days == 55 and inner.looked == ["known.com"]           # second lookup never reached the registry
    c.lookup("unknown.com"); c.lookup("unknown.com")
    assert inner.looked.count("unknown.com") == 2                                  # no date -> not frozen
