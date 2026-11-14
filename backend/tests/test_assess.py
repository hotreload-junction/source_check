import json

import httpx

from app.config import Settings
from app.fetch import FetchResult
from app.llm import LLMClient
from app.pipeline.assess import assess_claims, audit_records
from app.pipeline.extract import ExtractedClaim
from app.pipeline.textutil import LinkRef

ART = "https://news.test/a"
SETTINGS = Settings(_env_file=None, model_verify="vm", exa_api_key="exa", fuzzy_threshold=90, prompt_version="vT",
                    search_unsourced=False, exa_agent=False)
SETTINGS_ALL = Settings(_env_file=None, model_verify="vm", exa_api_key="exa", fuzzy_threshold=90, prompt_version="vT",
                        verify_secondary=True, search_unsourced=False, exa_agent=False)
TEXT = ("The ministry said on Tuesday that the new programme will expand coverage to 40 districts by 2027, "
        "according to the official report published last week.")
QUOTE = "the new programme will expand coverage to 40 districts by 2027"


class FakeFetcher:
    def __init__(self, pages):
        self.pages, self.calls = pages, []

    def fetch_source(self, url, article_url, with_html=True):
        self.calls.append(url)
        self.with_html_args = getattr(self, "with_html_args", []) + [with_html]
        return self.pages[url]

    def fetch_linked_page(self, url):
        self.calls.append(url)
        return self.pages[url]

    def fetch_html(self, url):
        return f"<html>{url}</html>"


def page(url, status="ok", kind="link", text=TEXT, subtype=None):
    return FetchResult(url, kind, status, text=text if status == "ok" else None, subtype=subtype)


def claim(i, links=(), attrs=()):
    return ExtractedClaim(f"c{i}", f"Claim number {i} text.", 0, 10, [LinkRef(u, "a", "sentence") for u in links], list(attrs))


def make_llm(verdict="supported", quote=QUOTE, calls=None, source_type="original"):
    def handler(request):
        if calls is not None:
            calls.append(1)
        c = json.dumps({"quote_original": quote, "quote_translation": None, "language": "en", "verdict": verdict, "explanation": "why.", "source_type": source_type})
        return httpx.Response(200, json={"model": "vm", "choices": [{"message": {"content": c}}]})
    return LLMClient("https://r.test/v1", "k", retries=0, client=httpx.Client(transport=httpx.MockTransport(handler)))


def exa_client(url="https://www.orig.gov.test/interview"):
    def handler(request):
        return httpx.Response(200, json={"results": ([{"url": url, "title": "T"}] if url else [])})
    return httpx.Client(transport=httpx.MockTransport(handler))


def run(claims, pages, llm=None, exa=None, settings=None):
    f = FakeFetcher(pages)
    res = assess_claims(claims, ART, fetcher=f, llm=llm or make_llm(), settings=settings or SETTINGS, exa_client=exa)
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
    (a,), f = run([claim(1, attrs=["the Finnish Ministry"])], {"https://www.orig.gov.test/interview": page("https://www.orig.gov.test/interview")},
                  exa=exa_client())
    assert a.verdict == "supported" and a.best.kind == "named" and a.best.attribution_text == "the Finnish Ministry"
    assert f.calls == ["https://www.orig.gov.test/interview"]


def test_named_source_secondary_report_corroborates_but_is_neutral():
    (a,), _ = run([claim(1, attrs=["Zelensky"])], {"https://outlet.test/report": page("https://outlet.test/report")},
                  llm=make_llm("supported", QUOTE, source_type="secondary"), exa=exa_client("https://outlet.test/report"),
                  settings=SETTINGS_ALL)
    assert a.verdict == "not_checkable" and a.evidence is None and a.best.corroborated
    assert "original" in a.explanation and "could not be found" in a.explanation


def test_secondary_report_cannot_contradict_or_distort():
    for verdict in ("distorted", "contradicted", "not_mentioned"):
        (a,), _ = run([claim(1, attrs=["X"])], {"https://www.orig.gov.test/interview": page("https://www.orig.gov.test/interview")},
                      llm=make_llm(verdict, QUOTE if verdict != "not_mentioned" else "", source_type="secondary"), exa=exa_client())
        assert a.verdict == "not_checkable", verdict


def test_original_found_after_secondary_candidates_decides():
    urls = ["https://outlet.test/report", "https://org.test/statement"]

    def exa_handler(request):
        return httpx.Response(200, json={"results": [{"url": u} for u in urls]})
    # candidates are verified in parallel: tell original from secondary by the PAGE, never by call order
    pages = {urls[0]: page(urls[0]), urls[1]: page(urls[1], text=TEXT + " ORIGINAL-MARKER")}

    def handler(request):
        user = json.loads(request.content)["messages"][1]["content"]
        c = json.dumps({"quote_original": QUOTE, "quote_translation": None, "language": "en", "verdict": "supported",
                        "explanation": "ok.", "source_type": "original" if "ORIGINAL-MARKER" in user else "secondary"})
        return httpx.Response(200, json={"model": "vm", "choices": [{"message": {"content": c}}]})
    llm = LLMClient("https://r.test/v1", "k", retries=0, client=httpx.Client(transport=httpx.MockTransport(handler)))
    (a,), _ = run([claim(1, attrs=["Org"])], pages, llm=llm, exa=httpx.Client(transport=httpx.MockTransport(exa_handler)),
                  settings=SETTINGS_ALL)
    assert a.verdict == "supported" and a.best.url == "https://org.test/statement" and len(a.sources) == 2


def test_guessed_original_that_differs_is_neutral_not_distorted():
    # live case: a ministry briefing from before a trip vs a report written after it
    for verdict in ("distorted", "contradicted"):
        (a,), _ = run([claim(1, attrs=["X"])], {"https://www.orig.gov.test/interview": page("https://www.orig.gov.test/interview")},
                      llm=make_llm(verdict, QUOTE), exa=exa_client())
        assert a.verdict == "not_checkable" and a.evidence is None and "Quote could not be verified" in a.explanation


def test_linked_original_that_distorts_still_counts():
    (a,), _ = run([claim(1, ["https://src.test/x"])], {"https://src.test/x": page("https://src.test/x")},
                  llm=make_llm("distorted", QUOTE))
    assert a.verdict == "distorted"       # the article itself cited this source: that is real evidence


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


def test_article_date_only_sent_for_guessed_named_sources():
    from datetime import datetime
    seen = []

    def handler(request):
        seen.append(json.loads(request.content)["messages"][1]["content"])
        c = json.dumps({"quote_original": "", "quote_translation": None, "language": "en", "verdict": "not_mentioned",
                        "explanation": "x", "source_type": "original"})
        return httpx.Response(200, json={"model": "vm", "choices": [{"message": {"content": c}}]})
    llm = LLMClient("https://r.test/v1", "k", retries=0, client=httpx.Client(transport=httpx.MockTransport(handler)))
    f = FakeFetcher({"https://src.test/x": page("https://src.test/x"), "https://www.orig.gov.test/interview": page("https://www.orig.gov.test/interview")})
    assess_claims([claim(1, ["https://src.test/x"])], ART, fetcher=f, llm=llm, settings=SETTINGS, published_at=datetime(2026, 11, 14))
    assess_claims([claim(2, attrs=["X"])], ART, fetcher=f, llm=llm, settings=SETTINGS, exa_client=exa_client(),
                  published_at=datetime(2026, 11, 14))
    linked = [m for m in seen if "Claim number 1" in m]
    named = [m for m in seen if "Claim number 2" in m]
    assert linked and all("ARTICLE DATE" not in m for m in linked)          # the article cited it itself: date irrelevant
    assert named and all(m.startswith("ARTICLE DATE: 2026-11-14") for m in named)


def test_secondary_candidates_are_not_verified_by_default():
    calls = []
    with_model = Settings(_env_file=None, model_verify="vm", model_extract="m", exa_api_key="exa", prompt_version="vT", exa_agent=False)
    (a,), f = run([claim(1, attrs=["X"])], {"https://outlet.test/report": page("https://outlet.test/report")},
                  llm=make_llm("supported", QUOTE, calls, source_type="secondary"), exa=exa_client("https://outlet.test/report"),
                  settings=with_model)
    assert f.calls == [] and a.verdict == "not_checkable" and "Quote could not be verified" in a.explanation
    assert len(calls) == 1          # only the domain-suggestion call, no verify call spent on another outlet


def test_html_is_fetched_off_the_critical_path_and_joined():
    urls = ["https://src.test/a", "https://src.test/b"]
    (a, b), f = run([claim(1, [urls[0]]), claim(2, [urls[1]])], {u: page(u) for u in urls})
    assert f.with_html_args == [False, False]                      # verification never waits for raw HTML
    assert a.best.fetch.html == "<html>https://src.test/a</html>"  # ... but it is there afterwards (B5)
    assert b.best.fetch.html == "<html>https://src.test/b</html>"


def test_claims_run_in_parallel():
    import threading
    import time
    active, peak, lock = [0], [0], threading.Lock()

    class SlowFetcher(FakeFetcher):
        def fetch_source(self, url, article_url, with_html=True):
            with lock:
                active[0] += 1
                peak[0] = max(peak[0], active[0])
            time.sleep(0.15)
            with lock:
                active[0] -= 1
            return super().fetch_source(url, article_url, with_html)
    urls = [f"https://src.test/{i}" for i in range(6)]
    f = SlowFetcher({u: page(u) for u in urls})
    assess_claims([claim(i, [u]) for i, u in enumerate(urls)], ART, fetcher=f, llm=make_llm(), settings=SETTINGS)
    assert peak[0] == 6


def test_deadline_turns_unfinished_claims_neutral_and_returns_on_time():
    import time
    fast_url, slow_url = "https://src.test/fast", "https://src.test/slow"

    class MixedFetcher(FakeFetcher):
        def fetch_source(self, url, article_url, with_html=True):
            if url == slow_url:
                time.sleep(3)
            return super().fetch_source(url, article_url, with_html)
    f = MixedFetcher({fast_url: page(fast_url), slow_url: page(slow_url)})
    quick = Settings(_env_file=None, model_verify="vm", exa_api_key="exa", prompt_version="vT", claim_deadline_s=0.6)
    t = time.time()
    fast, slow = assess_claims([claim(1, [fast_url]), claim(2, [slow_url])], ART, fetcher=f, llm=make_llm(), settings=quick)
    assert time.time() - t < 2.0                      # did not wait the 3 s
    assert fast.verdict == "supported"
    assert slow.verdict == "not_checkable" and "too long" in slow.explanation and slow.evidence is None


def test_slow_html_fetch_does_not_block_the_result():
    import time

    class SlowHtml(FakeFetcher):
        def fetch_html(self, url):
            time.sleep(5)
            return "<html/>"
    f = SlowHtml({"https://src.test/a": page("https://src.test/a")})
    t = time.time()
    quick = Settings(_env_file=None, model_verify="vm", prompt_version="vT", claim_deadline_s=1.5)
    (a,) = assess_claims([claim(1, ["https://src.test/a"])], ART, fetcher=f, llm=make_llm(), settings=quick)
    assert time.time() - t < 3.5 and a.verdict == "supported" and a.best.fetch.html is None   # skipped, neutral


def test_paragraph_scope_link_can_confirm_but_never_count_against():
    from app.pipeline.textutil import LinkRef
    url = "https://src.test/x"

    def loose_claim(i):
        return ExtractedClaim(f"c{i}", f"Claim number {i} text.", 0, 10, [LinkRef(url, "a", "paragraph")], [])
    for verdict in ("not_mentioned", "distorted", "contradicted"):
        (a,), _ = run([loose_claim(1)], {url: page(url)}, llm=make_llm(verdict, QUOTE if verdict != "not_mentioned" else ""))
        assert a.verdict == "not_checkable" and "not held against" in a.explanation, verdict
    (a,), _ = run([loose_claim(1)], {url: page(url)}, llm=make_llm("supported", QUOTE))
    assert a.verdict == "supported"
    (a,), _ = run([claim(1, [url])], {url: page(url)}, llm=make_llm("not_mentioned", ""))   # sentence-scope: still counts
    assert a.verdict == "not_mentioned"


def test_named_candidates_capped_to_bound_cost():
    urls = [f"https://n{i}.test/a" for i in range(8)]
    calls = []
    res, _ = run([claim(1, attrs=["A", "B"])], {u: page(u) for u in urls},
                 llm=make_llm("supported", QUOTE, calls, source_type="secondary"),
                 exa=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"results": [{"url": u} for u in urls]}))),
                 settings=SETTINGS_ALL)
    assert len(calls) <= 4 and res[0].verdict == "not_checkable"


def test_named_source_not_found_is_neutral_not_unsourced():
    (a,), _ = run([claim(1, attrs=["Zelenskyy"])], {}, exa=exa_client(None))
    assert a.verdict == "not_checkable" and "Zelenskyy" in a.explanation


def test_named_source_that_does_not_mention_claim_is_neutral():
    (a,), _ = run([claim(1, attrs=["X"])], {"https://www.orig.gov.test/interview": page("https://www.orig.gov.test/interview")},
                  llm=make_llm("not_mentioned", ""), exa=exa_client())
    assert a.verdict == "not_checkable"     # an Exa guess proves nothing about the article


# ------------------------------------------------------------------------------- self-citation chain (source of the source)
SELF_URL = "https://news.test/earlier-piece"
CHAIN_URL = "https://agency.test/report"
SELF_TEXT = (f"Earlier we reported that [the new programme will expand coverage to 40 districts by 2027]({CHAIN_URL}), "
             "a figure that officials repeated.")


def _chain_llm(calls):
    """Supported only for the chain source's text; the self page itself is only read to locate the passage."""
    def handler(request):
        body = json.loads(request.content)["messages"][-1]["content"]
        calls.append(body[:0])
        c = json.dumps({"quote_original": QUOTE, "quote_translation": None, "language": "en", "verdict": "supported",
                        "explanation": "why.", "source_type": "original"})
        return httpx.Response(200, json={"model": "vm", "choices": [{"message": {"content": c}}]})
    return LLMClient("https://r.test/v1", "k", retries=0, client=httpx.Client(transport=httpx.MockTransport(handler)))


def test_self_citation_source_of_the_source_can_support_the_claim():
    pages = {SELF_URL: page(SELF_URL, kind="self", text=SELF_TEXT, subtype="self_citation"), CHAIN_URL: page(CHAIN_URL)}
    (a,), f = run([claim(1, [SELF_URL])], pages, llm=_chain_llm([]))
    assert a.verdict == "supported" and a.best.url == CHAIN_URL
    head, d2 = a.sources
    assert head.url == SELF_URL and head.kind == "self" and head.verdict == "not_checkable" and head.depth == 1
    assert d2.url == CHAIN_URL and d2.depth == 2 and d2.parent_url == SELF_URL
    assert "own earlier piece" in a.explanation and a.subtype is None


def test_self_citation_without_chain_stays_neutral_self_citation():
    pages = {SELF_URL: page(SELF_URL, kind="self", text="No outbound links here at all.", subtype="self_citation")}
    (a,), _ = run([claim(1, [SELF_URL])], pages, llm=_chain_llm([]))
    assert a.verdict == "not_checkable" and a.subtype == "self_citation" and len(a.sources) == 1


def test_chain_never_follows_same_site_or_homepage_links():
    text = (f"The new programme will expand coverage to 40 districts by 2027, says [us](https://news.test/third) "
            "and [the agency](https://agency.test/).")
    pages = {SELF_URL: page(SELF_URL, kind="self", text=text, subtype="self_citation")}
    (a,), f = run([claim(1, [SELF_URL])], pages, llm=_chain_llm([]))
    assert len(a.sources) == 1 and f.calls == [SELF_URL]


def test_chain_source_that_does_not_support_never_counts_against_the_article():
    pages = {SELF_URL: page(SELF_URL, kind="self", text=SELF_TEXT, subtype="self_citation"), CHAIN_URL: page(CHAIN_URL, text="Unrelated text about weather.")}
    calls = []

    def handler(request):
        content = json.loads(request.content)["messages"][-1]["content"]
        verdict = "supported" if "Earlier we reported" in content else "contradicted"
        c = json.dumps({"quote_original": QUOTE if verdict == "supported" else None, "quote_translation": None, "language": "en",
                        "verdict": verdict, "explanation": "x.", "source_type": "original"})
        return httpx.Response(200, json={"model": "vm", "choices": [{"message": {"content": c}}]})
    llm = LLMClient("https://r.test/v1", "k", retries=0, client=httpx.Client(transport=httpx.MockTransport(handler)))
    (a,), _ = run([claim(1, [SELF_URL])], pages, llm=llm)
    assert a.verdict not in ("contradicted", "distorted")


# ------------------------------------------------------------------------------------ unsourced claims: search anyway
def _planner_llm(verdict="supported"):
    from app.pipeline.evidence import PLAN_PROMPT

    def handler(request):
        msgs = json.loads(request.content)["messages"]
        if msgs[0]["content"] == PLAN_PROMPT:
            c = json.dumps({"speaker": None, "venue": None, "occasion": None, "queries": ["coverage 40 districts 2027"], "phrases": []})
        else:
            c = json.dumps({"quote_original": QUOTE if verdict == "supported" else None, "quote_translation": None, "language": "en",
                            "verdict": verdict, "explanation": "why.", "source_type": "original"})
        return httpx.Response(200, json={"model": "vm", "choices": [{"message": {"content": c}}]})
    return LLMClient("https://r.test/v1", "k", retries=0, client=httpx.Client(transport=httpx.MockTransport(handler)))


SEARCH = Settings(_env_file=None, model_verify="vm", exa_api_key="exa", fuzzy_threshold=90, prompt_version="vT",
                  search_unsourced=True, exa_agent=False)
FOUND = "https://www.orig.gov.test/interview"


def test_unsourced_claim_with_a_verified_found_page_is_supported_and_says_the_article_gave_no_source():
    (a,), _ = run([claim(1)], {FOUND: page(FOUND)}, llm=_planner_llm(), exa=exa_client(FOUND), settings=SEARCH)
    assert a.verdict == "supported" and a.best.url == FOUND and a.evidence is not None and a.evidence.quote_original == QUOTE
    assert [s.url for s in a.sources] == [FOUND] and a.sources[0].kind == "named"
    assert a.explanation.startswith("The article gives no source for this claim.") and "gov.test" in a.explanation


def test_unsourced_found_page_that_does_not_support_is_ignored():
    (a,), _ = run([claim(1)], {FOUND: page(FOUND)}, llm=_planner_llm("not_mentioned"), exa=exa_client(FOUND), settings=SEARCH)
    assert a.verdict == "unsourced" and a.sources == [] and a.best is None


def test_unsourced_search_off_makes_no_calls():
    (a,), f = run([claim(1)], {}, settings=SETTINGS)
    assert a.verdict == "unsourced" and f.calls == []


# --------------------------------------------------- one found page checked against the other unsourced claims
def _two_claims_llm(c2_verdict="supported"):
    from app.pipeline.evidence import PLAN_PROMPT

    def handler(request):
        msgs = json.loads(request.content)["messages"]
        n = "2" if "Claim number 2" in msgs[1]["content"] else "1"
        if msgs[0]["content"] == PLAN_PROMPT:
            c = json.dumps({"speaker": None, "venue": None, "occasion": None, "queries": [f"only-claim-{n}"], "phrases": []})
        else:
            verdict = c2_verdict if n == "2" else "supported"
            c = json.dumps({"quote_original": QUOTE if verdict == "supported" else None, "quote_translation": None, "language": "en",
                            "verdict": verdict, "explanation": "why.", "source_type": "original"})
        return httpx.Response(200, json={"model": "vm", "choices": [{"message": {"content": c}}]})
    return LLMClient("https://r.test/v1", "k", retries=0, client=httpx.Client(transport=httpx.MockTransport(handler)))


def _exa_only_for_claim_1():
    def handler(request):
        found = "Claim number 1" in request.content.decode()
        return httpx.Response(200, json={"results": [{"url": FOUND, "title": "T"}] if found else []})
    return httpx.Client(transport=httpx.MockTransport(handler))


def _run_two(c2_verdict="supported", settings=SEARCH):
    f = FakeFetcher({FOUND: page(FOUND)})
    announced = []
    res = assess_claims([claim(1), claim(2)], ART, fetcher=f, llm=_two_claims_llm(c2_verdict), settings=settings,
                        exa_client=_exa_only_for_claim_1(), on_claim=lambda i, a: announced.append((i, a.verdict)))
    return res, f, announced


def test_a_page_found_for_one_unsourced_claim_also_supports_another_one():
    (a1, a2), f, announced = _run_two()
    assert a1.verdict == "supported" and a2.verdict == "supported"          # the search found nothing for claim 2 itself
    assert a2.best.url == FOUND and a2.evidence is not None and a2.evidence.fuzzy_score >= 90
    assert f.calls.count(FOUND) == 1                                         # the page is read once, then only verified
    assert sorted(announced) == [(0, "supported"), (1, "supported")]         # every claim announced once, already final


def test_the_other_claim_stays_unsourced_when_the_found_page_does_not_support_it():
    (a1, a2), _, announced = _run_two("not_mentioned")
    assert a1.verdict == "supported" and a2.verdict == "unsourced" and a2.sources == []     # never held against the article
    assert sorted(announced) == [(0, "supported"), (1, "unsourced")]


def test_cross_check_can_be_switched_off():
    off = Settings(**{**SEARCH.model_dump(), "cross_check_found": False})
    (a1, a2), _, announced = _run_two(settings=off)
    assert (a1.verdict, a2.verdict) == ("supported", "unsourced")
    assert sorted(announced) == [(0, "supported"), (1, "unsourced")]


def test_cross_check_never_touches_claims_that_have_a_link_or_an_attribution():
    f = FakeFetcher({FOUND: page(FOUND), "https://src.test/x": page("https://src.test/x", text="Nothing relevant here at all, sorry.")})
    res = assess_claims([claim(1), claim(2, links=["https://src.test/x"])], ART, fetcher=f, llm=_two_claims_llm("supported"),
                        settings=SEARCH, exa_client=_exa_only_for_claim_1())
    assert res[0].verdict == "supported" and res[1].best.url == "https://src.test/x"


def test_homepage_link_is_loose_and_neutral_on_not_mentioned():
    (a,), _ = run(
        [claim(1, ["https://example.org/"])],
        {"https://example.org/": page("https://example.org/")},
        llm=make_llm("not_mentioned", ""),
    )
    assert a.verdict == "not_checkable" and "not cited for this exact claim" in a.explanation


def test_neutral_linked_source_falls_back_to_attributed_search():
    def handler(request):
        user = json.loads(request.content)["messages"][1]["content"]
        if "SOURCE:" not in user:
            c = json.dumps({"speaker": "Finnish Ministry", "venue": None, "occasion": None, "queries": ["ministry-q"], "phrases": []})
        else:
            c = json.dumps({"quote_original": QUOTE, "quote_translation": None, "language": "en",
                            "verdict": "supported", "explanation": "ok.", "source_type": "original"})
        return httpx.Response(200, json={"model": "vm", "choices": [{"message": {"content": c}}]})

    llm = LLMClient("https://r.test/v1", "k", retries=0, client=httpx.Client(transport=httpx.MockTransport(handler)))
    pages = {
        "https://t.co/abc123": page("https://t.co/abc123", status="not_fetched", kind="social", subtype="social_media"),
        "https://www.orig.gov.test/interview": page("https://www.orig.gov.test/interview"),
    }
    (a,), _ = run([claim(1, links=["https://t.co/abc123"], attrs=["Finnish Ministry"])], pages, llm=llm, exa=exa_client())
    assert a.verdict == "supported" and a.best.url == "https://www.orig.gov.test/interview"


def test_cross_check_checks_unsourced_against_supported_linked_source():
    c1 = claim(1, links=["https://src.test/study"])
    c2 = claim(2)
    f = FakeFetcher({"https://src.test/study": page("https://src.test/study")})
    res = assess_claims([c1, c2], ART, fetcher=f, llm=_two_claims_llm("supported"), settings=SEARCH,
                        exa_client=exa_client(url=None))
    assert res[0].verdict == "supported" and res[1].verdict == "supported"
    assert res[1].best.url == "https://src.test/study"


def test_cross_check_attributed_reuses_same_speaker_source_across_claims():
    orig_url = "https://www.orig.gov.test/paper"

    def handler(request):
        user = json.loads(request.content)["messages"][1]["content"]
        if "SOURCE:" not in user:
            q = "query-miss-claim-1" if "Claim number 1" in user else "query-hit-claim-2"
            c = json.dumps({"speaker": "Sarah Betti", "venue": None, "occasion": None, "queries": [q], "phrases": []})
        else:
            c = json.dumps({"quote_original": QUOTE, "quote_translation": None, "language": "en",
                            "verdict": "supported", "explanation": "ok.", "source_type": "original"})
        return httpx.Response(200, json={"model": "vm", "choices": [{"message": {"content": c}}]})

    def exa_handler(request):
        hit = "query-hit-claim-2" in request.content.decode()
        return httpx.Response(200, json={"results": [{"url": orig_url, "title": "Paper"}] if hit else []})

    llm = LLMClient("https://r.test/v1", "k", retries=0, client=httpx.Client(transport=httpx.MockTransport(handler)))
    exa = httpx.Client(transport=httpx.MockTransport(exa_handler))
    f = FakeFetcher({orig_url: page(orig_url)})
    res = assess_claims(
        [claim(1, attrs=["Dr. Sarah Betti"]), claim(2, attrs=["Sarah Betti"])],
        ART, fetcher=f, llm=llm, settings=Settings(**{**SEARCH.model_dump(), "model_extract": "mx", "verify_secondary": True}), exa_client=exa,
    )
    assert res[0].verdict == "supported" and res[0].best.url == orig_url
    assert res[1].verdict == "supported" and res[1].best.url == orig_url
