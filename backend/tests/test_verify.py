import json

import httpx
import pytest

from app.config import Settings
from app.llm import LLMClient, LLMError
from app.pipeline.verify import MAX_SOURCE_CHARS, fuzzy_score, verify_claim

SETTINGS = Settings(_env_file=None, model_verify="verify-model", fuzzy_threshold=90, prompt_version="vT")
SOURCE = ("Финское министерство обороны заявило во вторник, что беспилотник пересёк границу страны около полуночи. "
          "Расследование продолжается, подробности не раскрываются. " * 3)
EN_SOURCE = ("The ministry said on Tuesday that the new programme will expand coverage to 40 districts by 2027, "
             "according to the official report published last week. Critics doubt the timeline.")


def llm_returning(payload, seen=None):
    def handler(request):
        if seen is not None:
            seen.append(json.loads(request.content))
        content = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
        return httpx.Response(200, json={"model": "verify-model", "choices": [{"message": {"content": content}}]})
    return LLMClient("https://r.test/v1", "k", retries=0, client=httpx.Client(transport=httpx.MockTransport(handler)))


def out(verdict, quote, **kw):
    return {"quote_original": quote, "quote_translation": kw.get("tr"), "language": kw.get("lang", "en"),
            "verdict": verdict, "explanation": "because."}


def test_supported_with_verbatim_quote():
    q = "the new programme will expand coverage to 40 districts by 2027"
    r = verify_claim("The programme covers 40 districts by 2027.", EN_SOURCE, llm=llm_returning(out("supported", q)), settings=SETTINGS)
    assert r.verdict == "supported" and r.fuzzy_score >= 90 and not r.downgraded
    assert (r.model, r.prompt_version, r.llm_verdict) == ("verify-model", "vT", "supported")
    assert r.has_evidence


def test_fuzzy_tolerates_whitespace_and_markup_in_source():
    src = "The **ministry** said\non Tuesday that the [new programme](https://x.test) will expand coverage to 40 districts by 2027."
    q = "The ministry said on Tuesday that the new programme will expand coverage to 40 districts by 2027."
    assert fuzzy_score(q, src) >= 95


def test_invented_quote_downgrades_supported_to_not_checkable():
    r = verify_claim("Claim.", EN_SOURCE, settings=SETTINGS, llm=llm_returning(
        out("supported", "The ministry guaranteed full coverage of every district before the end of 2026.")))
    assert r.verdict == "not_checkable" and r.downgraded and r.llm_verdict == "supported"
    assert r.fuzzy_score < 90 and not r.has_evidence
    assert "could not be found" in r.explanation


@pytest.mark.parametrize("bad", ["distorted", "contradicted"])
def test_negative_verdicts_also_need_a_verified_quote(bad):
    r = verify_claim("Claim.", EN_SOURCE, settings=SETTINGS, llm=llm_returning(
        out(bad, "Officials flatly denied that any programme would ever be extended to more districts.")))
    assert r.verdict == "not_checkable" and r.downgraded      # never trust an unchecked quote against an article


def test_negative_verdict_with_verified_quote_is_kept():
    q = "the new programme will expand coverage to 40 districts by 2027"
    r = verify_claim("It covers 400 districts.", EN_SOURCE, settings=SETTINGS, llm=llm_returning(out("distorted", q)))
    assert r.verdict == "distorted" and r.has_evidence


def test_short_or_missing_quote_downgrades():
    for q in ("", "40 districts"):
        r = verify_claim("Claim.", EN_SOURCE, settings=SETTINGS, llm=llm_returning(out("supported", q)))
        assert r.verdict == "not_checkable" and r.fuzzy_score is None


def test_not_mentioned_needs_no_quote_and_drops_any_quote():
    r = verify_claim("Claim.", EN_SOURCE, settings=SETTINGS, llm=llm_returning(out("not_mentioned", "whatever it made up here ok")))
    assert r.verdict == "not_mentioned" and r.quote_original is None and not r.downgraded


def test_not_mentioned_on_truncated_source_is_neutral():
    long_src = EN_SOURCE + " filler" * (MAX_SOURCE_CHARS // 6 + 100)
    seen = []
    r = verify_claim("Claim.", long_src, settings=SETTINGS, llm=llm_returning(out("not_mentioned", ""), seen))
    assert r.verdict == "not_checkable" and "too long" in r.explanation
    assert len(seen[0]["messages"][1]["content"]) < MAX_SOURCE_CHARS + 1000


def test_cross_language_quote_and_translation():
    q = "Финское министерство обороны заявило во вторник, что беспилотник пересёк границу страны около полуночи."
    r = verify_claim("Finland's defence ministry said a drone crossed the border.", SOURCE, settings=SETTINGS,
                     llm=llm_returning(out("supported", q, lang="ru", tr="The Finnish Ministry of Defence said on Tuesday that a drone crossed the border around midnight.")))
    assert r.verdict == "supported" and r.language == "ru" and r.quote_translation.startswith("The Finnish")


def test_prompt_is_quote_first_and_source_plain_text():
    seen = []
    verify_claim("Claim [x](https://a.test).", "Some [linked](https://b.test) source text " * 5, settings=SETTINGS,
                 llm=llm_returning(out("not_mentioned", ""), seen))
    sys, user = seen[0]["messages"][0]["content"], seen[0]["messages"][1]["content"]
    assert sys.index('"quote_original"') < sys.index('"verdict"', sys.index('"quote_original"'))
    assert "https://" not in user and seen[0]["temperature"] == 0 and seen[0]["model"] == "verify-model"


def test_invalid_verdict_raises():
    with pytest.raises(LLMError):
        verify_claim("c", EN_SOURCE, settings=SETTINGS, llm=llm_returning({"verdict": "maybe", "quote_original": ""}))
