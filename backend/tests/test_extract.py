import json
from pathlib import Path

import httpx
import pytest

from app.config import Settings
from app.llm import LLMClient, LLMError, parse_json_loose
from datetime import datetime

from app.pipeline.exa import find_candidates, primary_hint, resolve_named_source
from app.pipeline.extract import extract_claims

FX = Path(__file__).parent / "fixtures" / "extract"
PRAVDA = (FX / "pravda.md").read_text()
PRAVDA_URL = "https://news-pravda.com/world/2026/11/14/2743940.html"
SETTINGS = Settings(_env_file=None, model_extract="test-model", max_claims=6, extract_mode="votes")


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


def votes_returning(per_reading, seen=None):
    """A router whose K readings answer differently: reading i returns per_reading[i] (a list of (id, attributions))."""
    def handler(request):
        body = json.loads(request.content)
        user = body["messages"][1]["content"]
        if seen is not None:
            seen.append(user)
        i = int(user.split("Reading ")[1].split(" of")[0]) - 1
        claims = [{"id": cid, "attributions": attrs} for cid, attrs in per_reading[i % len(per_reading)]]
        return httpx.Response(200, json={"model": "test-model", "choices": [{"finish_reason": "stop", "message": {"content": json.dumps({"claims": claims})}}],
                                         "usage": {"prompt_tokens": 10, "completion_tokens": 5, "credits_charged": 0.01}})
    return LLMClient("https://router.test/v1", "key", retries=0, backoff_s=0, client=httpx.Client(transport=httpx.MockTransport(handler)))


MD = ("# Headline of the article about the ministry\n\n"
      "[X](https://x.com/share) [LinkedIn](https://linkedin.com/share) [Reddit](https://reddit.com/share)\n\n"
      "The ministry said on Tuesday that the programme will cover 40 districts by 2027, [the report](https://gov.example/r) states. "
      "Critics doubt that this is realistic given the budget.\n\n"
      "Officials in the region confirmed that 12 schools were closed last week after the storm.\n\n"
      "Many residents say they feel left alone by the authorities in these difficult weeks.\n\n"
      "The prime minister told reporters on Friday that no further cuts are planned this year.")


def unit_ids():
    from app.pipeline.textutil import sentence_units
    return {t[:20]: i for i, (_, _, t) in enumerate(sentence_units(MD))}, sentence_units(MD)


def test_units_are_markdown_sentences_with_exact_offsets_and_share_rows_stay_separate():
    ids, units = unit_ids()
    assert [MD[a:b] == t or "](" in MD[a:b] for a, b, t in units] and all(MD[a:b].strip() for a, b, _ in units)
    assert not any("LinkedIn" in t for _, _, t in units)        # a share-button row is no sentence (and is not glued to the lede)
    lede = next(t for _, _, t in units if t.startswith("The ministry said"))
    assert lede.startswith("The ministry said on Tuesday") and "Reddit" not in lede


def test_majority_vote_selects_claims_in_document_order_with_exact_slices_and_links():
    ids, units = unit_ids()
    a, b, c, d = (ids["The ministry said on"], ids["Critics doubt that t"], ids["Officials in the reg"], ids["The prime minister t"])
    # 5 readings: a x5, c x4, d x3 (majority), b x2 (minority)
    readings = [[(a, ["the ministry"]), (c, []), (d, [])], [(a, ["the ministry"]), (c, []), (d, [])], [(a, []), (c, []), (b, [])],
                [(a, ["the ministry"]), (b, []), (d, [])], [(a, ["the ministry"]), (c, [])]]
    seen = []
    res = extract_claims(MD, "https://news.test/a", llm=votes_returning(readings, seen), settings=SETTINGS)
    assert [x.text[:20] for x in res.claims] == ["The ministry said on", "Officials in the reg", "The prime minister t"]
    assert [x.votes for x in res.claims] == [5, 4, 3] and res.n_votes == 5
    for cl in res.claims:
        assert MD[cl.char_start:cl.char_end] == cl.text and cl.locate_score == 100.0
    first = res.claims[0]
    assert "[the report](https://gov.example/r)" in first.text and [(l.url, l.scope) for l in first.links] == [("https://gov.example/r", "sentence")]
    assert first.attributions == ["the ministry"]                         # the list most readings agree on
    assert len(set(seen)) == 5 and all(u.startswith("Reading ") for u in seen)    # five DIFFERENT requests (router cache cannot merge them)
    assert res.credits == pytest.approx(0.05) and res.model == "test-model"


def test_exactly_half_is_not_a_majority_and_the_cap_keeps_the_first_in_document_order():
    ids, units = unit_ids()
    everything = [(i, []) for i in range(len(units))]
    s = Settings(_env_file=None, model_extract="m", max_claims=2, extract_votes=4, extract_mode="votes")
    res = extract_claims(MD, "", llm=votes_returning([everything, everything, [], []]), settings=s)     # 2 of 4 = half
    assert res.claims == []
    res = extract_claims(MD, "", llm=votes_returning([everything, everything, everything, []]), settings=s)
    assert len(res.claims) == 2 and res.claims[0].char_start < res.claims[1].char_start              # capped, in order


def test_garbage_votes_are_ignored_and_failures_do_not_sink_the_extraction():
    ids, units = unit_ids()
    a = ids["The ministry said on"]
    good = [(a, [])]
    s = Settings(_env_file=None, model_extract="m", extract_votes=3, extract_mode="votes")
    calls = [0]

    def handler(request):
        calls[0] += 1
        if calls[0] == 1:
            return httpx.Response(500)                                                     # one reading fails outright
        claims = [{"id": a, "attributions": []}, {"id": 999}, {"id": "x"}, "nonsense", {"id": -1}]
        return httpx.Response(200, json={"model": "m", "choices": [{"finish_reason": "stop", "message": {"content": json.dumps({"claims": claims})}}]})
    llm = LLMClient("https://r.test/v1", "k", retries=0, backoff_s=0, client=httpx.Client(transport=httpx.MockTransport(handler)))
    res = extract_claims(MD, "", llm=llm, settings=s)
    assert [c.text[:12] for c in res.claims] == ["The ministry"] and res.n_votes == 2          # 2 usable readings, both agree


def test_no_usable_reading_raises_and_an_empty_article_is_not_an_error():
    s = Settings(_env_file=None, model_extract="m", extract_votes=3, extract_mode="votes")
    for llm in (llm_returning(json.dumps({"nope": 1})), llm_returning("not json", 200), llm_returning("x", 500)):
        with pytest.raises(LLMError):
            extract_claims(MD, "", llm=llm, settings=s)
    res = extract_claims("short", "", llm=llm_returning("x", 500), settings=s)
    assert res.claims == []


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
    find_candidates("X", "y", api_key="k", client=_exa(["https://a.test/1"], seen), window=(datetime(2026, 10, 1), datetime(2026, 11, 16)),
                    include_domains=["a.test"])
    assert "startPublishedDate" not in seen[0] and seen[0]["includeDomains"] == ["a.test"]


def test_candidates_date_window_and_limit():
    seen = []
    find_candidates("X", "y", api_key="k", client=_exa(["https://a.test/1"], seen), window=(datetime(2026, 10, 31), datetime(2026, 11, 16)))
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


def test_llm_empty_completion_is_retried():
    calls = []

    def handler(request):
        calls.append(1)
        content = "" if len(calls) == 1 else json.dumps({"claims": []})
        return httpx.Response(200, json={"model": "m", "choices": [{"message": {"content": content}}]})
    llm = LLMClient("https://r.test/v1", "k", retries=2, backoff_s=0, client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert llm.chat_json("m", "s", "u").data == {"claims": []} and len(calls) == 2


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


def test_attributions_need_a_strict_majority_per_name_and_are_order_stable():
    ids, units = unit_ids()
    a = ids["The ministry said on"]
    readings = [[(a, ["the ministry", "Reuters"])], [(a, ["the ministry"])], [(a, ["the ministry", "Reuters"])], [(a, [])], [(a, ["the ministry"])]]
    res = extract_claims(MD, "", llm=votes_returning(readings), settings=SETTINGS.model_copy(update={"extract_votes": 5}))
    assert res.claims[0].attributions == ["the ministry"]                # 4/5 vs Reuters 2/5
    # disagreement about whether the sentence names a source at all: no strict majority -> none
    readings = [[(a, ["the ministry"])], [(a, [])], [(a, ["the ministry"])], [(a, [])]]
    res = extract_claims(MD, "", llm=votes_returning(readings), settings=SETTINGS.model_copy(update={"extract_votes": 4}))
    assert res.claims[0].attributions == []


def test_the_same_person_under_different_titles_is_one_source():
    ids, units = unit_ids()
    a = ids["The ministry said on"]
    readings = [[(a, ["Senator Tim Scott"])], [(a, ["Tim Scott"])], [(a, ["Sen. Tim Scott"])], [(a, ["Tim Scott"])], [(a, [])]]
    res = extract_claims(MD, "", llm=votes_returning(readings), settings=SETTINGS.model_copy(update={"extract_votes": 5}))
    assert res.claims[0].attributions == ["Tim Scott"]            # 4/5 under one key; shown as the spelling most readings used
    readings = [[(a, ["Tim Scott", "Reuters"])], [(a, ["Reuters", "Tim Scott"])], [(a, ["Reuters"])]]
    res = extract_claims(MD, "", llm=votes_returning(readings), settings=SETTINGS.model_copy(update={"extract_votes": 3}))
    assert res.claims[0].attributions == ["Tim Scott", "Reuters"]  # both have a strict majority; first-named order of the first reading


def test_exa_429_is_retried_with_a_pause_and_never_cached(monkeypatch):
    from app.cache import MemoryCache
    from app.pipeline import exa as exa_mod
    monkeypatch.setattr(exa_mod.time, "sleep", lambda s: None)
    n = [0]

    def handler(request):
        n[0] += 1
        if n[0] <= 2:
            return httpx.Response(429, headers={"retry-after": "1"})
        return httpx.Response(200, json={"results": [{"url": "https://www.amnesty.org/r", "title": "t"}]})
    mem = MemoryCache()
    hits = find_candidates("Amnesty International", "said x", api_key="k", client=httpx.Client(transport=httpx.MockTransport(handler)), cache=mem)
    assert [h.url for h in hits] == ["https://www.amnesty.org/r"] and n[0] == 3 and len(mem.data) == 1

    always = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(429)))
    mem2 = MemoryCache()
    assert find_candidates("Amnesty International", "said x", api_key="k", client=always, cache=mem2) == [] and mem2.data == {}     # transient: not frozen
    broken = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    mem3 = MemoryCache()
    find_candidates("Amnesty International", "said x", api_key="k", client=broken, cache=mem3)
    assert len(mem3.data) == 1                                                                                                        # a real error is replayed for 30 min


def test_exa_requests_in_flight_are_capped():
    import threading
    import time
    from concurrent.futures import ThreadPoolExecutor
    from app.pipeline import exa as exa_mod
    active, peak, lock = [0], [0], threading.Lock()

    def handler(request):
        with lock:
            active[0] += 1
            peak[0] = max(peak[0], active[0])
        time.sleep(0.05)
        with lock:
            active[0] -= 1
        return httpx.Response(200, json={"results": []})
    client = httpx.Client(transport=httpx.MockTransport(handler))
    with ThreadPoolExecutor(16) as pool:
        list(pool.map(lambda i: find_candidates("X", f"claim {i}", api_key="k", client=client), range(24)))
    assert 1 < peak[0] <= exa_mod.EXA_CONCURRENCY


def test_prefer_sourced_keeps_reserved_unsourced_slots():
    from app.pipeline.extract import prefer_sourced
    # 9 majority sentences, 1..6 unsourced early, 7,8,9 sourced late -> sourced first, 2 slots reserved for unsourced
    maj = list(range(9))
    out = prefer_sourced(maj, {6, 7, 8}, 6, 2)
    assert len(out) == 6 and out == sorted(out)
    assert {6, 7, 8} <= set(out) and len([i for i in out if i not in {6, 7, 8}]) == 3


def test_prefer_sourced_unchanged_when_under_limit_or_all_unsourced():
    from app.pipeline.extract import prefer_sourced
    assert prefer_sourced([1, 2, 3], set(), 6, 2) == [1, 2, 3]
    assert prefer_sourced(list(range(8)), set(), 6, 2) == [0, 1, 2, 3, 4, 5]
    assert prefer_sourced(list(range(8)), set(range(8)), 6, 2) == [0, 1, 2, 3, 4, 5]
    # many sourced: only the reserved number of unsourced slots, when unsourced exist
    assert prefer_sourced(list(range(10)), {2, 3, 4, 5, 6, 7, 8, 9}, 6, 2) == [0, 1, 2, 3, 4, 5]


def test_no_comment_boilerplate_excluded_from_claims():
    from app.pipeline.textutil import sentence_units

    md = "The ministry confirmed 12 schools closed last week after the heavy storm.\n\nMoscow has not commented on the strike."
    units = sentence_units(md)
    assert len(units) == 2
    res = extract_claims(md, "", llm=votes_returning([[(0, []), (1, ["Moscow"])]]),
                         settings=SETTINGS.model_copy(update={"extract_votes": 1}))
    assert [c.text for c in res.claims] == ["The ministry confirmed 12 schools closed last week after the heavy storm."]


def test_carry_attributions_inherits_speaker_across_continuation_quotes():
    from app.pipeline.textutil import sentence_units

    md = (
        "Kremlin spokesman Dmitry Peskov rejected the accusation on Friday. "
        "\u201cWe will respond immediately,\u201d the statement continued.\n\n"
        "\u201cEvery allegation is baseless,\u201d he told reporters later.\n\n"
        "## Background reaction\n\n"
        "\u201cIndependent observers urged restraint,\u201d no clear speaker here."
    )
    assert len(sentence_units(md)) == 4
    res = extract_claims(
        md, "",
        llm=votes_returning([[(0, ["Dmitry Peskov"]), (1, []), (2, []), (3, [])]]),
        settings=SETTINGS.model_copy(update={"extract_votes": 1}),
    )
    assert [c.attributions for c in res.claims] == [["Dmitry Peskov"], ["Dmitry Peskov"], ["Dmitry Peskov"], []]


def test_prefer_sourced_diversifies_sources_and_skips_near_duplicates():
    from app.pipeline.extract import prefer_sourced
    from app.pipeline.textutil import LinkRef

    majority = [0, 1, 2, 3, 4, 5]
    links = {
        0: [LinkRef("https://reuters.com/story-a", "Reuters", "sentence")],
        1: [LinkRef("https://reuters.com/story-a", "Reuters", "sentence")],
        2: [LinkRef("https://reuters.com/story-b", "Reuters", "sentence")],
        3: [LinkRef("https://bbc.com/story-c", "BBC", "sentence")],
        4: [LinkRef("https://apnews.com/story-d", "AP", "sentence")],
    }
    attrs = {i: [] for i in majority}
    texts = {
        0: "Trade talks resumed in Geneva on Tuesday morning after months of delay.",
        1: "Trade talks resumed in Geneva on Tuesday morning after months of long delay.",
        2: "Negotiators agreed to freeze new agricultural tariffs for twelve months.",
        3: "European ministers welcomed the interim ceasefire on tariff escalation.",
        4: "Dockworkers ended a three-day walkout at Rotterdam container terminals.",
        5: "Local shipping volumes declined by four percent during October.",
    }
    out = prefer_sourced(majority, {0, 1, 2, 3, 4}, limit=4, min_unsourced=1, links=links, attrs=attrs, texts=texts)
    assert out == [0, 2, 3, 5]
