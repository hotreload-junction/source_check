"""PLAN Q1: source type rule table (backend/app/signals/source_type.py). Offline, inline HTML only."""
import pytest

from app.signals.source_type import SOURCE_TYPES, classify_source_type


def ld(t: str) -> str:
    return f'<html><head><script type="application/ld+json">{{"@context":"https://schema.org","@type":"{t}"}}</script></head></html>'


GRAPH_NEWS = ('<script type="application/ld+json">{"@graph":[{"@type":"WebSite"},{"@type":"Organization"},'
              '{"@type":["NewsArticle"],"headline":"x"}]}</script>')
CITATION_META = '<meta name="citation_doi" content="10.1000/xyz">'


@pytest.mark.parametrize("url,html,expected", [
    # social / user-generated
    ("https://x.com/someone/status/1", None, "social"),
    ("https://t.me/s/channel/123", None, "social"),
    ("https://www.youtube.com/watch?v=abc", None, "social"),
    ("https://truthsocial.com/@user/posts/1", None, "social"),
    # academic
    ("https://doi.org/10.1000/xyz", None, "academic"),
    ("https://pubmed.ncbi.nlm.nih.gov/123456/", None, "academic"),
    ("https://arxiv.org/abs/2401.00001", None, "academic"),
    ("https://journal.example.com/article/1", ld("ScholarlyArticle"), "academic"),
    ("https://journal.example.com/article/2", CITATION_META, "academic"),
    # press releases
    ("https://www.prnewswire.com/news-releases/acme-123.html", None, "press_release"),
    ("https://www.einpresswire.com/article/1/x", None, "press_release"),
    ("https://acme.com/newsroom/acme-launches-x", None, "press_release"),
    ("https://www.bmi.bund.de/SharedDocs/pressemitteilungen/DE/2026/x.html", None, "press_release"),
    ("https://apnews.com/press-release/business-wire/acme-1", None, "press_release"),
    # official (no press path)
    ("https://www.whitehouse.gov/briefings/x", None, "official"),
    ("https://www.gov.uk/government/publications/x", None, "official"),
    ("https://www.zsu.gov.ua/news/x", None, "official"),
    ("https://consilium.europa.eu/en/meetings/x", None, "official"),
    ("https://www.nato.int/cps/en/natohq/x.htm", None, "official"),
    ("https://news.un.org/en/story/2026/11/1", None, "official"),
    ("http://kremlin.ru/events/president/news/1", None, "official"),
    ("https://www.destatis.de/DE/Themen/x.html", None, "official"),
    # reference
    ("https://en.wikipedia.org/wiki/Example", None, "reference"),
    # news
    ("https://www.reuters.com/world/europe/x-2026-11-14/", None, "news"),
    ("https://tass.com/politics/123", None, "news"),
    ("https://some-paper.example/2026/11/14/story", ld("NewsArticle"), "news"),
    ("https://some-paper.example/story", GRAPH_NEWS, "news"),
    ("https://some-paper.example/live", ld("LiveBlogPosting"), "news"),
    # blog
    ("https://someone.substack.com/p/x", None, "blog"),
    ("https://medium.com/@a/x-123", None, "blog"),
    ("https://acme.com/blog/why-x", None, "blog"),
    ("https://plain.example/post", ld("BlogPosting"), "blog"),
    # unknown: nothing deterministic to go on
    ("https://daily-truth.example.org/expert-interview", None, "unknown"),
    ("https://plain.example/article", ld("Article"), "unknown"),
])
def test_rule_table(url, html, expected):
    st = classify_source_type(url, html)
    assert st is not None and st.type == expected, (url, st)
    assert st.type in SOURCE_TYPES
    assert st.reason


def test_no_url_is_none():
    assert classify_source_type(None) is None
    assert classify_source_type("") is None


def test_first_rule_wins():
    # an official site's press section is a press release; a news page on a social host stays social
    assert classify_source_type("https://www.gov.uk/press/x").type == "press_release"
    assert classify_source_type("https://x.com/reuters/status/1", ld("NewsArticle")).type == "social"
    # scholarly metadata beats the news schema (journals publish both)
    assert classify_source_type("https://j.example/a", CITATION_META + ld("NewsArticle")).type == "academic"


def test_news_schema_beats_blog_schema():
    html = ld("BlogPosting") + ld("NewsArticle")
    assert classify_source_type("https://paper.example/x", html).type == "news"


def test_reason_names_the_rule():
    assert classify_source_type("https://www.prnewswire.com/x").reason == "press-release distribution service (prnewswire.com)"
    assert classify_source_type("https://acme.com/newsroom/x").reason == "press-release section of the site (/newsroom/)"
    assert "NewsArticle" in classify_source_type("https://p.example/x", ld("NewsArticle")).reason


def test_lookalike_hosts_do_not_match():
    # a host that merely CONTAINS a listed name is not that host
    assert classify_source_type("https://reuters.com.fake-news.example/x").type == "unknown"
    assert classify_source_type("https://notmedium.com/x").type == "unknown"
    assert classify_source_type("https://governor-blog.example/x").type == "unknown"


def test_broken_html_never_raises():
    bad = '<script type="application/ld+json">{not json</script>' + "<" * 1000
    assert classify_source_type("https://p.example/x", bad).type == "unknown"
    assert classify_source_type("not a url at all", "<html>").type == "unknown"


def test_slug_named_press_is_not_a_section():
    # the last path segment is the article slug: a story called "/press" on a news site is not a press release
    assert classify_source_type("https://paper.example/media/press", ld("NewsArticle")).type == "news"
