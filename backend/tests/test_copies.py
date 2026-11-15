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


def test_canonical_reverse_proxy_mirror_is_dropped(monkeypatch):
    class _CanonFetcher:
        def fetch_html(self, url):
            if "proxy" in url:
                return '<html><head><link rel="canonical" href="https://news.example/a"></head></html>'
            return f"<html>{url}</html>"

    monkeypatch.setattr(copies_mod, "find_copies", lambda *a, **k: [
        {"url": "https://cdn-proxy.example/a", "text": _COPY_ARTICLE},
        {"url": "https://mirror-two.example/a", "text": _COPY_ARTICLE},
    ])
    got = find_article_copies("https://news.example/a", _COPY_ARTICLE, api_key="k", fetcher=_CanonFetcher())
    assert [(c.domain, c.url) for c in got] == [("mirror-two.example", "https://mirror-two.example/a")]


def test_sibling_subdomain_copy_is_kept_when_article_on_subdomain(monkeypatch):
    got = []

    def fake_find_copies(*a, **k):
        got.append(k.get("exclude_domain"))
        return [
            {"url": "https://canada.net.example/a", "text": _COPY_ARTICLE},  # same host -> skipped
            {"url": "https://usa.net.example/a", "text": _COPY_ARTICLE},     # sibling subdomain -> kept
        ]

    monkeypatch.setattr(copies_mod, "find_copies", fake_find_copies)
    res = find_article_copies("https://canada.net.example/a", _COPY_ARTICLE, api_key="k", fetcher=_HtmlFetcher())
    assert got == ["canada.net.example"]
    assert [(c.domain, c.url) for c in res] == [("usa.net.example", "https://usa.net.example/a")]
