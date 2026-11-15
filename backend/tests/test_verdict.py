import pytest

from app import verdict as v
from app.verdict import ClaimInput, decide


def cl(*verdicts):
    return [ClaimInput(f"c{i + 1}", x) for i, x in enumerate(verdicts)]


def dom(name, age=None, article=False):
    return {"domain": name, "age_days": age, "is_article_domain": article}


SHARED = ["a.com and b.net share G-ABC123456"]
OK = ["supported"] * 4
BAD2 = ["distorted", "unsourced", "supported", "supported"]


def label(*a, **k):
    return decide(*a, **k)["verdict"]["label"]


# --- every row of the verdict table -----------------------------------------------------------
def test_insufficient_when_more_than_half_not_checkable():
    assert label(cl("not_checkable", "not_checkable", "not_checkable", "supported")) == "insufficient_evidence"


def test_insufficient_wins_over_signals():
    r = decide(cl("not_checkable", "not_checkable", "distorted"), [dom("b.net", 5)], SHARED)
    assert r["verdict"]["label"] == "insufficient_evidence"


def test_exactly_half_not_checkable_is_not_insufficient():
    assert label(cl("not_checkable", "not_checkable", "supported", "supported")) == "well_sourced"


def test_no_claims_is_insufficient():
    assert label([]) == "insufficient_evidence"


def test_two_signals_propaganda_pattern():
    assert label(cl(*BAD2), [dom("b.net", 400)], SHARED) == "propaganda_pattern"


def test_evidence_plus_young_domain_propaganda_pattern():
    assert label(cl(*BAD2), [dom("a.com", 3000, True), dom("b.net", 55)]) == "propaganda_pattern"


def test_infra_plus_circle_propaganda_pattern():
    assert label(cl(*OK), [], SHARED, ["a", "s1", "a"]) == "propaganda_pattern"


def test_only_evidence_weakly_sourced():
    assert label(cl(*BAD2), [dom("b.net", 900)]) == "weakly_sourced"


def test_weakly_sourced_summary_says_how_claims_fail():
    s = decide(cl("contradicted", "distorted", "distorted", "not_mentioned", "unsourced"))["verdict"]["summary"]
    assert s.startswith("All 5 central claims lack support: 3 are contradicted or distorted by the sources checked, "
                        "2 are not backed by any source checked.")
    assert "false" not in s.lower()


def test_claims_sentence_quantifier():
    assert v.claims_sentence(cl("distorted", "unsourced", "unsourced", "supported")).startswith("Most central claims (3 of 4)")
    assert v.claims_sentence(cl(*BAD2)) == ("2 of 4 central claims lack support: 1 is contradicted or distorted by the "
                                            "sources checked, 1 is not backed by any source checked.")
    assert v.claims_sentence(cl(*OK)) == ""


def test_pattern_summary_adds_claims_sentence_only_when_evidence_fired():
    assert "2 of 4 central claims lack support" in decide(cl(*BAD2), [dom("b.net", 400)], SHARED)["verdict"]["summary"]
    assert "lack support" not in decide(cl(*OK), [], SHARED, ["a", "s1", "a"])["verdict"]["summary"]


def test_only_infra_well_sourced():
    assert label(cl(*OK), [dom("b.net", 10)]) == "well_sourced"
    assert label(cl(*OK), [], SHARED) == "well_sourced"


def test_only_circle_well_sourced():
    assert label(cl(*OK), circle_path=["a", "s1", "a"]) == "well_sourced"


def test_nothing_fires_well_sourced():
    assert label(cl(*OK), [dom("b.net", 900)]) == "well_sourced"


# --- invariants --------------------------------------------------------------------------------
@pytest.mark.parametrize("bad", sorted(v.BAD_CLAIM_VERDICTS))
def test_single_bad_claim_never_propaganda(bad):
    # even with a fired infra signal: one bad claim means the evidence signal stays silent
    r = decide(cl(bad, "supported", "supported", "supported"), [dom("b.net", 5)], SHARED)
    assert r["signals"]["evidence"]["fired"] is False
    assert r["verdict"]["label"] != "propaganda_pattern"


def test_not_checkable_never_counts_as_bad():
    r = decide(cl("not_checkable", "not_checkable", "supported", "supported", "supported"))
    assert r["signals"]["evidence"]["fired"] is False


def test_unknown_age_is_not_young_and_article_age_ignored():
    r = decide(cl(*OK), [dom("a.com", 1, article=True), dom("b.net", None)])
    assert r["signals"]["infrastructure"]["fired"] is False


def test_young_boundary():
    assert decide(cl(*OK), [dom("b.net", v.YOUNG_DOMAIN_DAYS - 1)])["signals"]["infrastructure"]["fired"]
    assert not decide(cl(*OK), [dom("b.net", v.YOUNG_DOMAIN_DAYS)])["signals"]["infrastructure"]["fired"]


def test_evidence_threshold_boundary_and_claim_ids():
    one = decide(cl("distorted", *["supported"] * 3))["signals"]["evidence"]
    two = decide(cl("distorted", "contradicted", "supported", "supported"))["signals"]["evidence"]
    assert not one["fired"] and one["claim_ids"] == []
    assert two["fired"] and two["claim_ids"] == ["c1", "c2"]


def test_signals_match_contract_shape():
    s = decide(cl(*BAD2), [dom("b.net", 55)], SHARED, ["a", "b"])["signals"]
    assert set(s) == {"evidence", "infrastructure", "circle", "laundering"}
    assert set(s["evidence"]) == {"fired", "claim_ids", "detail"}
    assert set(s["infrastructure"]) == {"fired", "reasons"}
    assert set(s["circle"]) == {"fired", "path"}
    assert "b.net was registered 55 days ago (threshold: 90)" in s["infrastructure"]["reasons"]


def test_thresholds_for_methodology():
    assert v.thresholds()["young_domain_days"] == 90 and v.thresholds()["min_bad_claims"] == 2


def test_claims_confirmed_only_by_search_cannot_make_an_article_well_sourced():
    claims = [ClaimInput("c1", "supported", True), ClaimInput("c2", "supported", True), ClaimInput("c3", "supported")]
    out = decide(claims)["verdict"]
    assert out["label"] == "weakly_sourced" and "names no source" in out["summary"]
    half = [ClaimInput("c1", "supported", True), ClaimInput("c2", "supported")]
    assert decide(half)["verdict"]["label"] == "well_sourced"              # exactly half is not "more than half"
    unchecked = [ClaimInput("c1", "supported", True), ClaimInput("c2", "not_checkable"), ClaimInput("c3", "not_checkable"), ClaimInput("c4", "supported")]
    assert decide(unchecked)["verdict"]["label"] == "well_sourced"          # not_checkable claims are not in the share
    assert "max_search_only_share" in v.thresholds()
