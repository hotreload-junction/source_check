"""Root or relay (pipeline/origin.py): a claim with no checkable source either ends at its root (neutral) or breaks off
at a relay (unsourced). Fake LLM, no network."""
import json

import httpx

from app.llm import LLMClient
from app.pipeline.origin import classify, eligible
from app.pipeline.run import analyze
from tests.test_run import (ARTICLE, ARTICLE_URL, EXTRACT, SETTINGS, FakeFetcher, FakeRdap, art, ids_for, src,
                            verifier, SRC_BAD, SRC_OK)

PARA = ARTICLE.split("\n\n")[-1]          # "Local residents saw no damage at all ..." (the unsourced claim)


def llm_with(origin_answer, calls=None, extract=EXTRACT):
    def handler(request):
        body = json.loads(request.content)
        sysmsg, user = body["messages"][0]["content"], body["messages"][1]["content"]
        if "CHECKABLE FACTUAL CLAIMS" in sysmsg:
            content = {"claims": ids_for(user, extract)}
        elif "check whether a SOURCE" in sysmsg:
            content = verifier(user)
        elif "ORIGINATES" in sysmsg:
            if calls is not None:
                calls.append(user)
            if origin_answer == "error":
                return httpx.Response(400, text="bad")
            content = origin_answer
        else:
            content = {"domains": [], "query": ""}
        return httpx.Response(200, json={"model": "m", "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(content)}}]})
    return LLMClient("https://r.test/v1", "k", retries=0, backoff_s=0, client=httpx.Client(transport=httpx.MockTransport(handler)))


def pages():
    return {"https://news-front.net/report-123": src("https://news-front.net/report-123", SRC_BAD, "<html></html>"),
            "https://ministry.gov.uk/report": src("https://ministry.gov.uk/report", SRC_OK, "<html></html>")}


def run(answer, calls=None, extract=EXTRACT, settings=SETTINGS):
    return analyze(ARTICLE_URL, fetcher=FakeFetcher(art(), pages()), llm=llm_with(answer, calls, extract), settings=settings,
                   rdap=FakeRdap({})).result


ROOT = {"origin": "root", "origin_holder": "the local residents", "article_span": "Local residents saw no damage at all",
        "reason": "A first-hand observation by the residents."}
RELAY = {"origin": "relay", "origin_holder": "unnamed observers", "article_span": "Local residents saw no damage at all",
         "reason": "The article asserts it as fact without saying who saw it."}


# ------------------------------------------------------------------ unit
def test_classify_needs_the_span_in_the_paragraph():
    assert classify("c", PARA, [], llm=llm_with(ROOT), settings=SETTINGS).origin == "root"
    invented = {**ROOT, "article_span": "the reporter watched it happen from the roof"}
    assert classify("c", PARA, [], llm=llm_with(invented), settings=SETTINGS) is None        # quote first
    assert classify("c", PARA, [], llm=llm_with({**ROOT, "origin": "maybe"}), settings=SETTINGS) is None
    assert classify("c", PARA, [], llm=llm_with({**ROOT, "article_span": "saw"}), settings=SETTINGS) is None
    assert classify("c", PARA, [], llm=llm_with("error"), settings=SETTINGS) is None


def test_eligible_only_without_evidence_subtype_or_cited_url():
    rows = {"s1": {"id": "s1", "url": None}, "s2": {"id": "s2", "url": "https://x.example/a"}}
    base = {"verdict": "not_checkable", "evidence": None, "subtype": None, "source_ids": ["s1"]}
    assert eligible(base, rows)                                                     # named stub, no URL
    assert eligible({**base, "verdict": "unsourced", "source_ids": []}, rows)
    assert not eligible({**base, "source_ids": ["s2"]}, rows)                      # cited page unreachable: stays neutral
    assert not eligible({**base, "subtype": "self_citation"}, rows)
    assert not eligible({**base, "subtype": "social_media"}, rows)
    assert not eligible({**base, "verdict": "supported"}, rows)
    assert not eligible({**base, "evidence": {"quote_original": "q"}}, rows)


# ------------------------------------------------------------------ end to end
def test_root_turns_an_unsourced_claim_neutral_and_only_eligible_claims_are_asked():
    calls = []
    r = run(ROOT, calls)
    c3 = r["claims"][2]
    assert c3["verdict"] == "not_checkable" and c3["explanation"].startswith("Root source:")
    assert [c["verdict"] for c in r["claims"][:2]] == ["not_mentioned", "supported"]   # linked claims untouched
    assert len(calls) == 1 and "Local residents" in calls[0]


def test_relay_stays_unsourced_with_an_explanation():
    c3 = run(RELAY)["claims"][2]
    assert c3["verdict"] == "unsourced" and c3["explanation"].startswith("Not the root source:")


def test_vague_attribution_without_url_becomes_unsourced():
    """The 'doctors claim' case: an attribution without a page is no longer automatically neutral."""
    extract = [*EXTRACT[:2], {**EXTRACT[2], "attributions": ["doctors"]}]
    before = run("error", extract=extract)["claims"][2]
    assert before["verdict"] == "not_checkable"                       # LLM down: unchanged, neutral
    after = run({**RELAY, "origin_holder": "unnamed doctors"}, extract=extract)["claims"][2]
    assert after["verdict"] == "unsourced" and "unnamed doctors" in after["explanation"]


def test_switch_off_and_llm_failure_keep_verdicts():
    off = SETTINGS.model_copy(update={"origin_check": False})
    calls = []
    assert run(ROOT, calls, settings=off)["claims"][2]["verdict"] == "unsourced" and calls == []
    assert run("error")["claims"][2]["verdict"] == "unsourced"


def check(page_verdict, quote="a verbatim sentence from the page"):
    return {"source_id": "s5", "verdict": "not_checkable", "page_verdict": page_verdict, "quote_original": quote, "explanation": "x"}


def test_pages_that_were_read_and_quoted_keep_the_claim_out_of_the_origin_check():
    base = {"verdict": "not_checkable", "evidence": None, "subtype": None, "source_ids": [], "found_source_ids": ["s5", "s6"]}
    # "Sources disagree": one found page confirms, another contradicts, both with a verified quote
    both = {**base, "source_checks": [check("supported"), check("contradicted")]}
    assert not eligible(both, {})
    assert not eligible({**base, "source_checks": [check("distorted")]}, {})
    # a page that was read but says nothing about the claim, or could not be read, is no evidence: still asked
    assert eligible({**base, "source_checks": [check("not_mentioned", None), check(None, None)]}, {})
    assert eligible({**base, "source_checks": []}, {}) and eligible(base, {})      # no checks (older result): as before
