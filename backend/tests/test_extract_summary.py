"""EXTRACT_MODE=summary: key points cite numbered sentences; one claim per key point; details fill the rest. Fake LLM, no network."""
import json

import httpx

from app.config import Settings
from app.llm import LLMClient
from app.pipeline.extract import _Reading, _parse_summary, extract_claims, select_by_summary
from app.pipeline.textutil import sentence_units

S = Settings(_env_file=None, model_extract="m", max_claims=6, summary_votes=3, summary_detail_slots=2)
SENT = [
    "The ministry said on Tuesday that the programme will cover 40 districts by 2027, [per its report](https://ministry.gov.uk/report).",   # 0
    "Officials told reporters that coverage will reach 40 districts by 2027.",                                                              # 1 same point as 0
    "Critics doubt the timeline, saying the budget falls short by 3 billion euros.",                                                         # 2
    "Sasha often felt lonely during her first year of motherhood, even when she was surrounded by people.",                                  # 3 anecdote
    "A 2024 audit found that 12 of 51 clinics lacked staff, the regulator reported.",                                                        # 4
    "The ministry has not commented on the audit.",                                                                                          # 5 no-comment line
    "Unemployment fell to 4.1 percent in March.",                                                                                            # 6 detail, unsourced
    "The programme costs 2.2 billion euros, according to the budget office.",                                                                # 7 detail, sourced
    "It is the largest expansion since 2009.",                                                                                               # 8
]
MD = "\n\n".join(SENT)


def units():
    return sentence_units(MD)


def index(prefix):
    return next(i for i, (_, _, t) in enumerate(units()) if t.startswith(prefix))


def router(readings):
    def handler(request):
        user = json.loads(request.content)["messages"][1]["content"]
        i = int(user.split("Reading ")[1].split(" of")[0]) - 1
        r = readings[i % len(readings)]
        return httpx.Response(200, json={"model": "m", "choices": [{"finish_reason": "stop", "message": {"content": r if isinstance(r, str) else json.dumps(r)}}],
                                         "usage": {"prompt_tokens": 10, "completion_tokens": 5}})
    return LLMClient("https://r.test/v1", "k", retries=0, backoff_s=0, client=httpx.Client(transport=httpx.MockTransport(handler)))


def reading(points, details=(), sources=()):
    return {"points": [{"point": "p", "sentences": ids} for ids in points], "details": list(details),
            "sources": [{"id": i, "attributions": a} for i, a in sources]}


def test_one_claim_per_key_point_most_central_first_and_exact_slices():
    a, b, c, d = index("The ministry said"), index("Officials told"), index("Critics"), index("A 2024 audit")
    rd = reading([[a, b], [c], [d]], sources=[(a, ["the ministry"]), (d, ["the regulator"])])
    res = extract_claims(MD, "https://news.test/a", llm=router([rd]), settings=S.model_copy(update={"summary_detail_slots": 0}))
    assert [x.text for x in res.claims] == [SENT[0], SENT[2], SENT[4]]          # the second sentence of key point 1 is not a claim
    assert all(MD[x.char_start:x.char_end] == x.text for x in res.claims)         # verbatim slices with exact offsets
    assert [x.id for x in res.claims] == ["c1", "c2", "c3"] and res.n_votes == 3
    assert res.claims[0].links and res.claims[0].attributions == ["the ministry"] and res.claims[2].attributions == ["the regulator"]
    assert res.claims[1].attributions == [] and res.claims[1].links == []


def test_a_sentence_needs_a_majority_of_the_readings_and_anecdotes_never_get_in():
    a, c, anecdote, d = index("The ministry said"), index("Critics"), index("Sasha"), index("A 2024 audit")
    readings = [reading([[a], [c]]), reading([[a], [d]]), reading([[a], [c], [anecdote]])]
    res = extract_claims(MD, "", llm=router(readings), settings=S.model_copy(update={"summary_detail_slots": 0}))
    assert [x.text for x in res.claims] == [SENT[0], SENT[2]]                  # c: 2 of 3 readings; d: 1 of 3; the anecdote: 1 of 3


def test_details_fill_the_reserved_slots_unsourced_first_and_no_comment_lines_are_dropped():
    a, c, d = index("The ministry said"), index("Critics"), index("A 2024 audit")
    unemp, cost, no_comment = index("Unemployment"), index("The programme costs"), index("The ministry has not")
    rd = reading([[a], [c], [d]], details=[cost, unemp, no_comment], sources=[(cost, ["the budget office"])])
    res = extract_claims(MD, "", llm=router([rd]), settings=S.model_copy(update={"max_claims": 5}))
    texts = [x.text for x in res.claims]
    assert texts == [SENT[0], SENT[2], SENT[4], SENT[6], SENT[7]]               # 3 key points + both details, document order
    res = extract_claims(MD, "", llm=router([rd]), settings=S.model_copy(update={"max_claims": 4, "summary_detail_slots": 1}))
    got = [x.text for x in res.claims]
    assert got == [SENT[0], SENT[2], SENT[4], SENT[6]]                          # one detail slot: the UNSOURCED figure beats the sourced one
    assert SENT[5] not in got                                                    # the no-comment line never counts


def test_near_duplicate_of_a_picked_claim_is_skipped():
    texts = {0: "The programme will cover 40 districts by 2027, the ministry said on Tuesday.",
             1: "The ministry said on Tuesday the programme will cover 40 districts by 2027.",
             2: "Unemployment fell to 4.1 percent in March."}
    r = _Reading(points=[[0], [1], [2]])
    keep, _, _ = select_by_summary([r, r, r], texts, lambda i: True, limit=6, detail_slots=0)
    assert keep == [0, 2]                                                        # key point 2 restates key point 1


def test_selection_is_deterministic_and_independent_of_the_reading_order():
    rs = [_Reading(points=[[1, 2], [5], [7]], details=[9]), _Reading(points=[[1], [5, 6], [7]], details=[9]), _Reading(points=[[1, 2], [7], [5]], details=[8])]
    texts = {i: f"sentence number {i} says something distinct about topic {i * 7}" for i in range(12)}
    a = select_by_summary(rs, texts, lambda i: True, limit=5, detail_slots=1)
    b = select_by_summary(list(reversed(rs)), texts, lambda i: True, limit=5, detail_slots=1)
    assert a[0] == b[0] and a[0] == sorted(a[0])


def test_garbage_summaries_fall_back_to_the_classifier_vote():
    a, c = index("The ministry said"), index("Critics")

    def handler(request):
        body = json.loads(request.content)
        system, user = body["messages"][0]["content"], body["messages"][1]["content"]
        if "KEY POINTS" in system:
            content = "not json at all"
        else:
            content = json.dumps({"claims": [{"id": a, "attributions": []}, {"id": c, "attributions": []}]})
        return httpx.Response(200, json={"model": "m", "choices": [{"finish_reason": "stop", "message": {"content": content}}], "usage": {}})
    llm = LLMClient("https://r.test/v1", "k", retries=0, backoff_s=0, client=httpx.Client(transport=httpx.MockTransport(handler)))
    res = extract_claims(MD, "", llm=llm, settings=S)
    assert [x.text for x in res.claims] == [SENT[0], SENT[2]]


def test_parse_summary_ignores_bad_ids_and_needs_a_point():
    r = _parse_summary({"points": [{"point": "x", "sentences": [1, "2", 99, -1, "z", 1]}, {"point": "y", "sentences": []}], "details": [3, 3, 500],
                        "sources": [{"id": 1, "attributions": [" X ", 5, ""]}, "junk", {"id": 77, "attributions": ["Z"]}]}, 10)
    assert r.points == [[1, 2]] and r.details == [3] and r.attrs == {1: ("X",)}
    import pytest
    from app.llm import LLMError
    for bad in ({"points": []}, {"points": [{"sentences": [99]}]}, {}):
        with pytest.raises(LLMError):
            _parse_summary(bad, 10)
