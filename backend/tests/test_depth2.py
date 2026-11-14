"""M1: depth 2 (sources of the sources) and the citation-circle signal. Fake fetcher / LLM / RDAP, no network."""
import pytest

from app.config import Settings
from app.pipeline.depth2 import passage_links
from app.pipeline.run import analyze
from app.signals.circles import Citation, article_cluster_domains, circle_path, find_circles
from tests.test_run import (ARTICLE_URL, EXTRACT, SETTINGS, FakeFetcher, FakeRdap, art, router, src, verifier)

QUOTE = "the new programme will expand coverage to 40 districts by 2027"


def ministry_text(link: str) -> str:
    """The depth-1 source that backs c2: the evidence sentence carries the depth-2 link."""
    return (f"Menu | Home\n\nThe ministry said on Tuesday that {QUOTE}, [per the partner outlet]({link}) report says.\n\n"
            "Unrelated paragraph with [a footer link](https://elsewhere.example/footer) and nothing else.")


S2 = Settings(**{**SETTINGS.model_dump(), "max_depth": 2})
CLEAN = "<html></html>"


def pages_with(link: str, extra=None, ministry_html=CLEAN):
    p = {"https://news-front.net/report-123": src("https://news-front.net/report-123", "Sources report new drone attacks.", CLEAN),
         "https://ministry.gov.uk/report": src("https://ministry.gov.uk/report", ministry_text(link), ministry_html)}
    p.update(extra or {})
    return p


def run2(pages, article_html=CLEAN, ages=None, settings=S2, events=None):
    return analyze(ARTICLE_URL, fetcher=FakeFetcher(art(html=article_html), pages), llm=router(EXTRACT, verifier), settings=settings,
                   rdap=FakeRdap(ages or {}), emit=(lambda e, d: events.append((e, d))) if events is not None else None)


# ------------------------------------------------------------------ pure circle logic
def test_find_circles_matches_article_domain_and_cluster_only():
    cites = [Citation("c1", "s1", "s3", "other.org"), Citation("c2", "s2", "s4", "pravda-mirror.com"),
             Citation("c2", "s2", "s5", "sister.net")]
    circles = find_circles(cites, "pravda-mirror.com", {"sister.net"})
    assert [c.path for c in circles] == [["a", "c2", "s2", "s4", "a"], ["a", "c2", "s2", "s5", "a"]]
    assert [c.kind for c in circles] == ["article_domain", "article_cluster"]
    assert circle_path(circles) == ["a", "c2", "s2", "s4", "a"] and circle_path([]) is None
    assert find_circles(cites, "pravda-mirror.com", set())[0].kind == "article_domain"
    assert find_circles([cites[0]], "pravda-mirror.com", {"sister.net"}) == []        # unrelated domain: no circle


def test_article_cluster_domains():
    domains = [{"domain": "a.com", "cluster_id": "k1"}, {"domain": "b.net", "cluster_id": "k1"},
               {"domain": "c.org", "cluster_id": "k2"}, {"domain": "d.io", "cluster_id": None}]
    assert article_cluster_domains(domains, "a.com") == {"b.net"}
    assert article_cluster_domains(domains, "d.io") == set()                          # no ID shared: no cluster


# ------------------------------------------------------------------ passage -> links
def test_passage_links_use_the_evidence_sentence_not_the_whole_page():
    links = passage_links(ministry_text("https://sister.example/x"), QUOTE, "irrelevant claim text here", "https://ministry.gov.uk/report")
    assert [l.url for l in links] == ["https://sister.example/x"] and links[0].anchor_text == "per the partner outlet"


def test_passage_links_fall_back_to_the_claim_text_and_give_up_when_nothing_matches():
    text = "Intro line.\n\nSources said the ministry will expand coverage to 40 districts by 2027, [read more](https://x.example/a) today."
    claim = "The ministry said the programme will expand coverage to 40 districts by 2027"
    assert [l.url for l in passage_links(text, None, claim, "https://m.example/p")] == ["https://x.example/a"]
    assert passage_links("Completely different text about football and the weather today.", None, claim, "https://m.example/p") == []


# ------------------------------------------------------------------ end to end
def test_depth_1_never_touches_depth_2_links():
    pages = pages_with("https://sister.example/x")
    ev = []
    r = run2(pages, settings=Settings(**{**SETTINGS.model_dump(), "max_depth": 1}), events=ev).result
    assert r["audit"]["depth"] == 1 and all(s["depth"] == 1 for s in r["sources"])
    assert "depth2" not in [d["step"] for e, d in ev if e == "progress"]
    assert r["signals"]["circle"] == {"fired": False, "path": None}


def test_link_back_to_the_article_domain_closes_a_circle():
    pages = pages_with("https://www.pravda-mirror.com/2026/11/earlier-story")
    ev = []
    r = run2(pages, events=ev).result
    assert r["audit"]["depth"] == 2
    child = next(s for s in r["sources"] if s["depth"] == 2)
    assert child == {"id": "s3", "url": "https://www.pravda-mirror.com/2026/11/earlier-story", "domain": "pravda-mirror.com",
                     "kind": "self", "attribution_text": "per the partner outlet", "depth": 2, "parent_source_id": "s2",
                     "fetch_status": "not_fetched", "fetched_via": None,
                     "source_type": "unknown", "source_type_reason": "no rule matched"}
    assert r["signals"]["circle"] == {"fired": True, "path": ["a", "c2", "s2", "s3", "a"]}
    edges = {(e["source"], e["target"], e["type"]) for e in r["graph"]["edges"]}
    assert ("s2", "s3", "cites") in edges and ("s3", "a", "cites_back") in edges and ("s3", "d:pravda-mirror.com", "hosted_on") in edges
    node = next(n for n in r["graph"]["nodes"] if n["id"] == "s3")
    assert node["depth"] == 2 and node["type"] == "source"
    steps = [d["step"] for e, d in ev if e == "progress"]
    assert steps.index("infra_signals") < steps.index("depth2") < steps.index("verdict")
    assert ("edge", {"id": "e-s3-back", "source": "s3", "target": "a", "type": "cites_back"}) in ev


def test_circle_alone_never_makes_propaganda_pattern():
    """One signal is not enough (two-signal rule): only c2 is extracted, it is supported, the page links back."""
    pages = pages_with("https://pravda-mirror.com/earlier")
    r = analyze(ARTICLE_URL, fetcher=FakeFetcher(art(html=CLEAN), pages), llm=router([EXTRACT[1]], verifier), settings=S2,
                rdap=FakeRdap({})).result
    assert r["signals"]["circle"]["fired"] is True and r["signals"]["evidence"]["fired"] is False
    assert r["signals"]["infrastructure"]["fired"] is False
    assert r["verdict"]["label"] == "well_sourced"


def test_depth_2_domain_in_the_article_operator_cluster_closes_a_circle():
    ids = '<script>ga("G-SAME0001")</script>'
    pages = pages_with("https://sister-site.org/story", {"https://sister-site.org/story": src("https://sister-site.org/story", "Story text. " * 30, ids)})
    r = run2(pages, article_html=ids).result
    child = next(s for s in r["sources"] if s["depth"] == 2)
    assert child["domain"] == "sister-site.org" and child["fetch_status"] == "ok" and child["parent_source_id"] == "s2"
    clustered = {d["domain"] for d in r["domains"] if d["cluster_id"]}
    assert clustered == {"pravda-mirror.com", "sister-site.org"}
    assert r["signals"]["circle"]["fired"] and r["signals"]["circle"]["path"] == ["a", "c2", "s2", "s3", "a"]
    assert any("G-SAME0001" in x for x in r["signals"]["infrastructure"]["reasons"])
    assert {n["cluster_id"] for n in r["graph"]["nodes"] if n["id"] in ("a", "s3")} == {"k1"}


def test_unrelated_depth_2_source_adds_structure_but_no_circle():
    pages = pages_with("https://independent.org/doc", {"https://independent.org/doc": src("https://independent.org/doc", "Original document. " * 30, CLEAN)})
    r = run2(pages, ages={"independent.org": 4000}).result
    child = next(s for s in r["sources"] if s["depth"] == 2)
    assert child["domain"] == "independent.org" and child["kind"] == "link" and child["fetch_status"] == "ok"
    assert r["signals"]["circle"] == {"fired": False, "path": None}
    assert {d["domain"] for d in r["domains"]} >= {"independent.org", "ministry.gov.uk"}
    assert next(n for n in r["graph"]["nodes"] if n["id"] == "d:independent.org")["depth"] == 2
    assert not [e for e in r["graph"]["edges"] if e["type"] == "cites_back"]


def test_unreachable_depth_2_source_is_neutral():
    pages = pages_with("https://gone.example/page")          # not in the fake fetcher: the fetch fails
    r = run2(pages).result
    child = next(s for s in r["sources"] if s["depth"] == 2)
    assert child["fetch_status"] == "unreachable"
    assert r["signals"]["circle"]["fired"] is False and r["signals"]["infrastructure"]["fired"] is False


def test_homepage_and_same_site_links_are_not_followed():
    text = (f"The ministry said on Tuesday that {QUOTE}, [Reuters](https://www.reuters.com/) and "
            "[our archive](https://ministry.gov.uk/archive) report.")
    pages = pages_with("https://x.example/y")
    pages["https://ministry.gov.uk/report"] = src("https://ministry.gov.uk/report", text, CLEAN)
    r = run2(pages).result
    assert all(s["depth"] == 1 for s in r["sources"]) and r["signals"]["circle"]["fired"] is False


def test_depth_2_link_to_an_existing_depth_1_source_reuses_it():
    pages = pages_with("https://news-front.net/report-123")
    r = run2(pages).result
    assert len(r["sources"]) == 2 and not [s for s in r["sources"] if s["depth"] == 2]
    assert ("s2", "s1", "cites") in {(e["source"], e["target"], e["type"]) for e in r["graph"]["edges"]}


def test_depth_2_replays_identically():
    pages = pages_with("https://www.pravda-mirror.com/2026/11/earlier-story")
    a, b = run2(pages).result, run2(pages).result
    for r in (a, b):
        r["audit"].pop("duration_s")
    assert a == b


@pytest.mark.parametrize("n_links", [5])
def test_fetch_budget_and_per_source_cap(n_links):
    links = " ".join(f"[l{i}](https://site{i}.example/p)" for i in range(n_links))
    text = f"The ministry said on Tuesday that {QUOTE}, {links} report says."
    pages = pages_with("https://x.example/y", {f"https://site{i}.example/p": src(f"https://site{i}.example/p", "t " * 200, CLEAN) for i in range(n_links)})
    pages["https://ministry.gov.uk/report"] = src("https://ministry.gov.uk/report", text, CLEAN)
    r = run2(pages).result
    assert len([s for s in r["sources"] if s["depth"] == 2]) == 2          # MAX_LINKS_PER_SOURCE


S3 = Settings(**{**SETTINGS.model_dump(), "max_depth": 3})
S5 = Settings(**{**SETTINGS.model_dump(), "max_depth": 5})


def test_depth_5_follows_four_hops_and_stops_there():
    hops = [f"https://hop{i}.example/p" for i in range(1, 5)]          # depth 2..5
    beyond = "https://beyond-limit.gov/study.pdf"                      # would be depth 6
    pages = pages_with(hops[0])
    for i, url in enumerate(hops):
        nxt = hops[i + 1] if i + 1 < len(hops) else beyond
        pages[url] = src(url, f"Report repeats {QUOTE}, per [the next link]({nxt}) item.", CLEAN)
    pages[beyond] = src(beyond, "Dataset covers 40 districts through 2027.", CLEAN)
    r = run2(pages, settings=S5).result
    assert r["audit"]["depth"] == 5
    by_url = {s["url"]: s for s in r["sources"]}
    assert [by_url[u]["depth"] for u in hops] == [2, 3, 4, 5]
    assert by_url[hops[3]]["parent_source_id"] == by_url[hops[2]]["id"]
    assert beyond not in by_url                                         # depth 6 is never followed


def test_depth_3_follows_depth_2_citations_and_sets_domain_depth():
    pages = pages_with(
        "https://intermediate.org/story",
        {
            "https://intermediate.org/story": src(
                "https://intermediate.org/story",
                f"Official summary notes {QUOTE}, according to [the primary paper](https://primary-data.gov/study.pdf).",
                CLEAN,
            ),
            "https://primary-data.gov/study.pdf": src(
                "https://primary-data.gov/study.pdf",
                "Primary dataset covering 40 districts through 2027.",
                CLEAN,
            ),
        },
    )
    ev = []
    r = run2(pages, settings=S3, ages={"intermediate.org": 500, "primary-data.gov": 4000}, events=ev).result
    assert r["audit"]["depth"] == 3
    d2 = next(s for s in r["sources"] if s["depth"] == 2)
    d3 = next(s for s in r["sources"] if s["depth"] == 3)
    assert d2["id"] == "s3" and d2["parent_source_id"] == "s2"
    assert d3["id"] == "s4" and d3["parent_source_id"] == "s3" and d3["domain"] == "primary-data.gov"
    assert ("s3", "s4", "cites") in {(e["source"], e["target"], e["type"]) for e in r["graph"]["edges"]}
    assert next(n for n in r["graph"]["nodes"] if n["id"] == "d:primary-data.gov")["depth"] == 3
    assert next(n for n in r["graph"]["nodes"] if n["id"] == "s4")["depth"] == 3


def test_depth_3_circle_back_to_article_records_full_chain():
    pages = pages_with(
        "https://hop2.org/piece",
        {
            "https://hop2.org/piece": src(
                "https://hop2.org/piece",
                f"Background states {QUOTE}, per [earlier claim](https://www.pravda-mirror.com/2026/origin).",
                CLEAN,
            ),
        },
    )
    r = run2(pages, settings=S3).result
    d3 = next(s for s in r["sources"] if s["depth"] == 3)
    assert d3["kind"] == "self" and d3["fetch_status"] == "not_fetched"
    assert r["signals"]["circle"] == {"fired": True, "path": ["a", "c2", "s2", "s3", "s4", "a"]}


def test_search_ids_excludes_depth_3_under_found_named_source():
    from app.pipeline.run import _search_ids
    from app.signals.circles import Citation

    rows = [
        {"id": "s1", "kind": "named", "url": "https://found.example/p", "depth": 1, "parent_source_id": None},
        {"id": "s2", "kind": "link", "url": "https://d2.example/a", "depth": 2, "parent_source_id": "s1"},
        {"id": "s3", "kind": "link", "url": "https://d3.example/b", "depth": 3, "parent_source_id": "s2"},
        {"id": "s4", "kind": "link", "url": "https://cited.example/x", "depth": 1, "parent_source_id": None},
        {"id": "s5", "kind": "link", "url": "https://shared.example/y", "depth": 2, "parent_source_id": "s1"},
        {"id": "s6", "kind": "link", "url": "https://below-shared.example/z", "depth": 3, "parent_source_id": "s5"},
    ]
    cites = [Citation("c1", "s4", "s5", "shared.example", ("s4",))]
    assert _search_ids(rows, cites) == {"s1", "s2", "s3"}


def test_passage_links_handles_multi_sentence_alt_quote():
    source_text = (
        "Background note on unrelated regional events.\n\n"
        "Measurements taken during the winter campaign showed crystal levels stayed below seven percent, "
        "[the primary study](https://primary.org/study) reported."
    )
    alt_quote = (
        "Completely paraphrased lead sentence that is not on the target page at all. "
        "Measurements taken during the winter campaign showed crystal levels stayed below seven percent."
    )
    links = passage_links(source_text, None, "Unmatched claim wording.", "https://mid.example/article", alt_quote=alt_quote)
    assert [l.url for l in links] == ["https://primary.org/study"]


def test_expand_sources_preserves_quote_after_none_and_expands_found_sources():
    from app.pipeline.assess import ClaimAssessment, SourceAssessment
    from app.pipeline.depth2 import expand_sources
    from app.pipeline.extract import ExtractedClaim
    from app.pipeline.verify import VerifyResult

    u_shared = "https://shared.example/page"
    u_found = "https://found.example/article"
    shared_text = f"Intro.\n\nData confirms {QUOTE}, citing [the dataset](https://d2a.example/data)."
    found_text = f"Report confirms {QUOTE}, citing [hop two](https://d2b.example/hop2)."
    hop2_text = f"Archive notes {QUOTE}, citing [primary source](https://d3b.example/primary)."

    pages = {
        u_shared: src(u_shared, shared_text, CLEAN),
        u_found: src(u_found, found_text, CLEAN),
        "https://d2a.example/data": src("https://d2a.example/data", "Dataset numbers match.", CLEAN),
        "https://d2b.example/hop2": src("https://d2b.example/hop2", hop2_text, CLEAN),
        "https://d3b.example/primary": src("https://d3b.example/primary", "Primary source record.", CLEAN),
    }
    rows = [
        {"id": "s1", "url": u_shared, "domain": "shared.example", "kind": "link",
         "attribution_text": None, "depth": 1, "parent_source_id": None, "fetch_status": "ok", "fetched_via": "context_dev"},
        {"id": "s2", "url": u_found, "domain": "found.example", "kind": "named",
         "attribution_text": None, "depth": 1, "parent_source_id": None, "fetch_status": "ok", "fetched_via": "context_dev"},
    ]
    c1, c2, c3 = ExtractedClaim("c1", "Unmatched claim.", 0, 10), ExtractedClaim("c2", "Shared claim.", 11, 20), ExtractedClaim("c3", "Found claim.", 21, 30)
    v_miss = VerifyResult(verdict="not_mentioned", llm_verdict="not_mentioned", quote_original=None,
                          quote_translation=None, language="en", fuzzy_score=None, explanation="", model="m", prompt_version="v8")
    v_hit = VerifyResult(verdict="supported", llm_verdict="supported", quote_original=QUOTE,
                         quote_translation=None, language="en", fuzzy_score=100.0, explanation="", model="m", prompt_version="v8")
    assessments = [
        ClaimAssessment(c1, "not_mentioned", None, "", sources=[
            SourceAssessment(u_shared, "link", None, pages[u_shared], v_miss, "not_mentioned"),
        ]),
        ClaimAssessment(c2, "supported", None, "", sources=[
            SourceAssessment(u_shared, "link", None, pages[u_shared], v_hit, "supported"),
        ]),
        ClaimAssessment(c3, "supported", None, "", sources=[
            SourceAssessment(u_found, "named", None, pages[u_found], v_hit, "supported"),
        ]),
    ]
    claims = [
        {"id": "c1", "text": c1.text, "source_ids": ["s1"], "found_source_ids": []},
        {"id": "c2", "text": c2.text, "source_ids": ["s1"], "found_source_ids": []},
        {"id": "c3", "text": c3.text, "source_ids": [], "found_source_ids": ["s2"]},
    ]
    expand_sources(rows, assessments, claims, fetcher=FakeFetcher(art(), pages),
                   article_url=ARTICLE_URL, article_domain="pravda-mirror.com", max_depth=3)
    by_url = {r["url"]: r for r in rows}
    assert by_url["https://d2a.example/data"]["depth"] == 2
    assert by_url["https://d2a.example/data"]["parent_source_id"] == "s1"
    assert by_url["https://d2b.example/hop2"]["depth"] == 2
    assert by_url["https://d2b.example/hop2"]["parent_source_id"] == "s2"
    assert by_url["https://d3b.example/primary"]["depth"] == 3
    assert by_url["https://d3b.example/primary"]["parent_source_id"] == by_url["https://d2b.example/hop2"]["id"]
