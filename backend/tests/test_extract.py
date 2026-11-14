import json
from pathlib import Path

import httpx
import pytest

from app.config import Settings
from app.llm import LLMClient, LLMError, parse_json_loose
from datetime import datetime

from app.pipeline.exa import find_candidates, primary_hint, resolve_named_source, suggest_official_domains, suggest_official_search
from app.pipeline.extract import extract_claims

FX = Path(__file__).parent / "fixtures" / "extract"
PRAVDA = (FX / "pravda.md").read_text()
PRAVDA_URL = "https://news-pravda.com/world/2026/11/14/2743940.html"
SETTINGS = Settings(_env_file=None, model_extract="test-model", max_claims=6)


def llm_returning(content, status=200, seen=None):
    def handler(request):
        if seen is not None:
            seen.append(json.loads(request.content))
        if status != 200:
            return httpx.Response(status, text="err")
        return httpx.Response(200, json={
            "model": "test-model", "choices": [{"message": {"content": content}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "credits_charged": 0.01}})
    return LLMClient("https://router.test/v1", "key", retries=0, backoff_s=0,
                     client=httpx.Client(transport=httpx.MockTransport(handler)))


def claims_json(*items):
    return json.dumps({"claims": [{"text": t, "attributions": a} for t, a in items]})


def test_extracts_verbatim_claim_with_link_and_exact_offsets():
    # the model drops the markdown, as it sees the plain view
    text = "This is exactly what filmmaker Andrey Medvedev's film «Betrayal. Expendable Material» is about."
    seen = []
    res = extract_claims(PRAVDA, PRAVDA_URL, llm=llm_returning(claims_json((text, ["Andrey Medvedev"])), seen=seen), settings=SETTINGS)
    (c,) = res.claims
    assert c.id == "c1" and PRAVDA[c.char_start:c.char_end] == c.text
    assert "[«Betrayal. Expendable Material»](https://t.me/smotrim_ru/34791)" in c.text   # verbatim markdown slice
    assert [(l.url, l.scope) for l in c.links] == [("https://t.me/smotrim_ru/34791", "sentence")]
    assert c.attributions == ["Andrey Medvedev"]
    assert res.credits == 0.01 and res.model == "test-model"
    req = seen[0]
    assert req["temperature"] == 0 and req["model"] == "test-model"
    assert "https://t.me" not in req["messages"][1]["content"]       # LLM sees the plain view, not URLs


def test_paraphrased_claim_is_dropped_not_trusted():
    res = extract_claims(PRAVDA, PRAVDA_URL, settings=SETTINGS, llm=llm_returning(claims_json(
        ("A documentary was released that follows several saboteurs and their reasons.", []),
        ("They created a chatbot posing as Ukrainian recruiters and identified everyone willing to carry out a «task».", []),
    )))
    assert len(res.claims) == 1 and len(res.dropped) == 1
    assert res.claims[0].links and res.claims[0].links[0].url == "https://t.me/rybar/75718"


def test_max_claims_and_overlap_dedupe():
    sents = ["A similar method was used in the US after 9/11 to find terrorists through front organizations.",
             "They created a chatbot posing as Ukrainian recruiters and identified everyone willing to carry out a «task».",
             "The film features a girl who directed a Ukrainian missile strike on a ferry in Crimea and on camera reasons that the dead captain «brought it on himself»."]
    s = Settings(_env_file=None, model_extract="m", max_claims=2)
    res = extract_claims(PRAVDA, PRAVDA_URL, settings=s, llm=llm_returning(claims_json(
        (sents[0], []), (sents[0], []), (sents[1], []), (sents[2], []))))
    assert [c.text[:20] for c in res.claims] == [sents[0][:20], sents[1][:20]]   # dup skipped, capped at 2


def test_bad_output_raises_llm_error():
    with pytest.raises(LLMError):
        extract_claims(PRAVDA, PRAVDA_URL, settings=SETTINGS, llm=llm_returning(json.dumps({"nope": 1})))
    with pytest.raises(LLMError):
        extract_claims(PRAVDA, PRAVDA_URL, settings=SETTINGS, llm=llm_returning("not json", 200))
    with pytest.raises(LLMError):
        extract_claims(PRAVDA, PRAVDA_URL, settings=SETTINGS, llm=llm_returning("x", 500))


def test_unconfigured_router_fails_loudly():
    with pytest.raises(LLMError):
        LLMClient("", "").chat_json("m", "s", "u")


def test_parse_json_loose():
    assert parse_json_loose('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json_loose('Sure! {"a": 1} hope that helps') == {"a": 1}
    with pytest.raises(LLMError):
        parse_json_loose("[1, 2]")


def test_exa_resolve_and_failures():
    def ok(request):
        body = json.loads(request.content)
        assert request.headers["x-api-key"] == "k" and "Finnish Ministry" in body["query"]
        return httpx.Response(200, json={"results": [{"url": "https://yle.fi/a/1", "title": "T", "publishedDate": "2026-11-11"}]})
    hit = resolve_named_source("the Finnish Ministry of Defence", "said a drone entered", api_key="k",
                               client=httpx.Client(transport=httpx.MockTransport(ok)))
    assert hit and hit.url == "https://yle.fi/a/1" and hit.attribution == "the Finnish Ministry of Defence"
    for resp in (httpx.Response(500), httpx.Response(200, json={"results": []}), httpx.Response(200, text="<html>")):
        assert resolve_named_source("X", "y", api_key="k",
                                    client=httpx.Client(transport=httpx.MockTransport(lambda r, resp=resp: resp))) is None
    assert resolve_named_source("X", "y", api_key="") is None


def _exa(urls, seen=None):
    def handler(request):
        if seen is not None:
            seen.append(json.loads(request.content))
        return httpx.Response(200, json={"results": [{"url": u, "title": "t"} for u in urls]})
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_primary_hint_ranking():
    assert primary_hint("https://www.amnesty.org/en/latest/x", "Amnesty International") == 2
    assert primary_hint("https://www.defmin.gov.uk/news", "the UK Ministry of Defence") >= 1
    assert primary_hint("https://www.bbc.com/news/x", "Volodymyr Zelensky") == 0


def test_candidates_ranked_first_hand_first_and_exclude_article_and_domain():
    urls = ["https://www.bbc.com/news/a", "https://www.politico.eu/x", "https://news.test/own-article",
            "https://www.amnesty.org/en/report", "https://other.test/y"]
    hits = find_candidates("Amnesty International", "said ...", api_key="k", client=_exa(urls),
                           exclude_url="https://news.test/a", limit=3)
    assert [h.url for h in hits] == ["https://www.amnesty.org/en/report", "https://www.bbc.com/news/a", "https://www.politico.eu/x"]
    assert all("news.test" not in h.url for h in hits)


def test_no_date_window_for_official_site_searches():
    seen = []
    find_candidates("X", "y", api_key="k", client=_exa(["https://a.test/1"], seen), around=datetime(2026, 11, 14),
                    include_domains=["a.test"])
    assert "startPublishedDate" not in seen[0] and seen[0]["includeDomains"] == ["a.test"]


def test_candidates_date_window_and_limit():
    seen = []
    find_candidates("X", "y", api_key="k", client=_exa(["https://a.test/1"], seen), around=datetime(2026, 11, 14))
    assert seen[0]["startPublishedDate"].startswith("2026-10-31") and seen[0]["endPublishedDate"].startswith("2026-11-16")
    seen.clear()
    find_candidates("X", "y", api_key="k", client=_exa(["https://a.test/1"], seen))
    assert "startPublishedDate" not in seen[0]


def test_official_domains_restrict_search_with_short_query_and_rank_first():
    seen = []
    hits = find_candidates("The World Bank", "x" * 400, api_key="k", client=_exa(["https://www.worldbank.org/a"], seen),
                           include_domains=["worldbank.org"])
    assert seen[0]["includeDomains"] == ["worldbank.org"] and len(seen[0]["query"]) <= 160
    assert hits[0].primary_hint == 3


def test_suggest_official_domains_cleans_and_never_raises():
    def llm_with(content):
        return llm_returning(content)
    cases = {json.dumps({"domains": ["https://www.WorldBank.org/en/home", "documents.worldbank.org", "not a domain", "worldbank.org"]}):
             ["worldbank.org", "documents.worldbank.org"],
             json.dumps({"domains": []}): [], json.dumps({"domains": "oops"}): [], json.dumps({"other": 1}): []}
    for content, want in cases.items():
        assert suggest_official_domains("The World Bank", "claim", llm=llm_with(content), model="m") == want
    assert suggest_official_domains("X", "c", llm=llm_returning("x", status=500), model="m") == []


def test_llm_empty_completion_is_retried():
    calls = []

    def handler(request):
        calls.append(1)
        content = "" if len(calls) == 1 else json.dumps({"claims": []})
        return httpx.Response(200, json={"model": "m", "choices": [{"message": {"content": content}}]})
    llm = LLMClient("https://r.test/v1", "k", retries=2, backoff_s=0, client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert llm.chat_json("m", "s", "u").data == {"claims": []} and len(calls) == 2


def test_suggest_official_search_returns_domains_and_clean_keyword_query():
    payload = json.dumps({"domains": ["president.gov.ua"], "query": '  "Starshield   speed restriction" '})
    assert suggest_official_search("Zelenskyy", "claim", llm=llm_returning(payload), model="m") == (
        ["president.gov.ua"], "Starshield speed restriction")
    assert suggest_official_search("X", "c", llm=llm_returning(json.dumps({"domains": [], "query": 5})), model="m") == ([], None)


def test_keyword_query_replaces_sentence_query():
    seen = []
    find_candidates("Zelenskyy", "long sentence " * 30, api_key="k", client=_exa(["https://www.president.gov.ua/x"], seen),
                    include_domains=["president.gov.ua"], query="Starshield speed restriction")
    assert seen[0]["query"] == "Starshield speed restriction"


def test_llm_truncated_completion_is_retried_with_bigger_budget():
    budgets = []

    def handler(request):
        budgets.append(json.loads(request.content)["max_tokens"])
        if len(budgets) < 3:   # live behaviour: reasoning eats the budget -> '```json\n{"' + finish_reason=length
            return httpx.Response(200, json={"model": "m", "choices": [{"finish_reason": "length", "message": {"content": '```json\n{"'}}]})
        return httpx.Response(200, json={"model": "m", "choices": [{"finish_reason": "stop", "message": {"content": '{"a": 1}'}}]})
    llm = LLMClient("https://r.test/v1", "k", retries=0, backoff_s=0, client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert llm.chat_json("m", "s", "u", max_tokens=300).data == {"a": 1}
    assert budgets == [300, 600, 1200]


def test_llm_truncation_never_loops_forever():
    def handler(request):
        return httpx.Response(200, json={"model": "m", "choices": [{"finish_reason": "length", "message": {"content": "{"}}]})
    llm = LLMClient("https://r.test/v1", "k", retries=0, backoff_s=0, client=httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(LLMError, match="truncated"):
        llm.chat_json("m", "s", "u", max_tokens=300)


def test_too_broad_suggested_domains_are_dropped():
    payload = json.dumps({"domains": ["president.gov.ua", "gov.ua", "co.uk"], "query": "q"})
    assert suggest_official_search("Z", "c", llm=llm_returning(payload), model="m")[0] == ["president.gov.ua"]


def _hedge_client(handler, **kw):
    return LLMClient("https://r.test/v1", "k", retries=0, backoff_s=0, hedge_after=0.1,
                     client=httpx.Client(transport=httpx.MockTransport(handler)), **kw)


def test_hedge_second_request_wins_when_first_is_slow():
    import threading
    import time
    n, lock = [0], threading.Lock()

    def handler(request):
        with lock:
            n[0] += 1
            me = n[0]
        if me == 1:
            time.sleep(1.0)                      # the tail: first request hangs
        return httpx.Response(200, json={"model": "m", "choices": [{"finish_reason": "stop", "message": {"content": json.dumps({"who": me})}}]})
    t = time.time()
    assert _hedge_client(handler).chat_json("m", "s", "u").data == {"who": 2}
    assert time.time() - t < 0.7 and n[0] == 2


def test_no_second_request_when_first_answers_quickly():
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(200, json={"model": "m", "choices": [{"finish_reason": "stop", "message": {"content": "{}"}}]})
    _hedge_client(handler).chat_json("m", "s", "u")
    assert len(calls) == 1


def test_hedge_survives_one_failed_request_and_raises_when_both_fail():
    import time
    n = [0]

    def flaky(request):
        n[0] += 1
        if n[0] == 1:
            time.sleep(0.3)
            return httpx.Response(500)           # first one finally errors ...
        return httpx.Response(200, json={"model": "m", "choices": [{"finish_reason": "stop", "message": {"content": '{"ok": 1}'}}]})
    assert _hedge_client(flaky).chat_json("m", "s", "u").data == {"ok": 1}

    def always_slow_500(request):
        time.sleep(0.3)
        return httpx.Response(500)
    with pytest.raises(LLMError):
        _hedge_client(always_slow_500).chat_json("m", "s", "u")
