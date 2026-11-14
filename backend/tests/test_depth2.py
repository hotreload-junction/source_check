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
