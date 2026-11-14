import json

import httpx

from app.config import Settings
from app.fetch import FetchResult
from app.llm import LLMClient
from app.pipeline.assess import assess_claims, audit_records
from app.pipeline.extract import ExtractedClaim
from app.pipeline.textutil import LinkRef

ART = "https://news.test/a"
SETTINGS = Settings(_env_file=None, model_verify="vm", exa_api_key="exa", fuzzy_threshold=90, prompt_version="vT")
TEXT = ("The ministry said on Tuesday that the new programme will expand coverage to 40 districts by 2027, "
        "according to the official report published last week.")
QUOTE = "the new programme will expand coverage to 40 districts by 2027"


class FakeFetcher:
    def __init__(self, pages):
        self.pages, self.calls = pages, []

    def fetch_source(self, url, article_url):
        self.calls.append(url)
        return self.pages[url]


def page(url, status="ok", kind="link", text=TEXT, subtype=None):
    return FetchResult(url, kind, status, text=text if status == "ok" else None, subtype=subtype)


def claim(i, links=(), attrs=()):
    return ExtractedClaim(f"c{i}", f"Claim number {i} text.", 0, 10, [LinkRef(u, "a", "sentence") for u in links], list(attrs))


def make_llm(verdict="supported", quote=QUOTE, calls=None):
    def handler(request):
        if calls is not None:
            calls.append(1)
        c = json.dumps({"quote_original": quote, "quote_translation": None, "language": "en", "verdict": verdict, "explanation": "why."})
        return httpx.Response(200, json={"model": "vm", "choices": [{"message": {"content": c}}]})
    return LLMClient("https://r.test/v1", "k", retries=0, client=httpx.Client(transport=httpx.MockTransport(handler)))


def exa_client(url="https://orig.test/interview"):
    def handler(request):
        return httpx.Response(200, json={"results": ([{"url": url, "title": "T"}] if url else [])})
    return httpx.Client(transport=httpx.MockTransport(handler))


def run(claims, pages, llm=None, exa=None):
    f = FakeFetcher(pages)
    res = assess_claims(claims, ART, fetcher=f, llm=llm or make_llm(), settings=SETTINGS, exa_client=exa)
    return res, f


def test_no_links_no_attribution_is_unsourced():
    (a,), _ = run([claim(1)], {})
    assert a.verdict == "unsourced" and a.sources == []


def test_linked_claim_supported_with_evidence():
    (a,), _ = run([claim(1, ["https://src.test/x"])], {"https://src.test/x": page("https://src.test/x")})
    assert a.verdict == "supported" and a.evidence.fuzzy_score >= 90 and a.best.url == "https://src.test/x"


def test_fabricated_quote_never_yields_supported():
    (a,), _ = run([claim(1, ["https://src.test/x"])], {"https://src.test/x": page("https://src.test/x")},
                  llm=make_llm("supported", "A sentence that is nowhere in the source text at all, honestly."))
    assert a.verdict == "not_checkable" and a.evidence is None


def test_neutral_fetch_outcomes():
    cases = [("unreachable", "link", None), ("paywall", "link", None), ("blocked", "link", None),
             ("not_fetched", "social", "social_media"), ("not_fetched", "self", "self_citation")]
    for status, kind, subtype in cases:
        url = f"https://x.test/{status}{kind}"
        (a,), _ = run([claim(1, [url])], {url: page(url, status, kind, subtype=subtype)})
        assert a.verdict == "not_checkable" and a.subtype == subtype, (status, kind)


def test_not_mentioned_counts_when_source_was_read():
    (a,), _ = run([claim(1, ["https://src.test/x"])], {"https://src.test/x": page("https://src.test/x")},
                  llm=make_llm("not_mentioned", ""))
    assert a.verdict == "not_mentioned"


def test_multiple_sources_supported_beats_neutral_and_negative():
    urls = ["https://a.test/1", "https://b.test/2", "https://c.test/3"]
    pages = {urls[0]: page(urls[0], "unreachable"), urls[1]: page(urls[1]), urls[2]: page(urls[2])}
    seq = iter(["distorted", "supported"])

    def handler(request):
        v = next(seq)
        c = json.dumps({"quote_original": QUOTE, "quote_translation": None, "language": "en", "verdict": v, "explanation": v})
        return httpx.Response(200, json={"model": "vm", "choices": [{"message": {"content": c}}]})
    llm = LLMClient("https://r.test/v1", "k", retries=0, client=httpx.Client(transport=httpx.MockTransport(handler)))
    (a,), _ = run([claim(1, urls)], pages, llm=llm)
    assert a.verdict == "supported" and len(a.sources) == 3


def test_named_source_resolved_via_exa_then_verified():
    (a,), f = run([claim(1, attrs=["the Finnish Ministry"])], {"https://orig.test/interview": page("https://orig.test/interview")},
                  exa=exa_client())
    assert a.verdict == "supported" and a.best.kind == "named" and a.best.attribution_text == "the Finnish Ministry"
    assert f.calls == ["https://orig.test/interview"]


def test_named_source_not_found_is_neutral_not_unsourced():
    (a,), _ = run([claim(1, attrs=["Zelenskyy"])], {}, exa=exa_client(None))
    assert a.verdict == "not_checkable" and "Zelenskyy" in a.explanation


def test_named_source_that_does_not_mention_claim_is_neutral():
    (a,), _ = run([claim(1, attrs=["X"])], {"https://orig.test/interview": page("https://orig.test/interview")},
                  llm=make_llm("not_mentioned", ""), exa=exa_client())
    assert a.verdict == "not_checkable"     # an Exa guess proves nothing about the article


def test_named_source_can_still_show_a_verified_distortion():
    (a,), _ = run([claim(1, attrs=["X"])], {"https://orig.test/interview": page("https://orig.test/interview")},
                  llm=make_llm("distorted", QUOTE), exa=exa_client())
    assert a.verdict == "distorted"


def test_links_take_precedence_over_exa_and_same_url_fetched_once():
    pages = {"https://src.test/x": page("https://src.test/x")}
    res, f = run([claim(1, ["https://src.test/x"], attrs=["A"]), claim(2, ["https://src.test/x"])], pages, exa=exa_client())
    assert [a.verdict for a in res] == ["supported", "supported"]
    assert f.calls == ["https://src.test/x"]


def test_verify_failure_is_neutral():
    def handler(request):
        return httpx.Response(500)
    llm = LLMClient("https://r.test/v1", "k", retries=0, client=httpx.Client(transport=httpx.MockTransport(handler)))
    (a,), _ = run([claim(1, ["https://src.test/x"])], {"https://src.test/x": page("https://src.test/x")}, llm=llm)
    assert a.verdict == "not_checkable"


def test_audit_records_carry_quote_score_model_prompt_version():
    (a,), _ = run([claim(1, ["https://src.test/x"])], {"https://src.test/x": page("https://src.test/x")})
    (row,) = audit_records(a, 7, lambda sa: "s1")
    assert (row.claim_pk, row.source_id, row.verdict, row.model, row.prompt_version) == (7, "s1", "supported", "vm", "vT")
    assert row.quote_original == QUOTE and row.fuzzy_score >= 90 and row.llm_verdict == "supported"
    (u,), _ = run([claim(2)], {})
    assert audit_records(u, 8, lambda sa: None)[0].verdict == "unsourced"
