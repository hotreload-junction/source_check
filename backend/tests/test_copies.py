"""Copies of the article on other domains (M3): only real mirrors on other domains reach the infrastructure analysis."""
from app.pipeline import copies as copies_mod
from app.pipeline.copies import find_article_copies
from tests.test_textutil import _COPY_ARTICLE


class _HtmlFetcher:
    def fetch_html(self, url):
        return f"<html>{url}</html>"


def _run(monkeypatch, hits):
    monkeypatch.setattr(copies_mod, "find_copies", lambda *a, **k: hits)
    return find_article_copies("https://news.example/a", _COPY_ARTICLE, api_key="k", fetcher=_HtmlFetcher())


def test_only_mirrors_on_other_domains_are_kept(monkeypatch):
    got = _run(monkeypatch, [
        {"url": "https://mirror-one.example/x", "text": _COPY_ARTICLE},
        {"url": "https://mirror-one.example/y", "text": _COPY_ARTICLE},          # same domain twice: one entry
        {"url": "https://news.example/other", "text": _COPY_ARTICLE},            # the article's own domain
        {"url": "https://report.example/z", "text": "An independent short report about the council budget vote."},
    ])
    assert [(c.domain, c.url) for c in got] == [("mirror-one.example", "https://mirror-one.example/x")]
    assert got[0].html == "<html>https://mirror-one.example/x</html>"


def test_a_failed_search_is_neutral(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("exa down")
    monkeypatch.setattr(copies_mod, "find_copies", boom)
    assert find_article_copies("https://news.example/a", _COPY_ARTICLE, api_key="k", fetcher=_HtmlFetcher()) == []
