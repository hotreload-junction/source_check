from app.pipeline.textutil import attach_links, build_plain, locate, sentence_bounds, to_markdown_span

MD = (
    "# Headline\n\n"
    "The ministry said **40 districts** will be covered, [the report](https://gov.example/report) states. "
    "A second sentence without a link follows here.\n\n"
    "![img](https://cdn.example/a.jpg) Another paragraph with [Reuters](https://reuters.com/x) cited. "
    "Then a claim in the same paragraph without its own link.\n\n"
    "Last paragraph has no links at all and says something checkable."
)


def span(claim):
    plain = build_plain(MD)
    ps, pe, score = locate(plain, claim)
    return to_markdown_span(MD, plain, ps, pe), score


def test_plain_view_strips_markup_and_maps_back():
    p = build_plain(MD)
    assert "**" not in p.text and "](" not in p.text and "![" not in p.text
    assert "40 districts will be covered, the report states." in p.text
    # every plain char maps to the same char in the markdown (links excepted: text chars map inside token)
    for i in (0, 20, len(p.text) - 1):
        assert MD[p.to_md[i]] == p.text[i]


def test_locate_exact_markup_stripped_and_offsets_roundtrip():
    (s, e), score = span("The ministry said 40 districts will be covered, the report states.")
    assert score == 100
    assert MD[s:e] == "The ministry said **40 districts** will be covered, [the report](https://gov.example/report) states."


def test_locate_fuzzy_tolerates_small_edits_but_not_paraphrase():
    plain = build_plain(MD)
    assert locate(plain, "The ministry said 40 districts will be covered, the report state.") is not None
    assert locate(plain, "Officials announced that a total of forty regions would receive coverage soon") is None


def test_span_never_cuts_a_link_token():
    plain = build_plain(MD)
    ps = plain.text.find("the report")
    s, e = to_markdown_span(MD, plain, ps, ps + len("the report"))
    assert MD[s:e] == "[the report](https://gov.example/report)"


def test_sentence_link_wins_over_paragraph():
    (s, e), _ = span("The ministry said 40 districts will be covered, the report states.")
    refs = attach_links(MD, s, e)
    assert [(r.url, r.scope) for r in refs] == [("https://gov.example/report", "sentence")]


def test_paragraph_fallback_and_images_ignored():
    (s, e), _ = span("Then a claim in the same paragraph without its own link.")
    refs = attach_links(MD, s, e)
    assert [(r.url, r.scope) for r in refs] == [("https://reuters.com/x", "paragraph")]  # not the image


def test_no_links_and_article_self_link_excluded():
    (s, e), _ = span("Last paragraph has no links at all and says something checkable.")
    assert attach_links(MD, s, e) == []
    (s, e), _ = span("The ministry said 40 districts will be covered, the report states.")
    assert attach_links(MD, s, e, article_url="https://gov.example/report/") == []


def test_sentence_bounds_do_not_split_on_url_dots():
    (s, e), _ = span("A second sentence without a link follows here.")
    s0, s1 = sentence_bounds(MD, s, e)
    assert MD[s0:s1] == "A second sentence without a link follows here."


def test_dw_topic_pages_are_navigation_not_citations():
    from app.pipeline.textutil import is_navigation_link
    art = "https://www.dw.com/en/some-story-about-things/a-79947535"
    assert is_navigation_link("https://www.dw.com/en/nato/t-19016346", art)
    assert not is_navigation_link("https://www.dw.com/en/what-have-the-talks-achieved-for-ukraine/a-79930613", art)
