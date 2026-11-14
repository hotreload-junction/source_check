"""URL helpers: cache normalisation (contract), domain comparison, source classification (DECISIONS Q18/Q19)."""
from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_TRACKING_PARAMS = {"fbclid", "gclid"}
# Second-level public suffixes we care about; a full PSL would be overkill for same-domain checks.
_SECOND_LEVEL = {
    "co.uk", "org.uk", "ac.uk", "gov.uk", "com.au", "net.au", "org.au", "co.nz", "co.jp",
    "com.br", "com.tr", "com.ua", "co.za", "co.in", "com.cn", "com.mx", "com.ar", "org.ru",
}
_GENERIC_SLD = {"co", "com", "org", "net", "gov", "edu", "ac", "mil", "go", "or", "ne", "gob", "gouv"}
SOCIAL_DOMAINS = {
    "x.com", "twitter.com", "t.co", "youtube.com", "youtu.be", "facebook.com", "fb.com", "instagram.com",
    "tiktok.com", "linkedin.com", "vk.com", "ok.ru", "reddit.com", "threads.net",
}


class InvalidURL(ValueError):
    pass


def _host(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower().rstrip(".")
    return host[4:] if host.startswith("www.") else host


def normalize_url(url: str) -> str:
    """Cache key: lowercase host, strip www., fragment, utm_*/fbclid/gclid, trailing slash."""
    url = url.strip()
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise InvalidURL(f"not an http(s) URL: {url!r}")
    query = [
        (k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not k.lower().startswith("utm_") and k.lower() not in _TRACKING_PARAMS
    ]
    host = _host(url)
    if parts.port and parts.port not in (80, 443):
        host = f"{host}:{parts.port}"
    path = parts.path.rstrip("/")
    return urlunsplit((parts.scheme, host, path, urlencode(query), ""))


def registrable_domain(url_or_host: str) -> str:
    host = _host(url_or_host) if "//" in url_or_host else url_or_host.lower().removeprefix("www.")
    labels = host.split(".")
    if len(labels) <= 2:
        return host
    # country-code second-level registries: gov.ua, co.uk, com.au, org.ru ... (known list + generic pattern)
    if ".".join(labels[-2:]) in _SECOND_LEVEL or (len(labels[-1]) == 2 and labels[-2] in _GENERIC_SLD):
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def is_same_domain(a: str, b: str) -> bool:
    return registrable_domain(a) == registrable_domain(b)


def classify_url(url: str, article_url: str) -> str:
    """-> 'self' | 'social' | 'telegram' | 'link'. Order matters: self-citation wins over everything."""
    if is_same_domain(url, article_url):
        return "self"
    dom = registrable_domain(url)
    if dom in SOCIAL_DOMAINS:
        return "social"
    if dom in ("t.me", "telegram.me"):
        return "telegram"
    return "link"
