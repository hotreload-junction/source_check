"""AI overview (overview.py): plain-English summary of the finished check; code-written counts, guards, fallbacks. Display only."""
import json

import httpx
import pytest

import tests.test_run as tr
from app.config import Settings
from app.llm import LLMClient
from app.pipeline.overview import OVERVIEW_PROMPT_VERSION, check_text, count_sentence, write_overview

S = Settings(_env_file=None, model_extract="m", ai_overview=True)

CLAIMS = [
    {"id": "c1", "text": "Ship 42 launched on Flight 15 from Starbase.", "verdict": "supported", "central": True,
     "explanation": "Scientific American reports the launch from Starbase.", "source_ids": ["s1"], "found_source_ids": [],
     "source_checks": [{"source_id": "s1", "verdict": "supported", "page_verdict": "supported", "fuzzy_score": 98.0,
                        "quote_original": "Ship 42 lifted off on Flight 15 from Starbase", "quote_translation": None,
                        "explanation": "The page states the launch.", "note": None}]},
    {"id": "c2", "text": "SpaceX says all 33 engines relit.", "verdict": "distorted", "central": True,
     "explanation": "The source says 32 of 33 engines relit.", "source_ids": ["s2"], "found_source_ids": [],
     "source_checks": [{"source_id": "s2", "verdict": "distorted", "page_verdict": "distorted", "fuzzy_score": 95.0,
                        "quote_original": "32 of 33 engines relit for the boostback", "quote_translation": None,
                        "explanation": "32 of 33, not all.", "note": None}]},
    {"id": "c3", "text": "The booster was caught for the fourth time.", "verdict": "not_checkable", "central": True,
     "explanation": "The source could not be read (paywall).", "source_ids": ["s3"], "found_source_ids": [], "source_checks": []},
]
SOURCES = [{"id": "s1", "domain": "scientificamerican.com", "kind": "link", "attribution_text": None},
           {"id": "s2", "domain": "spacex.com", "kind": "link", "attribution_text": None},
           {"id": "s3", "domain": "paywalled.example", "kind": "link", "attribution_text": None}]
DECISION = {"verdict": {"label": "well_sourced", "summary": "No two independent warning signals fired."},
            "signals": {"evidence": {"fired": False, "claim_ids": [], "detail": "1 of 3"}, "infrastructure": {"fired": False, "reasons": []},
                        "circle": {"fired": False, "path": None}, "laundering": {"fired": False, "claim_ids": [], "reasons": []}}}

GOOD = {
    "article": [{"text": "The launch itself is confirmed by scientificamerican.com.", "claims": ["c1"]},
                {"text": "The engine figure is overstated: spacex.com says 32 of 33, not all 33.", "claims": ["c2"]}],
    "claims": {"c1": "Scientific American confirms that Ship 42 launched on Flight 15 from Starbase.",
               "c2": "The article says all 33 engines relit; spacex.com says 32 of 33.",
               "c3": "The only cited page is behind a paywall, so this could not be checked."},
}


def llm(answer, calls=None):
    def handler(request):
        if calls is not None:
            calls.append(json.loads(request.content)["messages"][1]["content"])
        if answer == "fail":
            return httpx.Response(500, json={})
        return httpx.Response(200, json={"model": "m", "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(answer)}}]})
    return LLMClient("https://r.test/v1", "k", retries=0, backoff_s=0, client=httpx.Client(transport=httpx.MockTransport(handler)))


def run(answer, claims=CLAIMS, settings=S, calls=None):
    return write_overview("Starship flight 15", claims, SOURCES, DECISION, llm=llm(answer, calls), settings=settings)


@pytest.fixture
def pages():
    return tr.pages.__wrapped__()


def test_counts_are_written_by_code():
    assert count_sentence(CLAIMS) == "Of 3 central claims: 1 supported by a source, 1 overstated or distorted, 1 could not be checked (neutral)."
    assert count_sentence([]) == "No central claims were found."
    focus_only = dict(CLAIMS[0], id="c9", central=False)
    assert count_sentence(CLAIMS + [focus_only]).startswith("Of 3 central claims")      # reader-question claims are not counted


def test_good_answer_is_shown_with_code_counts_first():
    calls = []
    ov = run(GOOD, calls=calls)
    assert ov.article["source"] == "ai" and ov.article["prompt_version"] == OVERVIEW_PROMPT_VERSION and ov.article["model"] == "m"
    assert ov.article["text"].startswith(count_sentence(CLAIMS) + " The launch itself")
    assert ov.claims == GOOD["claims"]
    assert len(calls) == 1 and "32 of 33 engines relit" in calls[0] and "paywall" in calls[0]


def test_invented_number_rejects_the_article_text_but_keeps_good_claim_texts():
    bad = {**GOOD, "article": [{"text": "The engine figure is overstated: 31 of 33 relit.", "claims": ["c2"]}]}
    ov = run(bad)
    assert ov.article["source"] == "rule" and ov.article["text"] == count_sentence(CLAIMS)
    assert ov.claims["c1"] == GOOD["claims"]["c1"]


def test_wording_outside_the_verdict_category_is_rejected_per_claim():
    bad = {**GOOD, "claims": {**GOOD["claims"], "c1": "The launch claim is misleading.", "c3": "This claim is false."}}
    ov = run(bad)
    assert ov.claims["c1"] is None and ov.claims["c3"] is None and ov.claims["c2"] == GOOD["claims"]["c2"]


def test_guard_rules():
    hay = "spacex.com 32 of 33 engines Starbase"
    assert check_text("Spacex.com says 32 of 33.", hay, {"distorted"}, "well_sourced", 400) is None
    assert "not in the input" in check_text("The page cites NASA data.", hay, {"distorted"}, "well_sourced", 400)
    assert "loaded word" in check_text("This is propaganda.", hay, {"distorted"}, "weakly_sourced", 400)
    assert check_text("It fits a propaganda pattern.", hay, set(), "propaganda_pattern", 400) is None
    assert "claim id" in check_text("As c2 shows, the number differs.", hay, {"distorted"}, "well_sourced", 400)
    assert "does not fit" in check_text("The claim is accurate.", hay, {"not_mentioned"}, "well_sourced", 400)
    assert check_text("x" * 401, hay, set(), "well_sourced", 400) == "too long"


def test_unknown_or_question_only_tag_rejects_the_article_text():
    focus_only = dict(CLAIMS[0], id="c4", central=False)
    for tags in (["c7"], ["c4"]):
        bad = {**GOOD, "article": [{"text": "The launch is confirmed by scientificamerican.com.", "claims": tags}]}
        assert run(bad, claims=CLAIMS + [focus_only]).article["source"] == "rule"


def test_failure_or_switch_off_falls_back_without_raising():
    calls = []
    off = Settings(_env_file=None, model_extract="m", ai_overview=False)
    for ov in (run("fail"), run({"unexpected": 1}), run(GOOD, settings=off, calls=calls)):
        assert ov.article == {"text": count_sentence(CLAIMS), "source": "rule", "model": None, "prompt_version": OVERVIEW_PROMPT_VERSION}
        assert set(ov.claims) == {"c1", "c2", "c3"} and all(v is None for v in ov.claims.values())
    assert calls == []                                   # switched off: no model call


def test_analysis_carries_overview_and_label_is_unchanged(pages):
    r = tr.run(pages).result                              # the fake router answers the overview call with junk -> rule text
    assert r["overview"]["source"] == "rule" and r["overview"]["text"].startswith("Of 3 central claims")
    assert all("overview" in c and c["overview"] is None for c in r["claims"])
    assert r["audit"]["overview_prompt_version"] == OVERVIEW_PROMPT_VERSION
    off = Settings(**{**tr.SETTINGS.model_dump(), "ai_overview": False})
    r_off = tr.analyze(tr.ARTICLE_URL, fetcher=tr.FakeFetcher(tr.art(), pages), llm=tr.router(tr.EXTRACT, tr.verifier), settings=off,
                       rdap=tr.FakeRdap({"news-front.net": 55, "ministry.gov.uk": 3000, "pravda-mirror.com": 1000})).result
    assert r_off["verdict"] == r["verdict"] and r_off["signals"] == r["signals"]
