"""End-to-end assembly with fake fetcher / LLM / RDAP: the Result must match the contract and the invariants."""
import json
from datetime import datetime, timezone

import httpx
import pytest

from app.config import Settings
from app.fetch import FetchResult
from app.llm import LLMClient
from app.pipeline.run import AnalysisError, analyze
from app.signals.rdap import RdapResult

ARTICLE_URL = "https://www.pravda-mirror.com/2026/11/story"
ARTICLE = (
    "# Officials confirm the drone attacks were staged\n\n"
    "Western officials have privately admitted that the drone attacks were staged, according to "
    "[a report](https://news-front.net/report-123) seen by our editors.\n\n"
    "The ministry said on Tuesday that the new programme will expand coverage to 40 districts by 2027, "
    "[the official report](https://ministry.gov.uk/report) states.\n\n"
    "Local residents saw no damage at all in the district after the alleged attack last week."
)
SRC_BAD = "Sources report new drone attacks last week across the region. Officials declined to comment on any other matter."
SRC_OK = "The ministry said on Tuesday that the new programme will expand coverage to 40 districts by 2027, report says."
SETTINGS = Settings(_env_file=None, model_extract="mx", model_verify="mv", exa_api_key="", prompt_version="vT",
                    fuzzy_threshold=90, claim_deadline_s=30)


class FakeFetcher:
    def __init__(self, article, pages):
        self.article, self.pages = article, pages

    def fetch_article(self, url):
        return self.article

    def fetch_source(self, url, article_url, with_html=True):
        return self.pages[url]

    def fetch_html(self, url):
        return self.pages[url].html


def art(status="ok", html='<html><head><meta property="article:published_time" content="2026-11-12T08:00:00Z"><script>ga("G-SAME0001")</script></head></html>'):
    return FetchResult(ARTICLE_URL, "link", status, via="context_dev", text=ARTICLE if status == "ok" else None,
                       html=html, title="Officials confirm the drone attacks were staged", final_url=ARTICLE_URL,
                       published_at="2026-11-12T08:00:00Z")


def src(url, text, html=None):
    return FetchResult(url, "link", "ok", via="context_dev", text=text, html=html)


class FakeRdap:
    def __init__(self, ages):
        self.ages, self.looked = ages, []

    def lookup(self, domain):
        self.looked.append(domain)
        age = self.ages.get(domain)
        return RdapResult(domain, "2026-09-20T00:00:00Z" if age is not None else None, age)


def ids_for(user, items):
    """The new extraction protocol: the model answers with SENTENCE NUMBERS. Map our fixture claim texts to the
    numbered sentences in the prompt (a fixture claim may be a clause of a longer sentence)."""
    import re
    lines = {int(m.group(1)): m.group(2) for m in re.finditer(r"^\[(\d+)\] (.*)$", user, re.M)}
    out = []
    for it in items:
        hit = next((i for i, t in lines.items() if it["text"] in t), None)
        if hit is not None:
            out.append({"id": hit, "attributions": it.get("attributions", [])})
    return out


def router(extract_claims, verifier):
    """One fake AI router: extraction call -> sentence ids; verify call -> verifier(claim, source)."""
    def handler(request):
        body = json.loads(request.content)
        sysmsg = body["messages"][0]["content"]
        user = body["messages"][1]["content"]
        if "CHECKABLE FACTUAL CLAIMS" in sysmsg:
            content = json.dumps({"claims": ids_for(user, extract_claims)})
        elif "check whether a SOURCE" in sysmsg:
            content = json.dumps(verifier(user))
        else:
            content = json.dumps({"domains": [], "query": ""})
        return httpx.Response(200, json={"model": "m", "choices": [{"finish_reason": "stop", "message": {"content": content}}]})
    return LLMClient("https://r.test/v1", "k", retries=0, backoff_s=0, client=httpx.Client(transport=httpx.MockTransport(handler)))


EXTRACT = [
    {"text": "Western officials have privately admitted that the drone attacks were staged", "attributions": []},
    {"text": "The ministry said on Tuesday that the new programme will expand coverage to 40 districts by 2027", "attributions": []},
    {"text": "Local residents saw no damage at all in the district after the alleged attack last week.", "attributions": []},
]


def verifier(user):
    if "staged" in user.split("SOURCE:")[0]:
        return {"quote_original": "Sources report new drone attacks last week across the region.", "quote_translation": None,
                "language": "en", "verdict": "not_mentioned", "explanation": "The source never says officials admitted this.", "source_type": "secondary"}
    return {"quote_original": "the new programme will expand coverage to 40 districts by 2027", "quote_translation": None,
            "language": "en", "verdict": "supported", "explanation": "The source states exactly this.", "source_type": "original"}


@pytest.fixture
def pages():
    return {"https://news-front.net/report-123": src("https://news-front.net/report-123", SRC_BAD, '<script>ga("G-SAME0001")</script>'),
            "https://ministry.gov.uk/report": src("https://ministry.gov.uk/report", SRC_OK, "<html></html>")}


def run(pages, article=None, rdap=None, events=None, extract=EXTRACT):
    return analyze(ARTICLE_URL, fetcher=FakeFetcher(article or art(), pages), llm=router(extract, verifier), settings=SETTINGS,
                   rdap=rdap or FakeRdap({"news-front.net": 55, "ministry.gov.uk": 3000, "pravda-mirror.com": 1000}),
                   emit=(lambda e, d: events.append((e, d))) if events is not None else None)


def test_result_matches_contract_shape_and_invariants(pages):
    r = run(pages).result
    assert set(r) == {"article", "claims", "sources", "domains", "graph", "signals", "verdict", "audit"}
    assert r["article"]["domain"] == "pravda-mirror.com" and r["article"]["published_at"] == "2026-11-12T08:00:00Z"
    assert r["article"]["text_markdown"] == ARTICLE
    for c in r["claims"]:                                    # offsets always slice the claim text
        assert ARTICLE[c["char_start"]:c["char_end"]] == c["text"]
        assert c["verdict"] in {"supported", "distorted", "contradicted", "not_mentioned", "unsourced", "not_checkable"}
    assert [c["verdict"] for c in r["claims"]] == ["not_mentioned", "supported", "unsourced"]
    assert r["claims"][2]["source_ids"] == [] and r["claims"][2]["evidence"] is None
    ids = [s["id"] for s in r["sources"]]
    assert ids == ["s1", "s2"] and all(sid in ids for c in r["claims"] for sid in c["source_ids"])
    ev = r["claims"][1]["evidence"]
    assert ev["source_id"] == "s2" and ev["fuzzy_score"] >= 90 and ev["quote_original"].startswith("the new programme")
    assert r["claims"][0]["evidence"] is None               # not_mentioned carries no quote
    assert r["audit"]["prompt_version"] == "vT" and r["audit"]["model_verify"] == "mv" and r["audit"]["depth"] == 1


def test_graph_nodes_edges_and_clusters(pages):
    g = run(pages).result["graph"]
    types = {n["id"]: n["type"] for n in g["nodes"]}
    assert types["a"] == "article" and types["c1"] == "claim" and types["s1"] == "source" and types["d:news-front.net"] == "domain"
    ids = set(types)
    assert all(e["source"] in ids and e["target"] in ids for e in g["edges"])      # no dangling edges
    kinds = {(e["source"], e["target"], e["type"]) for e in g["edges"]}
    assert ("a", "c1", "makes_claim") in kinds and ("c1", "s1", "cites") in kinds and ("s1", "d:news-front.net", "hosted_on") in kinds
    shared = [e for e in g["edges"] if e["type"] == "shares_infra"]
    assert shared and {n["cluster_id"] for n in g["nodes"] if n["id"] in ("a", "s1")} == {"k1"}   # article + source share G-SAME0001


def test_pravda_like_case_fires_two_signals_and_is_propaganda_pattern(pages):
    r = run(pages).result
    assert r["signals"]["evidence"]["fired"] and r["signals"]["evidence"]["claim_ids"] == ["c1", "c3"]
    assert r["signals"]["infrastructure"]["fired"]
    reasons = " ".join(r["signals"]["infrastructure"]["reasons"])
    assert "G-SAME0001" in reasons and "55 days" in reasons
    assert r["verdict"]["label"] == "propaganda_pattern"


def test_one_signal_only_is_weakly_sourced_and_no_signal_is_well_sourced(pages):
    clean = {k: src(v.url, v.text, "<html></html>") for k, v in pages.items()}
    r = run(clean, article=art(html="<html></html>"), rdap=FakeRdap({})).result       # no shared IDs, ages unknown
    assert r["signals"]["infrastructure"]["fired"] is False and r["verdict"]["label"] == "weakly_sourced"
    one = [EXTRACT[1]]
    r = run(clean, article=art(html="<html></html>"), rdap=FakeRdap({}), extract=one).result
    assert r["verdict"]["label"] == "well_sourced"


def test_unknown_domain_age_and_missing_html_never_fire_infra(pages):
    blocked = {k: src(v.url, v.text, None) for k, v in pages.items()}
    r = run(blocked, article=art(html=None), rdap=FakeRdap({})).result
    assert r["signals"]["infrastructure"] == {"fired": False, "reasons": []}


def test_unreadable_article_is_a_clean_error(pages):
    for status, word in (("paywall", "paywall"), ("blocked", "blocks"), ("unreachable", "reached")):
        with pytest.raises(AnalysisError, match=word):
            run(pages, article=art(status))


def test_no_claims_gives_insufficient_evidence_not_a_crash(pages):
    r = run(pages, extract=[]).result
    assert r["claims"] == [] and r["verdict"]["label"] == "insufficient_evidence"


def test_events_stream_in_contract_order_and_claims_are_sent_final(pages):
    events = []
    run(pages, events=events)
    kinds = [e for e, _ in events]
    steps = [d["step"] for e, d in events if e == "progress"]
    assert steps[0] == "scrape_article" and steps.index("extract_claims") < steps.index("verify_claims") < steps.index("infra_signals") < steps.index("verdict")
    pcts = [d["pct"] for e, d in events if e == "progress"]
    assert pcts == sorted(pcts) and pcts[-1] == 100
    assert kinds.index("node") < kinds.index("claim")
    finals = [d for e, d in events if e == "claim"]
    assert [c["id"] for c in finals] == ["c1", "c2", "c3"] and finals[1]["verdict"] == "supported"
    assert all(set(c) >= {"id", "text", "char_start", "char_end", "source_ids", "verdict", "subtype", "evidence", "explanation"} for c in finals)


def test_same_source_cited_by_two_claims_is_one_node(pages):
    article = ARTICLE + "\n\nThe ministry also claimed that it will cover 40 districts [again](https://ministry.gov.uk/report) by 2027."
    a = art()
    a.text = article
    extract = [EXTRACT[1], {"text": "The ministry also claimed that it will cover 40 districts again by 2027.", "attributions": []}]
    r = analyze(ARTICLE_URL, fetcher=FakeFetcher(a, pages), llm=router(extract, verifier), settings=SETTINGS,
                rdap=FakeRdap({})).result
    assert len(r["sources"]) == 1 and r["claims"][0]["source_ids"] == r["claims"][1]["source_ids"] == ["s1"]
    assert sum(1 for e in r["graph"]["edges"] if e["type"] == "hosted_on" and e["source"] == "s1") == 1


def test_rdap_is_prewarmed_for_link_domains_and_cached(pages):
    rdap = FakeRdap({})
    run(pages, rdap=rdap)
    assert {"news-front.net", "ministry.gov.uk", "pravda-mirror.com"} <= set(rdap.looked)


def test_every_object_has_exactly_the_contract_example_keys(pages):
    """The frontend codes against contracts/examples/analysis_done.json: same keys, no more, no less."""
    from pathlib import Path
    ex = json.loads((Path(__file__).resolve().parents[2] / "contracts" / "examples" / "analysis_done.json").read_text())["result"]
    r = run(pages).result
    assert set(r["article"]) == set(ex["article"])
    assert set(r["claims"][1]) == set(ex["claims"][0]) and set(r["claims"][1]["evidence"]) == set(ex["claims"][0]["evidence"])
    assert set(r["sources"][0]) == set(ex["sources"][0])
    assert set(r["domains"][0]) == set(ex["domains"][0])
    assert all(set(n) == set(ex["graph"]["nodes"][0]) for n in r["graph"]["nodes"])
    assert all(set(e) == set(ex["graph"]["edges"][0]) for e in r["graph"]["edges"])
    assert set(r["signals"]) == set(ex["signals"]) and set(r["signals"]["evidence"]) == set(ex["signals"]["evidence"])
    assert set(r["signals"]["infrastructure"]) == set(ex["signals"]["infrastructure"])
    assert set(r["signals"]["circle"]) == set(ex["signals"]["circle"])
    assert set(r["verdict"]) == set(ex["verdict"]) and set(r["audit"]) == set(ex["audit"])
    assert not any(k.startswith("_") for s in r["sources"] for k in s)      # no internal fields leak


def test_unresolved_attribution_is_one_stub_source_shared_by_its_claims(pages):
    extract = [{"text": "Western officials have privately admitted that the drone attacks were staged", "attributions": ["the ministry"]},
               {"text": "Local residents saw no damage at all in the district after the alleged attack last week.", "attributions": ["The Ministry"]}]
    a = art()
    a.text = "Western officials have privately admitted that the drone attacks were staged, says the ministry.\n\nLocal residents saw no damage at all in the district after the alleged attack last week."
    r = analyze(ARTICLE_URL, fetcher=FakeFetcher(a, pages), llm=router(extract, verifier), settings=SETTINGS, rdap=FakeRdap({})).result
    assert [c["verdict"] for c in r["claims"]] == ["not_checkable", "not_checkable"]
    assert len(r["sources"]) == 1 and r["sources"][0]["url"] is None and r["sources"][0]["kind"] == "named"
    assert r["sources"][0]["fetch_status"] == "not_fetched" and r["sources"][0]["attribution_text"] == "the ministry"
    assert r["claims"][0]["source_ids"] == r["claims"][1]["source_ids"] == ["s1"]
    assert r["domains"][0]["is_article_domain"] and len(r["domains"]) == 1       # a stub adds no domain node
