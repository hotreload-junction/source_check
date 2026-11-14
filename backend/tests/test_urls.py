import pytest

from app.fetch.urls import InvalidURL, classify_url, is_same_domain, normalize_url, registrable_domain


def test_normalize_contract_rules():
    assert normalize_url("HTTPS://WWW.Example.COM/Path/?utm_source=x&b=2&fbclid=1&gclid=2#frag") == \
        "https://example.com/Path?b=2"
    assert normalize_url("https://example.com/a/") == normalize_url("https://www.example.com/a#x")


@pytest.mark.parametrize("bad", ["ftp://x.com", "example.com", "", "https://"])
def test_normalize_invalid(bad):
    with pytest.raises(InvalidURL):
        normalize_url(bad)


def test_registrable_and_same_domain():
    assert registrable_domain("https://news.bbc.co.uk/x") == "bbc.co.uk"
    assert registrable_domain("https://www.reuters.com") == "reuters.com"
    assert is_same_domain("https://a.example.com/x", "https://example.com/y")
    assert not is_same_domain("https://example.com", "https://example.org")
    assert not is_same_domain("https://a.co.uk", "https://b.co.uk")


@pytest.mark.parametrize("url,kind", [
    ("https://news.test/other", "self"),
    ("https://x.com/user/status/1", "social"),
    ("https://youtu.be/abc", "social"),
    ("https://t.me/chan/5", "telegram"),
    ("https://example.org/report", "link"),
])
def test_classify(url, kind):
    assert classify_url(url, "https://www.news.test/a") == kind


def test_self_beats_social():
    assert classify_url("https://x.com/a", "https://x.com/b") == "self"


def test_government_second_level_domains_are_not_one_domain():
    # found in a live run: president.gov.ua collapsed to "gov.ua"
    assert registrable_domain("https://www.president.gov.ua/en/news/x") == "president.gov.ua"
    assert not is_same_domain("https://www.president.gov.ua/a", "https://mfa.gov.ua/b")
    assert is_same_domain("https://www.president.gov.ua/a", "https://president.gov.ua/b")
    assert registrable_domain("https://www.defmin.fi/en") == "defmin.fi"
    assert registrable_domain("https://www.gov.uk/x") == "gov.uk"
