"""F10 browser extension: the page as the reader's browser renders it (POST /api/analyses/page) and the selected
passage as the focus claim. Offline: fake AI router, fixtures from test_run / test_focus."""
import pytest

import test_jobs as tj
import tests.test_focus as tf
import tests.test_run as tr
from app.fetch.page import page_to_article
from app.pipeline.focus import clean_selection, match_selection
from app.pipeline.run import analyze
from app.pipeline.textutil import sentence_units

PAGE_URL = "https://news.example.org/2026/11/ministry-plan"
PARA = ("The ministry said on Tuesday that the new programme will expand coverage to 40 districts by 2027, "
        '<a href="https://ministry.gov.uk/report">the official report</a> states. ')
HTML = ("<html><head><title>Ministry expands programme | Example News</title>"
        '<meta property="article:published_time" content="2026-11-12T08:00:00Z">'
        "<script>gtag('config', 'G-PAGE0001')</script></head><body>"
        "<nav><a href='/'>Home</a> <a href='/world'>World</a></nav>"
        "<article><h1>Ministry expands programme</h1>"
        f"<p>{PARA * 2}</p>"
        "<p>Local residents saw no damage at all in the district after the alleged attack last week, the mayor told reporters on Monday.</p>"
        "<p>Critics said the plan was announced without any budget and that previous programmes were never completed on time.</p>"
        "</article><footer>Copyright Example News</footer></body></html>")


def test_page_html_becomes_markdown_with_links_date_and_raw_html_kept():
    art = page_to_article(PAGE_URL, HTML, "Ministry expands programme")
    assert art.status == "ok" and art.via == "browser" and art.final_url == PAGE_URL
    assert "[the official report](https://ministry.gov.uk/report)" in art.text
    assert art.published_at == "2026-11-12T08:00:00Z" and "G-PAGE0001" in art.html      # infra signals read the HTML
    assert art.title == "Ministry expands programme"


def test_page_without_article_text_is_refused_with_a_reason():
    art = page_to_article(PAGE_URL, "<html><body><p>Log in</p></body></html>")
    assert art.status != "ok" and "no article text" in art.error


ARTICLE_UNITS = sentence_units(tr.ARTICLE)


def test_selection_inside_a_sentence_ignores_markdown_quotes_and_spacing():
    # the browser shows link text without markdown, the selection is part of the sentence, spacing differs
    hit = match_selection("expand coverage to 40 districts   by 2027, the official report states", ARTICLE_UNITS)
    assert [ARTICLE_UNITS[i][2][:20] for i in hit] == ["The ministry said on"]


def test_selection_spanning_two_sentences_gives_both_in_document_order():
    sel = ("Western officials have privately admitted that the drone attacks were staged, according to a report seen by our "
           "editors. The ministry said on Tuesday that the new programme will expand coverage to 40 districts by 2027, "
           "the official report states.")
    hit = match_selection(sel, ARTICLE_UNITS)
    assert [ARTICLE_UNITS[i][2][:12] for i in hit] == ["Western offi", "The ministry"]


def test_selection_not_in_the_text_matches_nothing():
    assert match_selection("The president resigned on Friday after the vote.", ARTICLE_UNITS) == []


def test_selection_length_limits():
    assert clean_selection("  ") is None and clean_selection(None) is None
    with pytest.raises(ValueError):
        clean_selection("minister")
    with pytest.raises(ValueError):
        clean_selection("x" * 1501)


def go(pages, *, selection=None, extract=tr.EXTRACT, focus_answer=lambda u: [], calls=None):
    return analyze(tr.ARTICLE_URL, fetcher=tr.FakeFetcher(tr.art(), pages), llm=tf.router(extract, focus_answer, calls),
                   settings=tr.SETTINGS, selection=selection,
                   rdap=tr.FakeRdap({"news-front.net": 55, "ministry.gov.uk": 3000, "pravda-mirror.com": 1000})).result


def test_selected_passage_is_the_focus_claim_located_by_code_and_never_changes_the_label(pages):
    central_only = [e for e in tr.EXTRACT if tf.STAGED not in e["text"]]
    plain = go(pages, extract=central_only)
    # the model reading points elsewhere: code location wins, the model only supplies attributions
    r = go(pages, extract=central_only, selection="officials have privately admitted that the drone attacks were staged",
           focus_answer=lambda u: [tf.MINISTRY])
    assert r["focus"]["kind"] == "selection" and r["focus"]["status"] == "found"
    extra = r["claims"][-1]
    assert extra["text"].startswith("Western officials") and extra["focus"] and not extra["central"]
    assert r["focus"]["claim_ids"] == [extra["id"]] and "you selected" in r["focus"]["note"]
    assert r["verdict"] == plain["verdict"] and r["signals"] == plain["signals"]


def test_selected_central_claim_is_marked_not_duplicated(pages):
    plain = go(pages)
    r = go(pages, selection="the new programme will expand coverage to 40 districts by 2027")
    assert r["focus"]["claim_ids"] == ["c2"] and len(r["claims"]) == len(plain["claims"])
    assert "central claims" in r["focus"]["note"]


def test_selection_not_in_the_text_falls_back_to_the_model_reading_and_says_so(pages):
    r = go(pages, selection="The president resigned on Friday after the vote.", focus_answer=lambda u: [])
    assert r["focus"]["status"] == "not_in_article" and r["focus"]["kind"] == "selection"
    assert "selected was not found" in r["focus"]["note"]


# ---------------------------------------------------------------- API
class Rec(tj.Fake):
    def __init__(self, out):
        super().__init__(out)
        self.kw = []

    def __call__(self, url, **kw):
        self.kw.append((url, kw))
        return super().__call__(url, **kw)


def test_page_endpoint_runs_on_the_browser_text_and_keeps_its_own_cache(client, jobs):
    rec = jobs.analyze_fn = Rec(tj.real_output())
    r = client.post("/api/analyses/page", json={"url": PAGE_URL, "html": HTML, "title": "T"})
    assert r.status_code == 202, r.text
    job = r.json()["job_id"]
    assert tj.done(client, job)["status"] == "done"
    url, kw = rec.kw[0]
    assert url == PAGE_URL and kw["article"].via == "browser" and "selection" not in kw
    again = client.post("/api/analyses/page", json={"url": PAGE_URL, "html": HTML})
    assert again.json() == {"job_id": job, "status": "done", "cached": True}
    # a plain URL request never gets the page a reader sent (different cache key)
    jobs.analyze_fn = Rec(tj.real_output())
    plain = client.post("/api/analyses", json={"url": PAGE_URL})
    assert plain.status_code == 202 and plain.json()["job_id"] != job


def test_page_endpoint_passes_the_selection_and_validates_input(client, jobs):
    rec = jobs.analyze_fn = Rec(tj.real_output())
    sel = "the new programme will expand coverage to 40 districts by 2027"
    r = client.post("/api/analyses/page", json={"url": PAGE_URL, "html": HTML, "selection": sel})
    assert r.status_code == 202, r.text
    tj.done(client, r.json()["job_id"])
    assert rec.kw[0][1]["selection"] == sel
    bad = lambda **b: client.post("/api/analyses/page", json={"url": PAGE_URL, "html": HTML, **b})
    assert bad(selection="too short").json()["error"] == "invalid_question"
    assert bad(selection=sel, question="Is it true?").status_code == 422
    assert bad(url="http://localhost/x").json()["error"] == "invalid_url"
    assert client.post("/api/analyses/page", json={"url": PAGE_URL, "html": "<p>hi</p>"}).json()["error"] == "unreadable_page"


def test_extension_origins_may_call_the_api(client):
    r = client.options("/api/analyses/page", headers={"Origin": "chrome-extension://abcdefghijklmnop",
                                                      "Access-Control-Request-Method": "POST",
                                                      "Access-Control-Request-Headers": "content-type"})
    assert r.status_code == 200 and r.headers["access-control-allow-origin"] == "chrome-extension://abcdefghijklmnop"
    other = client.get("/api/health", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in other.headers


jobs = tj.jobs
pages = tr.pages
