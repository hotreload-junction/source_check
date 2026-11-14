from app.fetch.meta import parse_published


def test_meta_property_either_attribute_order_and_timezones():
    assert parse_published('<meta property="article:published_time" content="2026-11-14T08:30:00+01:00">') == "2026-11-14T07:30:00Z"
    assert parse_published('<meta content="2026-11-14T08:30:00Z" property="article:published_time" />') == "2026-11-14T08:30:00Z"
    assert parse_published('<meta name="date" content="2026-11-13">') == "2026-11-13T00:00:00Z"


def test_json_ld_and_time_tag_fallbacks_and_precedence():
    assert parse_published('<script type="application/ld+json">{"@type":"NewsArticle","datePublished":"2026-11-12T10:00:00Z"}</script>') == "2026-11-12T10:00:00Z"
    assert parse_published('<time datetime="2026-11-11T09:00:00Z">Nov 11</time>') == "2026-11-11T09:00:00Z"
    both = '<meta property="article:published_time" content="2026-11-14T00:00:00Z"><time datetime="2020-01-01T00:00:00Z">'
    assert parse_published(both) == "2026-11-14T00:00:00Z"


def test_garbage_and_implausible_dates_are_none():
    for bad in (None, "", "<html></html>", '<meta name="date" content="soon">', '<meta name="date" content="1970-01-01">',
                '<meta name="date" content="2999-01-01">'):
        assert parse_published(bad) is None
