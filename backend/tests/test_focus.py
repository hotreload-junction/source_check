"""F6: optional reader question. Offline: fake AI router, fixtures from test_run."""
import json

import httpx
import pytest

import tests.test_run as tr
from app.api import cache_key
from app.llm import LLMClient
from app.pipeline.focus import MAX_QUESTION_CHARS, clean_question
from app.pipeline.run import analyze

STAGED = "Western officials have privately admitted that the drone attacks were staged"
MINISTRY = "The ministry said on Tuesday that the new programme will expand coverage to 40 districts by 2027"


def router(extract, focus_answer, calls=None):
    """extraction -> sentence ids; focus prompt -> `focus_answer(user)` (list of fixture texts, or a raw dict/str);
    verify -> tr.verifier."""
    def handler(request):
        body = json.loads(request.content)
        sysmsg, user = body["messages"][0]["content"], body["messages"][1]["content"]
        if "find WHERE the article makes the assertion" in sysmsg:
            if calls is not None:
                calls.append(user)
            ans = focus_answer(user)
            content = ans if isinstance(ans, str) else json.dumps(
                ans if isinstance(ans, dict) else {"claims": tr.ids_for(user, [{"text": t} for t in ans])})
        elif "CHECKABLE FACTUAL CLAIMS" in sysmsg:
            content = json.dumps({"claims": tr.ids_for(user, extract)})
        elif "check whether a SOURCE" in sysmsg:
            content = json.dumps(tr.verifier(user))
        else:
            content = json.dumps({"domains": [], "query": ""})
        return httpx.Response(200, json={"model": "m", "choices": [{"finish_reason": "stop", "message": {"content": content}}]})
    return LLMClient("https://r.test/v1", "k", retries=0, backoff_s=0, client=httpx.Client(transport=httpx.MockTransport(handler)))


def go(pages, *, question=None, extract=tr.EXTRACT, focus_answer=lambda u: [], calls=None, events=None):
    return analyze(tr.ARTICLE_URL, fetcher=tr.FakeFetcher(tr.art(), pages), llm=router(extract, focus_answer, calls),
                   settings=tr.SETTINGS, question=question,
                   rdap=tr.FakeRdap({"news-front.net": 55, "ministry.gov.uk": 3000, "pravda-mirror.com": 1000}),
                   emit=(lambda e, d: events.append((e, d))) if events is not None else None).result


@pytest.fixture
def pages():
    return tr.pages.__wrapped__()


def test_no_question_means_no_focus_and_no_focus_call(pages):
    calls = []
    r = go(pages, calls=calls)
    assert r["focus"] is None and calls == [] and r["audit"]["focus_prompt_version"] is None
    assert all(c["central"] and not c["focus"] for c in r["claims"])


def test_question_on_a_central_claim_marks_it_and_changes_nothing_else(pages):
    plain = go(pages)
    calls = []
    r = go(pages, question="Is the 40 districts figure real?", focus_answer=lambda u: [MINISTRY], calls=calls)
    assert len(calls) == tr.SETTINGS.extract_votes                    # majority vote like extraction
    assert "Is the 40 districts figure real?" in calls[0]
    assert r["focus"]["status"] == "found" and r["focus"]["claim_ids"] == ["c2"]
    c2 = r["claims"][1]
    assert c2["focus"] and c2["central"] and c2["verdict"] == "supported"
    assert len(r["claims"]) == len(plain["claims"])                   # no duplicate claim for a central sentence
    assert r["verdict"] == plain["verdict"] and r["signals"] == plain["signals"]
    assert "central claims" in r["focus"]["note"] and r["audit"]["focus_prompt_version"] == "f1"


def test_focus_only_claim_is_checked_but_never_changes_the_label(pages):
    """The 'staged' sentence is NOT central here; its source shares GA ID G-SAME0001 with the article and would fire the
    infrastructure signal. Asked about by the reader, it is checked and shown, and the article label stays the same."""
    central_only = [e for e in tr.EXTRACT if STAGED not in e["text"]]
    plain = go(pages, extract=central_only)
    ev = []
    r = go(pages, extract=central_only, question="Did officials really admit the attacks were staged?",
           focus_answer=lambda u: [STAGED], events=ev)
    extra = r["claims"][-1]
    assert extra["id"] == "c3" and extra["focus"] and not extra["central"] and extra["text"].startswith("Western officials")
    assert extra["verdict"] == "not_mentioned" and extra["source_ids"]          # fully checked against its cited source
    assert r["focus"]["claim_ids"] == ["c3"] and "does not change the article verdict" in r["focus"]["note"]
    assert r["verdict"] == plain["verdict"] and r["signals"] == plain["signals"]
    assert r["signals"]["infrastructure"] == plain["signals"]["infrastructure"]
    assert "news-front.net" not in {d["domain"] for d in r["domains"]}         # no domain node, no shared-ID cluster
    assert [c["id"] for c in r["claims"][:2]] == [c["id"] for c in plain["claims"]]
    assert any(e == "progress" and d["step"] == "focus" for e, d in ev)
    assert any(e == "claim" and d["id"] == "c3" and d["focus"] for e, d in ev)


def test_question_not_in_the_article_says_so_honestly(pages):
    r = go(pages, question="Did the president resign?", focus_answer=lambda u: [])
    assert r["focus"] == {"question": "Did the president resign?", "status": "not_in_article", "claim_ids": [],
                          "note": "The article does not contain a statement that matches your question, so there is nothing in it to check."}
    assert not any(c["focus"] for c in r["claims"])


def test_failed_focus_model_is_reported_and_the_analysis_still_runs(pages):
    r = go(pages, question="Is this true?", focus_answer=lambda u: "not json at all")
    assert r["focus"]["status"] == "failed" and r["focus"]["claim_ids"] == []
    assert r["verdict"] == go(pages)["verdict"]


def test_invalid_sentence_numbers_and_minority_votes_are_ignored(pages):
    n = [0]

    def answer(user):        # 2 of 5 readings name the ministry sentence (minority), all name a number out of range
        n[0] += 1
        ids = tr.ids_for(user, [{"text": MINISTRY}]) if n[0] <= 2 else []
        return {"claims": ids + [{"id": 999, "attributions": []}, {"id": "x"}]}
    r = go(pages, question="?", focus_answer=answer)
    assert r["focus"]["status"] == "not_in_article"


def test_at_most_two_focus_sentences(pages):
    texts = [e["text"] for e in tr.EXTRACT]
    r = go(pages, question="all of it?", focus_answer=lambda u: texts)
    assert len(r["focus"]["claim_ids"]) == 2


def test_clean_question():
    assert clean_question(None) is None and clean_question("   \n ") is None
    assert clean_question("  Is   this\n real? ") == "Is this real?"
    with pytest.raises(ValueError):
        clean_question("x" * (MAX_QUESTION_CHARS + 1))


def test_cache_key_separates_questions():
    norm = "https://a.example/story"
    assert cache_key(norm, None) == norm
    assert cache_key(norm, "Is it true?") == cache_key(norm, "is it TRUE?") != cache_key(norm, "Other?")
    assert cache_key(norm, "Is it true?").startswith(norm + "#q=")
    assert len(cache_key("https://a.example/" + "x" * 600, "q")) <= 512


def test_api_rejects_a_too_long_question(client):
    r = client.post("/api/analyses", json={"url": "https://a.example/story", "question": "x" * (MAX_QUESTION_CHARS + 1)})
    assert r.status_code == 422 and r.json()["error"] == "invalid_question"
