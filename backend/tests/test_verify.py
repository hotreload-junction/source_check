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


def test_article_date_is_passed_to_the_model_only_when_given():
    from datetime import date
    seen = []
    verify_claim("Claim.", EN_SOURCE, settings=SETTINGS, llm=llm_returning(out("not_mentioned", ""), seen), article_date=date(2026, 11, 14))
    assert seen[0]["messages"][1]["content"].startswith("ARTICLE DATE: 2026-11-14")
    assert "different occasion or time" in seen[0]["messages"][0]["content"]
    seen.clear()
    verify_claim("Claim.", EN_SOURCE, settings=SETTINGS, llm=llm_returning(out("not_mentioned", ""), seen))
    assert "ARTICLE DATE" not in seen[0]["messages"][1]["content"]


def test_fuzzy_score_matches_ordered_ellipsis_segments_and_rejects_bad_ones():
    src = (
        "First relevant clause confirms exports jumped 14 percent in the third quarter. "
        "Unrelated procedural paragraph inserted between the two findings in the paper. "
        "Second confirmed clause notes customs delays remained low across all ports."
    )
    valid_dots = "First relevant clause confirms exports jumped 14 percent in the third quarter. ... Second confirmed clause notes customs delays remained low across all ports."
    valid_unicode = "First relevant clause confirms exports jumped 14 percent in the third quarter.\u2026Second confirmed clause notes customs delays remained low across all ports."
    reversed_order = "Second confirmed clause notes customs delays remained low across all ports. ... First relevant clause confirms exports jumped 14 percent in the third quarter."
    fabricated_part = "First relevant clause confirms exports jumped 14 percent in the third quarter. ... Completely invented claim about energy embargoes."
    too_short_part = "Exports jumped 14%. ... Second confirmed clause notes customs delays remained low across all ports."

    assert fuzzy_score(valid_dots, src) >= 90.0
    assert fuzzy_score(valid_unicode, src) >= 90.0
    assert fuzzy_score(reversed_order, src) < 90.0
    assert fuzzy_score(fabricated_part, src) < 90.0
    assert fuzzy_score(too_short_part, src) < 90.0


WALL = "Sign in to continue to Google Trends. Use your Google account to see what the world is searching for. " * 2


def test_login_wall_page_is_not_checkable_and_costs_no_model_call():
    seen = []
    r = verify_claim("Searches for male loneliness spiked in 2023.", WALL, llm=llm_returning(out("not_mentioned", ""), seen), settings=SETTINGS)
    assert r.verdict == "not_checkable" and r.downgraded and "login" in r.explanation and not r.has_evidence
    assert seen == []


def test_a_normal_short_page_still_reaches_the_model():
    seen = []
    r = verify_claim("Searches for male loneliness spiked in 2023.", EN_SOURCE, llm=llm_returning(out("not_mentioned", ""), seen), settings=SETTINGS)
    assert r.verdict == "not_mentioned" and len(seen) == 1


SSO = ("Sign in  [Can’t access your account?](https://login.microsoftonline.com/f610c0b7-bd24-4b39-810b-3dc280afb590/saml2?SAMLRequest="
       + "fZLNbsIwEIRfJfI9sRMSCBZBSuFQJNoikvbQS%2BUYh1hy7NTrlPbtC6Q%2FVKo47" * 6 + "&RelayState=ss%3Amem%3A25f55a946bd8c#)")


def test_sso_login_form_that_is_mostly_a_link_is_not_checkable():
    # live: a WHO PDF behind a Microsoft login came back as "Sign in" + one long SAML link and was judged "not mentioned"
    from app.fetch.fetcher import looks_like_wall
    assert looks_like_wall(SSO) and len(SSO) > 200                      # long raw text, almost no visible words
    seen = []
    r = verify_claim("The UK government commissioned research on loneliness.", SSO, llm=llm_returning(out("not_mentioned", ""), seen), settings=SETTINGS)
    assert r.verdict == "not_checkable" and r.downgraded and seen == []


def test_ordinary_pages_that_mention_login_are_not_walls():
    from app.fetch.fetcher import looks_like_wall
    article = ("The ministry said on Tuesday that the new programme will expand coverage to 40 districts by 2027, according to the "
               "official report. Critics asked how the new login system for the benefit portal would protect passwords. " * 4)
    assert not looks_like_wall(article)
    assert not looks_like_wall("Sign in to read more is how many paywalled pages end, but this page has a whole report. " * 40)   # > 2000 chars
    assert looks_like_wall("Log in") and looks_like_wall("Please sign in to view this document.") and not looks_like_wall("")


def test_anchor_text_passed_only_when_meaningful():
    seen = []
    verify_claim("A long sentence about multiple things.", EN_SOURCE, llm=llm_returning(out("not_mentioned", ""), seen),
                 settings=SETTINGS, anchor_text="expanded coverage")
    assert "LINK ANCHOR: expanded coverage" in seen[0]["messages"][1]["content"]
    assert "LINK ANCHOR" in seen[0]["messages"][0]["content"]
    seen.clear()
    verify_claim("Short anchor ignored.", EN_SOURCE, llm=llm_returning(out("not_mentioned", ""), seen),
                 settings=SETTINGS, anchor_text="ok")
    assert "LINK ANCHOR" not in seen[0]["messages"][1]["content"]


def test_long_source_window_selects_relevant_tail_paragraph():
    filler = ("Unrelated procedural paragraph covering municipal zoning archives. " * 20).strip()
    head = "\n\n".join([filler] * 25)
    target = "The programme will expand coverage to 40 districts by 2027, officials confirmed."
    long_src = f"{head}\n\n{target}\n\n" + "\n\n".join([filler] * 35)
    assert len(long_src) > MAX_SOURCE_CHARS
    seen = []
    r = verify_claim("The programme expands coverage to 40 districts by 2027.", long_src,
                     llm=llm_returning(out("supported", target), seen), settings=SETTINGS)
    assert r.verdict == "supported" and r.has_evidence
    assert target in seen[0]["messages"][1]["content"]
    assert len(seen[0]["messages"][1]["content"]) < MAX_SOURCE_CHARS + 1000
