"""Evidence search for attributed claims: plan -> parallel search -> rank -> verify -> depth-2 sources."""
import json
from datetime import date, datetime

import httpx
import pytest

import test_run as tr
from app.config import Settings
from app.fetch import FetchResult
from app.llm import LLMClient
from app.pipeline.evidence import (PLAN_PROMPT, SearchPlan, gather_candidates, in_window, is_venue_domain, parse_plan,
                                   plan_search, search_window)
from app.pipeline.run import analyze

ARTICLE_DATE = datetime(2026, 11, 13)
CLAIM = "The Nvidia founder repeatedly warned that cutting Beijing off from American technology would accelerate China's push for independence."
CONTEXT = (CLAIM + " In a May interview with Fox Business, he pointed to Huawei's growing influence: “They’re flourishing in our absence.”")
PLAN_JSON = {"speaker": "Jensen Huang", "venue": "Fox Business", "medium": "Interview", "date_from": "2026-05-01", "date_to": "2026-05",
             "domains": ["https://www.FoxBusiness.com/x", "gov.ua", "not a domain"], "queries": ["Jensen Huang Fox Business interview May 2026 Huawei", "x"],
             "phrases": ["They’re flourishing in our absence", "invented words not in text", "single"]}


# ------------------------------------------------------------------------------------------------------- the plan
def test_parse_plan_validates_everything_the_model_says():
    p = parse_plan(PLAN_JSON, CONTEXT)
    assert (p.speaker, p.venue, p.medium) == ("Jensen Huang", "Fox Business", "interview")
    assert p.date_from == date(2026, 5, 1) and p.date_to == date(2026, 5, 31)          # a bare month ends on its last day
    assert p.domains == ["foxbusiness.com"]                                              # cleaned; the registry "gov.ua" and junk dropped
    assert p.queries == ["Jensen Huang Fox Business interview May 2026 Huawei"]          # "x" is too short to be a search
    assert p.phrases == ["flourishing in our absence"]                                   # invented / 1-word phrases never reach Exa; the
    #                                                                                     apostrophe word is cut off (sites spell it differently)


def test_parse_plan_survives_garbage():
    for data in ({}, {"phrases": "oops", "domains": 5, "queries": None, "date_from": "soon", "speaker": 7},
                 {"date_from": "2026-06-01", "date_to": "2026-05-01"}, {"speaker": "null", "venue": "null"}):
        p = parse_plan(data, CONTEXT)
        assert p.phrases == [] and p.domains == [] and p.date_from is None and p.venue is None
    assert parse_plan({"phrases": ["a b c d e f"]}, "a b c d e f").phrases == []          # more than 5 words: not an includeText phrase


def test_occasion_text_for_the_verify_prompt():
    p = parse_plan(PLAN_JSON, CONTEXT)
    assert p.occasion == "interview at/with Fox Business between 2026-05-01 and 2026-05-31"
    assert SearchPlan().occasion is None and SearchPlan(medium="other").occasion is None


def llm_for(content, seen=None, status=200):
    def handler(request):
        if seen is not None:
            seen.append(json.loads(request.content))
        if status != 200:
            return httpx.Response(status)
        return httpx.Response(200, json={"model": "m", "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(content)}}]})
    return LLMClient("https://r.test/v1", "k", retries=0, backoff_s=0, client=httpx.Client(transport=httpx.MockTransport(handler)))


def test_plan_search_sends_claim_paragraph_and_article_date():
    seen = []
    plan = plan_search("The Nvidia founder", CLAIM, CONTEXT, ARTICLE_DATE, llm=llm_for(PLAN_JSON, seen), model="m")
    assert plan.speaker == "Jensen Huang" and plan.venue == "Fox Business"
    user = seen[0]["messages"][1]["content"]
    assert "ARTICLE DATE: 2026-11-13" in user and "In a May interview with Fox Business" in user and "ATTRIBUTED TO: The Nvidia founder" in user
    assert seen[0]["messages"][0]["content"] == PLAN_PROMPT and seen[0]["temperature"] == 0


def test_plan_search_falls_back_to_the_old_query_when_the_model_fails_or_says_nothing():
    plan = plan_search("Jensen Huang", CLAIM, CONTEXT, ARTICLE_DATE, llm=llm_for({}, status=500), model="m")
    assert plan.speaker == "Jensen Huang" and plan.queries[0].startswith("Jensen Huang The Nvidia founder") and plan.domains == []
    plan = plan_search("Jensen Huang", CLAIM, CONTEXT, ARTICLE_DATE, llm=llm_for({"venue": "Fox Business"}), model="m")
    assert plan.venue == "Fox Business" and plan.queries and plan.speaker == "Jensen Huang"


# ------------------------------------------------------------------------------------------------------- venue / window
def test_venue_is_recognised_by_domain_even_when_the_model_guessed_a_wrong_domain():
    plan = SearchPlan(venue="Fox Business", domains=["fox.com"])
    assert is_venue_domain("https://www.foxbusiness.com/technology/x", plan)          # the domain NAME is the venue
    assert is_venue_domain("https://www.fox.com/watch/clip/1", plan)                  # a planned domain
    assert not is_venue_domain("https://www.cnbc.com/2026/05/21/x", plan)
    assert not is_venue_domain("https://www.un.org/x", SearchPlan(venue="UN"))        # too short to be a name match
    assert not is_venue_domain("https://x.com/y", SearchPlan())


def test_window_follows_the_named_occasion_else_the_default_lookback():
    may = parse_plan(PLAN_JSON, CONTEXT)
    start, end = search_window(may, ARTICLE_DATE)
    assert start == datetime(2026, 4, 17) and end == datetime(2026, 6, 14)            # May +/- 14 days, NOT 60 days before November
    start, end = search_window(SearchPlan(), ARTICLE_DATE)
    assert (ARTICLE_DATE - start).days == 180 and (end - ARTICLE_DATE).days == 2
    assert search_window(SearchPlan(), None) is None


def test_in_window_only_rejects_pages_clearly_outside():
    w = (datetime(2026, 4, 17), datetime(2026, 6, 14))
    assert in_window("2026-05-21T00:00:00.000Z", w) and in_window(None, w) and in_window("garbage", w) and in_window("2026-05-21", None)
    assert not in_window("2025-05-21T00:00:00.000Z", w) and not in_window("2026-11-12T00:00:00.000Z", w)


# ------------------------------------------------------------------------------------------------------- the search
FOX = "https://www.foxbusiness.com/technology/nvidia-ceo-jensen-huang-warns-china-has-all-chips"
CNBC = "https://www.cnbc.com/2026/05/21/nvidia-jensen-huang-china.html"
QUOTE_COPY = "https://www.bizinsider.org/nvidia-ceo-jensen-huang-warns-china"
OLD_OUTLET = "https://www.wsj.com/world/asia/nvidias-huang-calls-export-controls-a-failure"
OWN = "https://news.example/own-article"


def exa_router(seen):
    """A fake Exa: what comes back depends on HOW we ask, like the real one measured in the live experiment."""
    def handler(request):
        body = json.loads(request.content)
        seen.append(body)
        if body.get("includeText"):
            results = [FOX, QUOTE_COPY, OWN]
        elif body.get("includeDomains"):
            results = [FOX]
        else:
            results = [CNBC, FOX, OLD_OUTLET]
        dates = {FOX: "2026-05-21", CNBC: "2026-05-21", QUOTE_COPY: "2026-05-22", OLD_OUTLET: "2025-05-21", OWN: "2026-11-13"}
        return httpx.Response(200, json={"results": [{"url": u, "title": "t", "publishedDate": dates[u]} for u in results]})
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_three_searches_run_and_the_venue_page_comes_first():
    seen = []
    plan = parse_plan(PLAN_JSON, CONTEXT)
    cands = gather_candidates(plan, CLAIM, "https://news.example/a", ARTICLE_DATE, api_key="k", exa_client=exa_router(seen))
    kinds = ["text" if b.get("includeText") else "domain" if b.get("includeDomains") else "query" for b in seen]
    assert sorted(kinds) == ["domain", "query", "text"]
    by_kind = {k: b for k, b in zip(kinds, seen)}
    assert by_kind["query"]["startPublishedDate"].startswith("2026-04-17") and by_kind["query"]["endPublishedDate"].startswith("2026-06-14")
    assert "startPublishedDate" not in by_kind["domain"] and by_kind["domain"]["includeDomains"] == ["foxbusiness.com"]
    assert by_kind["text"]["includeText"] == ["flourishing in our absence"] and "Jensen Huang Fox Business" in by_kind["text"]["query"]
    urls = [c.hit.url for c in cands]
    assert urls[0] == FOX and cands[0].venue_hosted and cands[0].phrase_hit          # the page every search agrees on
    assert urls[1] == QUOTE_COPY and cands[1].phrase_hit and not cands[1].venue_hosted    # exact quote, other outlet
    assert OWN not in urls                                                              # the article's own domain is never evidence
    assert OLD_OUTLET not in urls and CNBC not in urls                                  # plain outlets: outside window / not worth a verify call


def test_plain_outlets_are_verified_only_when_asked_for_and_only_inside_the_window():
    plan = parse_plan(PLAN_JSON, CONTEXT)
    cands = gather_candidates(plan, CLAIM, "https://news.example/a", ARTICLE_DATE, api_key="k", exa_client=exa_router([]), include_secondary=True)
    urls = [c.hit.url for c in cands]
    assert CNBC in urls and OLD_OUTLET not in urls                                      # May 2026 fits, May 2025 does not


def test_no_plan_no_search_and_a_dead_exa_gives_nothing():
    assert gather_candidates(SearchPlan(), CLAIM, "https://news.example/a", ARTICLE_DATE, api_key="k", exa_client=exa_router([])) == []
    dead = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    assert gather_candidates(parse_plan(PLAN_JSON, CONTEXT), CLAIM, "https://news.example/a", ARTICLE_DATE, api_key="k", exa_client=dead) == []


# ------------------------------------------------------------------------------------------------------- end to end
ARTICLE_MD = ("# Why Huang left\n\nHuang's resignation removes one of Washington's most prominent advocates of keeping China connected to American technology.\n\n"
              + CONTEXT)
FOX_PAGE = ("Nvidia CEO Jensen Huang warned in a Fox Business interview this week that cutting Beijing off from American technology would "
            "accelerate China's push for independence. Huang also said: \"They’re flourishing in our absence.\" " * 3)
SETTINGS = Settings(_env_file=None, model_extract="mx", model_verify="mv", exa_api_key="exa", prompt_version="vT", extract_votes=1,
                    claim_deadline_s=30, fuzzy_threshold=90)


def pipeline_router(found_type="secondary", verdict="supported", plan_json=PLAN_JSON, verify_seen=None):
    def handler(request):
        body = json.loads(request.content)
        sysmsg, user = body["messages"][0]["content"], body["messages"][1]["content"]
        if "CHECKABLE FACTUAL CLAIMS" in sysmsg:
            content = {"claims": tr.ids_for(user, [{"text": "The Nvidia founder repeatedly warned", "attributions": ["The Nvidia founder"]}])}
        elif "Plan how to find" in sysmsg:
            content = plan_json
        else:
            if verify_seen is not None:
                verify_seen.append(user)
            quote = "cutting Beijing off from American technology would accelerate China's push for independence"
            content = {"quote_original": quote if verdict != "not_mentioned" else "", "quote_translation": None, "language": "en",
                       "verdict": verdict, "explanation": "The page reports it.", "source_type": found_type}
        return httpx.Response(200, json={"model": "m", "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(content)}}]})
    return LLMClient("https://r.test/v1", "k", retries=0, backoff_s=0, client=httpx.Client(transport=httpx.MockTransport(handler)))


def article_result():
    return FetchResult("https://news.example/a", "link", "ok", via="context_dev", text=ARTICLE_MD, html="<html></html>", title="Why Huang left",
                       final_url="https://news.example/a", published_at="2026-11-13T09:00:00Z")


def run(settings=SETTINGS, **router_kw):
    pages = {u: FetchResult(u, "link", "ok", via="context_dev", text=FOX_PAGE, html="<html><script>ga('G-FOUND0001')</script></html>")
             for u in (FOX, QUOTE_COPY, CNBC)}
    return analyze("https://news.example/a", fetcher=tr.FakeFetcher(article_result(), pages), llm=pipeline_router(**router_kw),
                   settings=settings, rdap=tr.FakeRdap({}), exa_client=exa_router([]))


def test_the_may_interview_is_found_verified_and_hung_below_the_named_person():
    verify_seen = []
    out = run(found_type="secondary", verify_seen=verify_seen)        # the model calls Fox's own report "secondary": the venue wins
    r = out.result
    (claim,) = r["claims"]
    assert claim["verdict"] == "supported" and claim["evidence"]["quote_original"].startswith("cutting Beijing off")
    stub = next(s for s in r["sources"] if s["url"] is None)
    found = next(s for s in r["sources"] if s["url"] == FOX)
    assert (stub["kind"], stub["depth"], stub["attribution_text"]) == ("named", 1, "Jensen Huang")       # resolved from the paragraph by the plan
    assert (found["kind"], found["depth"], found["parent_source_id"], found["domain"]) == ("named", 2, stub["id"], "foxbusiness.com")
    assert claim["source_ids"][0] == stub["id"] and found["id"] in claim["source_ids"]
    assert claim["evidence"]["source_id"] == found["id"]
    edges = {(e["source"], e["target"], e["type"]) for e in r["graph"]["edges"]}
    assert ("c1", stub["id"], "cites") in edges and ("c1", found["id"], "cites") in edges and (stub["id"], found["id"], "cites") in edges
    assert not any(e["source"] == found["id"] and e["type"] == "hosted_on" for e in r["graph"]["edges"])     # found pages are no cited source
    assert "foxbusiness.com" not in {d["domain"] for d in r["domains"]}                                       # ... and not in the infrastructure analysis
    assert all(e["source"] in {n["id"] for n in r["graph"]["nodes"]} and e["target"] in {n["id"] for n in r["graph"]["nodes"]} for e in r["graph"]["edges"])
    # the verify prompt carried the occasion and the venue, so a May page is judged against "a May interview", not the November date
    assert any("OCCASION: interview at/with Fox Business between 2026-05-01 and 2026-05-31" in u and "NAMED VENUE: Fox Business" in u
               and "ARTICLE DATE: 2026-11-13" in u for u in verify_seen)


def test_independent_report_of_the_quote_is_linked_but_stays_neutral_unless_switched_on():
    only_copy = Settings(**{**SETTINGS.model_dump(), "max_evidence_candidates": 5})
    pages_router = pipeline_router(found_type="secondary")
    # drop the venue page from the search results: only another outlet with the exact quote remains
    def exa_without_fox():
        def handler(request):
            return httpx.Response(200, json={"results": [{"url": QUOTE_COPY, "title": "t", "publishedDate": "2026-05-22"}]})
        return httpx.Client(transport=httpx.MockTransport(handler))
    pages = {QUOTE_COPY: FetchResult(QUOTE_COPY, "link", "ok", via="context_dev", text=FOX_PAGE, html="<html></html>")}
    out = analyze("https://news.example/a", fetcher=tr.FakeFetcher(article_result(), pages), llm=pages_router, settings=only_copy,
                  rdap=tr.FakeRdap({}), exa_client=exa_without_fox())
    (claim,) = out.result["claims"]
    assert claim["verdict"] == "not_checkable" and claim["evidence"] is None and "Independent reporting confirms the quote" in claim["explanation"]
    linked = [s for s in out.result["sources"] if s["url"] == QUOTE_COPY]
    assert linked and linked[0]["depth"] == 2 and linked[0]["id"] in claim["source_ids"]            # shown below the person anyway
    counting = Settings(**{**SETTINGS.model_dump(), "corroboration_counts": True})
    out = analyze("https://news.example/a", fetcher=tr.FakeFetcher(article_result(), pages), llm=pipeline_router(found_type="secondary"), settings=counting,
                  rdap=tr.FakeRdap({}), exa_client=exa_without_fox())
    assert out.result["claims"][0]["verdict"] == "supported"


def test_nothing_found_says_quote_could_not_be_verified_and_keeps_only_the_stub():
    empty = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"results": []})))
    out = analyze("https://news.example/a", fetcher=tr.FakeFetcher(article_result(), {}), llm=pipeline_router(), settings=SETTINGS,
                  rdap=tr.FakeRdap({}), exa_client=empty)
    r = out.result
    (claim,) = r["claims"]
    assert claim["verdict"] == "not_checkable" and claim["explanation"].startswith("Quote could not be verified")
    assert [(s["kind"], s["depth"], s["url"]) for s in r["sources"]] == [("named", 1, None)] and claim["evidence"] is None
    assert claim["source_ids"] == [r["sources"][0]["id"]]


def test_a_found_page_that_does_not_contain_the_quote_is_never_evidence_against_the_article():
    out = run(verdict="not_mentioned", found_type="original")
    (claim,) = out.result["claims"]
    assert claim["verdict"] == "not_checkable" and "does not contain this quote" in claim["explanation"]
    assert [s["depth"] for s in out.result["sources"]] == [1]                       # a page without the quote is not linked at all


def test_exa_phrase_keeps_only_what_exa_can_match_exactly():
    from app.pipeline.evidence import exa_phrase
    assert exa_phrase("They’re flourishing in our absence") == "flourishing in our absence"
    assert exa_phrase("It's a very big deal, truly") == "a very big deal"                # longest plain run, never across a comma
    assert exa_phrase("one two three four five six seven") == "one two three four five"
    assert exa_phrase("Don't") is None and exa_phrase("a, b, c") is None


def test_a_page_with_the_exact_quote_outranks_many_pages_of_the_venue_without_it():
    from app.pipeline.evidence import Candidate
    from app.pipeline.exa import NamedSourceHit
    h = lambda u, hint=0: NamedSourceHit(u, "t", None, "X", hint)
    venue_only = [Candidate(h(f"https://www.president.gov.ua/en/news/old-{i}", 3), venue_hosted=True, order=i) for i in range(7)]
    quote_elsewhere = Candidate(h("https://www.bbc.com/news/articles/x"), phrase_hit=True, order=9)
    quote_on_venue = Candidate(h("https://www.president.gov.ua/en/news/the-statement", 3), venue_hosted=True, phrase_hit=True, order=8)
    ranked = sorted(venue_only + [quote_elsewhere, quote_on_venue], key=lambda c: c.score)
    assert [c.hit.url for c in ranked[:2]] == ["https://www.president.gov.ua/en/news/the-statement", "https://www.bbc.com/news/articles/x"]
    assert [c.hit.url for c in ranked[2:4]] == [f"https://www.president.gov.ua/en/news/old-{i}" for i in (0, 1)]       # then Exa's own order
