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


# ----- copies of the article (mirror network): the page repeats the article's own sentences
_COPY_ARTICLE = (
    "The regional council approved the new transport budget after a long debate on Tuesday evening. "
    "Opposition members criticised the plan for ignoring rural bus lines that serve thousands of commuters. "
    "The mayor defended the decision and promised a review of the rural timetable within six months. "
    "Unions announced a protest outside the town hall on Friday morning against the planned cuts."
)


def test_looks_like_copy_detects_a_mirror_but_not_an_independent_report():
    from app.pipeline.textutil import looks_like_copy
    mirror = "Mirror site. " + _COPY_ARTICLE + " Subscribe to our channel."
    independent = "The council voted on transport money this week; opposition politicians were unhappy and unions plan to demonstrate."
    assert looks_like_copy(_COPY_ARTICLE, mirror, "") is True
    assert looks_like_copy(_COPY_ARTICLE, independent, "") is False
    assert looks_like_copy("Too short to judge.", mirror, "") is False


def test_single_quoted_link_titles_and_quote_aware_sentence_splits():
    from app.pipeline.textutil import sentence_units

    md = (
        "Read [the official report](https://gov.example/r 'Official Report Title') today. "
        "Officials said \u201cthe city is safe.\u201d Next sentence follows cleanly. "
        "The headline \u00abBetrayal. Expendable Material\u00bb aired on prime time television. "
        'He asked: "What if this happened? Would they act?" Then everyone waited.'
    )
    plain = build_plain(md)
    assert "Official Report Title" not in plain.text
    assert [u.url for u in attach_links(md, 0, md.index("today.") + 6)] == ["https://gov.example/r"]

    units = [t for _, _, t in sentence_units(md)]
    assert 'Officials said \u201cthe city is safe.\u201d' in units
    assert "Next sentence follows cleanly." in units
    assert "The headline \u00abBetrayal. Expendable Material\u00bb aired on prime time television." in units
    assert 'He asked: "What if this happened? Would they act?"' in units
    idx = md.index("Officials said")
    s0, s1 = sentence_bounds(md, idx, idx + 15)
    assert md[s0:s1] == "Officials said \u201cthe city is safe.\u201d"


def test_syndication_share_detects_short_excerpt_of_long_article():
    from app.pipeline.textutil import SYNDICATION_THRESHOLD, syndication_share

    shared = [
        f"Negotiators from both delegations met in Geneva for the {w} round of technical consultations on export controls."
        for w in ("first", "second", "third")
    ]
    extra = [
        f"Separate background briefing note number {i} covered customs procedures and long-term tariff schedules in detail."
        for i in range(1, 8)
    ]
    long_article = " ".join(shared + extra)
    short_excerpt = " ".join(shared + [
        "Another brief paragraph at the bottom summarizes unrelated regional weather conditions for travellers."
    ])
    share = syndication_share(long_article, "Unrelated specific claim sentence.", short_excerpt)
    assert share is not None and share >= SYNDICATION_THRESHOLD
