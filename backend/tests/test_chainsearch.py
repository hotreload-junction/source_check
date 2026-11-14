"""Source of the source without a link: the passage names it ("the New York Times reported"), Exa Agent finds the page.
Fake fetcher / LLM / search, no network."""
import json

import httpx

from app.config import Settings
from app.llm import LLMClient
from app.pipeline import chainsearch
from app.pipeline.assess import ClaimAssessment, SourceAssessment
from app.pipeline.chainsearch import named_source
from app.pipeline.depth2 import passage_of
from app.pipeline.run import analyze
from app.pipeline.verify import VerifyResult
from app.fetch import FetchResult
from tests.test_depth2 import CLEAN, QUOTE, S2
from tests.test_run import ARTICLE_URL, EXTRACT, FakeFetcher, FakeRdap, art, router, src, verifier

NYT_URL = "https://www.nytimes.com/2026/11/10/world/ministry-expands-coverage.html"
NYT_QUOTE = "The ministry will expand coverage to 40 districts by 2027, officials told reporters."
# the key only opens the chain-search gate: every other Exa feature stays off, so nothing here can reach a network
SETTINGS = Settings(**{**S2.model_dump(), "exa_api_key": "exa", "exa_agent": False, "factcheck_agent": False,
                       "search_unsourced": False, "origin_check": False, "find_copies": False})
NO_LINK = (f"Menu | Home\n\nThe ministry said on Tuesday that {QUOTE}, the New York Times reported last week.\n\n"
           "Unrelated paragraph with [a footer link](https://elsewhere.example/footer) and nothing else.")


def llm_saying(answer):
    def handler(request):
        content = json.dumps(answer)
        return httpx.Response(200, json={"model": "m", "choices": [{"finish_reason": "stop", "message": {"content": content}}]})
    return LLMClient("https://r.test/v1", "k", retries=0, backoff_s=0, client=httpx.Client(transport=httpx.MockTransport(handler)))


# ------------------------------------------------------------------ naming the source
def test_named_source_reads_the_origin_from_the_passage():
    llm = llm_saying({"source": "The New York Times"})
    assert named_source("s", "p", "claim text here", "ministry.gov.uk", llm=llm, model="m") == "The New York Times"


def test_named_source_is_none_for_null_junk_and_model_failures():
    for answer in ({"source": None}, {"source": "null"}, {"source": ""}, {"source": 5}, {"other": 1}, ["x"]):
        assert named_source("s", "p", "claim text here", "d.gov", llm=llm_saying(answer), model="m") is None

    def boom(request):
        return httpx.Response(500, json={})
    bad = LLMClient("https://r.test/v1", "k", retries=0, backoff_s=0, client=httpx.Client(transport=httpx.MockTransport(boom)))
    assert named_source("s", "p", "claim text here", "d.gov", llm=bad, model="m") is None


def test_passage_of_returns_sentence_and_paragraph_as_plain_text():
    sentence, paragraph = passage_of(NO_LINK, QUOTE, "irrelevant claim text here")
    assert QUOTE in sentence and "New York Times reported" in sentence
    assert "Unrelated paragraph" not in paragraph and "[" not in sentence
    assert passage_of("Completely different text about football and the weather today.", None, "The ministry said the programme will expand") is None


# ------------------------------------------------------------------ end to end
def found_assessment(url=NYT_URL, text=NYT_QUOTE):
    v = VerifyResult("supported", "supported", text, None, "en", 100.0, "The page states it.", "vm", "vT", source_type="secondary")
    fetch = FetchResult(url, "link", "ok", via="context_dev", text=text, html=CLEAN)
    sa = SourceAssessment(url, "named", "The New York Times", fetch, v, "not_checkable", corroborated=True, phrase_hit=True)
    return sa


def run(monkeypatch, *, who="The New York Times", found=True, settings=SETTINGS, parent_text=NO_LINK, calls=None):
    def fake_name(*a, **k):
        if calls is not None:
            calls.append("name")
        return who

    def fake_assess(claim, article_url, **k):
        if calls is not None:
            calls.append(("search", article_url, claim.attributions, claim.text))
        return ClaimAssessment(claim, "supported", None, "x", sources=[found_assessment()] if found else [])
    monkeypatch.setattr(chainsearch, "named_source", fake_name)
    monkeypatch.setattr(chainsearch, "_assess_attributed", fake_assess)
    pages = {"https://news-front.net/report-123": src("https://news-front.net/report-123", "Sources report new drone attacks.", CLEAN),
             "https://ministry.gov.uk/report": src("https://ministry.gov.uk/report", parent_text, CLEAN),
             NYT_URL: src(NYT_URL, NYT_QUOTE, CLEAN)}
    return analyze(ARTICLE_URL, fetcher=FakeFetcher(art(html=CLEAN), pages), llm=router(EXTRACT, verifier), settings=settings,
                   rdap=FakeRdap({})).result


def test_passage_without_link_that_names_a_source_gets_that_source_searched(monkeypatch):
    calls = []
    r = run(monkeypatch, calls=calls)
    parent = next(s for s in r["sources"] if s["url"] == "https://ministry.gov.uk/report")
    child = next(s for s in r["sources"] if s["depth"] == 2)
    assert child["url"] == NYT_URL and child["kind"] == "named" and child["attribution_text"] == "The New York Times"
    assert child["parent_source_id"] == parent["id"] and child["fetch_status"] == "ok"
    assert child["check"]["verdict"] == "supported" and child["check"]["quote_original"] == NYT_QUOTE
    assert {"id": f"e-{parent['id']}-{child['id']}", "source": parent["id"], "target": child["id"], "type": "cites"} in \
        [e for e in r["graph"]["edges"] if e["type"] == "cites"]
    search = next(c for c in calls if c[0] == "search")
    assert search[1] == "https://ministry.gov.uk/report" and search[2] == ["The New York Times"] and "New York Times reported" in search[3]


def test_nothing_found_leaves_a_stub_with_the_name_and_stays_neutral(monkeypatch):
    base = run(monkeypatch, found=True)["verdict"]["label"]
    r = run(monkeypatch, found=False)
    stub = next(s for s in r["sources"] if s["depth"] == 2)
    assert stub["url"] is None and stub["domain"] is None and stub["kind"] == "named" and stub["attribution_text"] == "The New York Times"
    assert stub["fetch_status"] == "not_fetched" and "check" not in stub
    assert r["verdict"]["label"] == base and r["signals"]["circle"]["fired"] is False


def test_a_found_page_never_feeds_the_signals(monkeypatch):
    r = run(monkeypatch)
    assert "nytimes.com" not in {d["domain"] for d in r["domains"]}
    assert r["signals"]["infrastructure"]["fired"] is False and r["signals"]["circle"]["fired"] is False


def test_no_search_when_the_passage_has_a_usable_link(monkeypatch):
    calls = []
    linked = NO_LINK.replace("the New York Times reported last week", "[the New York Times](https://www.nytimes.com/2026/11/10/linked.html) reported")
    r = run(monkeypatch, parent_text=linked, calls=calls)
    assert "name" not in calls and [s["kind"] for s in r["sources"] if s["depth"] == 2] != ["named"]


def test_no_search_when_the_passage_names_no_further_source(monkeypatch):
    calls = []
    r = run(monkeypatch, who=None, calls=calls)
    assert not any(c[0] == "search" for c in calls if isinstance(c, tuple)) and all(s["depth"] == 1 for s in r["sources"])


def test_switched_off_or_without_exa_key_nothing_is_searched(monkeypatch):
    for settings in (Settings(**{**SETTINGS.model_dump(), "chain_search": False}), Settings(**{**SETTINGS.model_dump(), "exa_api_key": ""}),
                     Settings(**{**SETTINGS.model_dump(), "max_chain_searches": 0})):
        calls = []
        r = run(monkeypatch, settings=settings, calls=calls)
        assert calls == [] and all(s["depth"] == 1 for s in r["sources"])


# ------------------------------------------------------------------ the real search + verify path (fake Exa / LLM)
def test_find_cited_runs_plan_search_fetch_and_quote_first_verify():
    from app.pipeline.assess import _FetchCache

    def handler(request):
        sysmsg = json.loads(request.content)["messages"][0]["content"]
        if sysmsg.startswith("You read ONE passage"):
            data = {"source": "The New York Times"}
        elif sysmsg.startswith("A news article makes a claim"):
            data = {"speaker": "The New York Times", "venue": "The New York Times", "domains": ["nytimes.com"],
                    "queries": ["New York Times ministry expand coverage 40 districts 2027"], "phrases": []}
        else:
            data = {"quote_original": NYT_QUOTE, "quote_translation": None, "language": "en", "verdict": "supported",
                    "explanation": "The page states it.", "source_type": "original"}
        return httpx.Response(200, json={"model": "m", "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(data)}}]})
    llm = LLMClient("https://r.test/v1", "k", retries=0, backoff_s=0, client=httpx.Client(transport=httpx.MockTransport(handler)))
    exa = httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(200, json={"results": [{"url": NYT_URL, "title": "T"}]})))

    class Fetcher:
        def fetch_source(self, url, article_url, with_html=True):
            return src(url, NYT_QUOTE, CLEAN)

        def fetch_html(self, url):
            return CLEAN

    sentence, paragraph = passage_of(NO_LINK, QUOTE, "irrelevant claim text here")
    cited = chainsearch.find_cited("https://ministry.gov.uk/report", "ministry.gov.uk", NO_LINK, sentence, paragraph,
                                   "The ministry said the programme will expand coverage", cache=_FetchCache(Fetcher(), ARTICLE_URL),
                                   llm=llm, settings=SETTINGS, exa_client=exa)
    assert cited.who == "The New York Times" and cited.found is not None
    assert cited.found.url == NYT_URL and cited.found.verify.quote_original == NYT_QUOTE and cited.found.verify.fuzzy_score >= 90
