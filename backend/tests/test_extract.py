import json
from pathlib import Path

import httpx
import pytest

from app.config import Settings
from app.llm import LLMClient, LLMError, parse_json_loose
from app.pipeline.exa import resolve_named_source
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
