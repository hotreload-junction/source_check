"""Publication date of an article from its HTML (needed for the Exa date guard and the verify ARTICLE DATE)."""
from __future__ import annotations

import re
from datetime import datetime, timezone

_PATTERNS = [
    re.compile(r'<meta[^>]+(?:property|name)=["\'](?:article:published_time|og:article:published_time|date|pubdate|publishdate|parsely-pub-date|dc\.date(?:\.issued)?|datePublished)["\'][^>]*content=["\']([^"\']+)["\']', re.I),
    re.compile(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:property|name)=["\'](?:article:published_time|og:article:published_time|date|pubdate|publishdate|parsely-pub-date|dc\.date(?:\.issued)?|datePublished)["\']', re.I),
    re.compile(r'"datePublished"\s*:\s*"([^"]+)"'),
    re.compile(r'<time[^>]+datetime=["\']([^"\']+)["\']', re.I),
]


def parse_published(html: str | None) -> str | None:
    """First plausible publication timestamp as ISO 8601 UTC, else None. Never raises."""
    if not html:
        return None
    head = html[:200_000]
    for pat in _PATTERNS:
        for m in pat.finditer(head):
            iso = _to_iso(m.group(1))
            if iso:
                return iso
    return None


def _to_iso(raw: str) -> str | None:
    raw = raw.strip()
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    dt = dt.astimezone(timezone.utc)
    if not (2000 <= dt.year <= datetime.now(timezone.utc).year + 1):
        return None
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
