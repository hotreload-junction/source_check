"""Self-contained check sentence (statement.py): minimal rewrite when a sentence needs its context, guards, not-checkable refill."""
import json

import httpx

from app.config import Settings
from app.llm import LLMClient
from app.pipeline.extract import ExtractedClaim, ExtractionResult
from app.pipeline.statement import contextualize, guard
from app.pipeline.textutil import sentence_units

S = Settings(_env_file=None, model_extract="m", contextualize_claims=True)
SENT = [
    "The Global Risks Report 2025 presents the findings of a survey of over 900 experts worldwide.",      # 0
    "A majority of respondents (52%) anticipate an unsettled outlook over the next two years.",           # 1
    "Another 31% expect turbulence and 5% a stormy outlook.",                                              # 2 needs context
    "Sasha often felt lonely during her first year of motherhood.",                                        # 3 anecdote
    "Unemployment fell to 4.1 percent in March, the statistics office said.",                              # 4
    "Critics doubt the timeline, saying the budget falls short by 3 billion euros.",                       # 5
    "The ministry plans to open 40 new clinics by 2027.",                                                  # 6
]
MD = "\n\n".join(SENT)
UNITS = sentence_units(MD)


def idx(prefix):
    return next(i for i, (_, _, t) in enumerate(UNITS) if t.startswith(prefix))


def claim(i, n, attrs=()):
    st, en, _ = UNITS[i]
    return ExtractedClaim(f"c{n}", MD[st:en], st, en, [], list(attrs), votes=3, context=MD[st:en])


def llm_by_sentence(answers, seen=None):
    """answers: {sentence prefix: dict}; the model is asked per SENTENCE."""
    def handler(request):
        user = json.loads(request.content)["messages"][1]["content"]
        sentence = user.split("SENTENCE: ")[1].split("\n")[0]
        if seen is not None:
            seen.append(sentence)
        a = next((v for k, v in answers.items() if sentence.startswith(k)), {"needs_context": False, "statement": sentence, "checkable": True})
        if a == "fail":
            return httpx.Response(500, json={})
        return httpx.Response(200, json={"model": "m", "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(a)}}]})
    return LLMClient("https://r.test/v1", "k", retries=0, backoff_s=0, client=httpx.Client(transport=httpx.MockTransport(handler)))


def result(picks, alternates=(), alt_attrs=None):
    return ExtractionResult([claim(idx(p), n + 1) for n, p in enumerate(picks)], "m", units=UNITS,
                            alternates=[idx(p) for p in alternates], alternate_attrs={idx(k): v for k, v in (alt_attrs or {}).items()})


# ------------------------------------------------------------------ guard
def test_guard_accepts_a_minimal_edit_and_rejects_invention_loss_and_bloat():
    orig = "Another 31% expect turbulence and 5% a stormy outlook."
    article = "Global Risks Report 2025 survey. " + orig
    assert guard(orig, "In the Global Risks Report 2025 survey, another 31% expect turbulence and 5% a stormy outlook.", article) is None
    assert "not in the article" in guard(orig, "In the World Economic Forum survey, another 31% expect turbulence and 5% a stormy outlook.", article)
    assert "not in the article" in guard(orig, "Another 32% expect turbulence and 5% a stormy outlook.", article)           # a changed number
    assert "drops too much" in guard(orig, "Turbulence is expected.", article)
    assert "too long" in guard(orig, orig + " " + "x" * 300, article)
    assert guard(orig, "", article) == "empty"
    assert guard("Coke Zero has no calories.", "Coke Zero's maker says Coke Zero has no calories.", "Coke Zero has no calories") is None   # possessive of a known name


# ------------------------------------------------------------------ contextualize
def test_a_sentence_that_needs_context_gets_a_check_text_and_the_rest_is_left_alone():
    stmt = "In the Global Risks Report 2025 survey, another 31% expect turbulence and 5% a stormy outlook."
    seen = []
    ex = contextualize(result(["A majority", "Another 31%", "Unemployment"]), MD, "Global Risks Report 2025",
                       llm=llm_by_sentence({"Another 31%": {"needs_context": True, "statement": stmt, "checkable": True}}, seen), settings=S)
    assert [c.check_text for c in ex.claims] == [None, stmt, None]
    assert [c.text for c in ex.claims] == [SENT[1], SENT[2], SENT[4]]                       # what is shown never changes
    assert [c.id for c in ex.claims] == ["c1", "c2", "c3"] and len(seen) == 3


def test_a_rewrite_that_fails_the_guard_or_changes_nothing_is_not_used():
    bad = "In the World Economic Forum survey, another 31% expect turbulence and 5% a stormy outlook."
    ex = contextualize(result(["Another 31%"]), MD, "t", llm=llm_by_sentence({"Another 31%": {"needs_context": True, "statement": bad, "checkable": True}}), settings=S)
    assert ex.claims[0].check_text is None
    same = contextualize(result(["Another 31%"]), MD, "t", llm=llm_by_sentence({"Another 31%": {"needs_context": True, "statement": SENT[2], "checkable": True}}), settings=S)
    assert same.claims[0].check_text is None


def test_a_claim_that_is_not_checkable_is_replaced_by_the_next_best_candidate_with_its_attributions():
    ex = contextualize(result(["A majority", "Sasha", "Unemployment"], alternates=["Critics", "The ministry plans"], alt_attrs={"Critics": ["Critics"]}),
                       MD, "t", llm=llm_by_sentence({"Sasha": {"needs_context": False, "statement": SENT[3], "checkable": False}}), settings=S)
    assert [c.text for c in ex.claims] == [SENT[1], SENT[4], SENT[5]]                      # Sasha out, Critics in, document order
    assert [c.id for c in ex.claims] == ["c1", "c2", "c3"]
    assert ex.claims[2].attributions == ["Critics"] and ex.alternates == [idx("The ministry plans")]


def test_the_replacement_is_checked_too_and_without_alternates_the_claim_stays():
    answers = {"Sasha": {"needs_context": False, "statement": "x", "checkable": False}, "Critics": {"needs_context": False, "statement": "x", "checkable": False}}
    ex = contextualize(result(["A majority", "Sasha"], alternates=["Critics", "The ministry plans"]), MD, "t", llm=llm_by_sentence(answers), settings=S)
    assert [c.text for c in ex.claims] == [SENT[1], SENT[6]]                                # Critics also not checkable -> the next one
    none_left = contextualize(result(["Sasha"]), MD, "t", llm=llm_by_sentence(answers), settings=S)
    assert [c.text for c in none_left.claims] == [SENT[3]]                                  # nothing to refill with: it stays (neutral anyway)


def test_off_and_failures_leave_everything_as_it_was():
    base = result(["Another 31%", "Sasha"], alternates=["Critics"])
    off = contextualize(base, MD, "t", llm=llm_by_sentence({}), settings=S.model_copy(update={"contextualize_claims": False}))
    assert off is base
    failed = contextualize(result(["Another 31%", "Sasha"], alternates=["Critics"]), MD, "t", llm=llm_by_sentence({"Another": "fail", "Sasha": "fail"}), settings=S)
    assert [c.text for c in failed.claims] == [SENT[2], SENT[3]] and all(c.check_text is None for c in failed.claims)
