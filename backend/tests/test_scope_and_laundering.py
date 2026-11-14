"""Extraction scope (article body only, no duplicates, navigation links ignored) and laundering detection
(documented networks, declared Telegram reposts, channel-only claims). Offline: no network, no LLM."""
from app.pipeline.textutil import article_body_filter, attach_links, is_navigation_link, sentence_units
from app.signals.laundering import channel_sourced_claims, declared_reposts
from app.signals.networks import match_network
from app.verdict import ClaimInput, decide

ART = "https://www.theguardian.com/world/2026/nov/10/lewis-hamilton-visits-rafah"


def cl(*verdicts):
    return [ClaimInput(f"c{i + 1}", x) for i, x in enumerate(verdicts)]


def label(*a, **k):
    return decide(*a, **k)["verdict"]["label"]


NET = ["news-pravda.com (the article) is on the published list of the Portal Kombat / Pravda network (VIGINUM)"]
DECLARED = ['Source: Telegram "ukraine_watch"']


# --- documented networks ---------------------------------------------------------------------------------------
def test_network_matches_listed_hosts_and_their_subdomains():
    for u in ("https://news-pravda.com/world/2026/11/14/1.html", "https://germany.news-pravda.com/x",
              "https://www.pravda-de.com/", "pravda-fr.com"):
        m = match_network(u)
        assert m is not None and m.network.startswith("Portal Kombat"), u


def test_network_never_widens_to_a_shared_zone():
    # the list has lenta.kharkiv.ua; the rest of kharkiv.ua is an ordinary city zone
    assert match_network("https://lenta.kharkiv.ua/news/1") is not None
    for u in ("https://kharkiv.ua/", "https://city.kharkiv.ua/news", "https://www.theguardian.com/world",
              "https://notnews-pravda.com/", "", None):
        assert match_network(u) is None, u


# --- declared reposts --------------------------------------------------------------------------------------------
def test_declared_repost_lines_are_found():
    md = ("During a live broadcast, something happened.\n\n"
          '[Source:](https://news-pravda.com/ukraine/2026/11/14/2744406.html) Telegram "ukraine\\_watch" \n'
          "[t](https://telegra.ph/x)\n\nИсточник: Telegram-канал «Рыбарь»\n\nQuelle: [@channel](https://t.me/channel)")
    lines = [d.line for d in declared_reposts(md)]
    assert lines[0].startswith("Source: Telegram") and "ukraine_watch" in lines[0]
    assert any(l.startswith("Источник") for l in lines) and any(l.startswith("Quelle") for l in lines)
    assert len(lines) == 3


def test_quoting_a_telegram_post_is_not_a_declaration():
    md = ("Zelensky wrote on Telegram that the talks will continue next week.\n\n"
          "Source: Reuters\n\n"
          "Sources: officials quoted by the agency, who also said on Telegram that " + "x" * 220)
    assert declared_reposts(md) == []


# --- channel-only claims -------------------------------------------------------------------------------------------
def src(i, kind, url="https://x.test/p", depth=1):
    return {"id": i, "kind": kind, "url": url, "depth": depth}


def test_channel_sourced_claims_counts_only_the_articles_links():
    sources = [src("s1", "telegram"), src("s2", "telegram"), src("s3", "link"), src("s4", "named", url=None),
               src("s5", "named", depth=2), src("s6", "social")]
    claims = [{"id": "c1", "source_ids": ["s1"]}, {"id": "c2", "source_ids": ["s2", "s6"]},
              {"id": "c3", "source_ids": ["s1", "s3"]},            # also a normal link -> not channel-only
              {"id": "c4", "source_ids": ["s4", "s5"]},            # named person + found page: not the article's link
              {"id": "c5", "source_ids": []}]
    assert channel_sourced_claims(claims, sources) == (["c1", "c2"], 3)


def test_laundering_needs_two_channel_claims_and_a_majority():
    ok = cl("supported", "supported", "supported", "supported")
    sig = lambda ids, linked: decide(ok, channel_claim_ids=ids, linked_claims=linked)["signals"]["laundering"]["fired"]
    assert not sig(["c1"], 1)              # one claim never
    assert sig(["c1", "c2"], 3)            # 2 of 3 linked claims
    assert not sig(["c1", "c2"], 4)        # 2 of 4 is not a majority


# --- verdict rule ----------------------------------------------------------------------------------------------------
def test_network_plus_declared_repost_is_a_pattern_even_when_claims_cannot_be_checked():
    r = decide(cl("not_checkable", "not_checkable", "supported"), network_reasons=NET, declared_reposts=DECLARED)
    assert r["verdict"]["label"] == "propaganda_pattern"
    assert r["signals"]["infrastructure"]["fired"] and r["signals"]["laundering"]["fired"]
    assert NET[0] in r["signals"]["infrastructure"]["reasons"]


def test_one_claim_independent_signal_alone_never_overrides_insufficient_evidence():
    assert label(cl("not_checkable", "not_checkable", "supported"), network_reasons=NET) == "insufficient_evidence"
    assert label(cl("not_checkable", "not_checkable", "supported"), declared_reposts=DECLARED) == "insufficient_evidence"


def test_single_laundering_signal_is_not_a_pattern():
    r = decide(cl("supported", "supported", "supported"), declared_reposts=DECLARED)
    assert r["verdict"]["label"] == "well_sourced"
    assert "single signal is not enough" in r["verdict"]["summary"]


def test_laundering_plus_weak_evidence_is_a_pattern():
    assert label(cl("unsourced", "distorted", "supported"), declared_reposts=DECLARED) == "propaganda_pattern"


def test_signal_shape_has_laundering():
    s = decide(cl("supported"))["signals"]
    assert set(s["laundering"]) == {"fired", "claim_ids", "reasons"} and s["laundering"]["fired"] is False


# --- extraction scope ------------------------------------------------------------------------------------------------
def test_navigation_links_of_the_own_site():
    for u in ("https://www.theguardian.com/world/gaza", "https://www.theguardian.com/sport/lewis-hamilton",
              "https://www.theguardian.com/world/palestinian-territories", "https://www.theguardian.com/"):
        assert is_navigation_link(u, ART), u
    for u in ("https://www.theguardian.com/world/2026/nov/08/egypt-two-trains",          # date -> article
              "https://www.zerohedge.com/geopolitical/donbas-nato-zelensky-says-washington-floated",
              "https://www.theguardian.com/p/x7k2.html",
              "https://www.bbc.com/news/world"):                                         # other site
        assert not is_navigation_link(u, ART), u


def test_topic_link_is_ignored_but_article_self_link_stays():
    md = ("He has posted about children killed in [Gaza](https://www.theguardian.com/world/gaza) and called for a ceasefire. "
          "He [said in October](https://www.theguardian.com/sport/2026/oct/02/hamilton-interview) that peace is needed.")
    first = attach_links(md, 0, md.index("ceasefire.") + 10, ART)
    assert first == []
    second = attach_links(md, md.index("He [said"), len(md), ART)
    assert [l.url for l in second] == ["https://www.theguardian.com/sport/2026/oct/02/hamilton-interview"]


def test_repeated_sentences_are_kept_once():
    md = ("# Germany recorded the highest number of corporate insolvencies in 13 years\n\n"
          "**Germany recorded the highest number of corporate insolvencies in 13 years**\n\n"
          "In August 2026, German courts recorded 2,067 applications for corporate insolvencies.\n\n"
          "Top news\n\n"
          "In August 2026, German courts recorded 2,067 applications for corporate insolvencies.")
    texts = [t for _, _, t in sentence_units(md)]
    assert len(texts) == 2
    assert len([t for _, _, t in sentence_units(md, dedupe=False)]) == 4


def test_body_filter_runs_before_the_size_cap():
    md = "\n\n".join([f"Sidebar teaser number {i} about something else entirely." for i in range(50)]
                     + ["The council passed the ordinance on Tuesday with seven votes to two."])
    keep = lambda s: "council" in s
    units = sentence_units(md, max_units=10, keep=keep)
    assert [t for _, _, t in units] == ["The council passed the ordinance on Tuesday with seven votes to two."]


BODY_HTML = """<html><head><title>Council bans AI</title></head><body>
<nav><a href="/">Home</a> <a href="/politics">Politics</a></nav>
<aside class="related"><h3>Related</h3><ul>
<li><a href="/2026-11-12-xi.html">Xi declares Taiwan reunification inevitable days before Trump meeting</a></li>
<li><a href="/2026-11-12-tax.html">California voters reject Proposition 40 billionaire wealth tax</a></li></ul></aside>
<article><h1>Council bans AI</h1>
<p>The city council passed the ordinance on Tuesday with seven votes to two, after a three-hour public hearing.</p>
<p>The ban covers every program the ordinance defines as a recursively self-improving system, the city attorney said.</p>
<p>Residents who break the rule face a fine of 500 dollars per day, according to the text published by the clerk.</p>
<p>Business groups said they would challenge the ordinance in the state court before it takes effect in January.</p>
</article>
<footer>Copyright 2026. All rights reserved. Subscribe to our newsletter for daily updates.</footer>
</body></html>"""


def test_article_body_filter_keeps_the_article_and_drops_the_sidebar():
    keep = article_body_filter(BODY_HTML)
    assert keep is not None
    assert keep("The city council passed the ordinance on Tuesday with seven votes to two, after a three-hour public hearing.")
    assert not keep("Xi declares Taiwan reunification inevitable days before Trump meeting")
    assert not keep("California voters reject Proposition 40 billionaire wealth tax")


def test_article_body_filter_without_usable_html_is_no_filter():
    assert article_body_filter(None) is None
    assert article_body_filter("<html><body><p>Hi.</p></body></html>") is None
