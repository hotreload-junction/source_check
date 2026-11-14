"""Extract Google Analytics / Tag Manager / AdSense IDs from raw page HTML (B5).

Plain regex on the raw HTML (DECISIONS Q7). The patterns are deliberately strict so placeholders in
docs or templates ("G-XXXXXXXXXX", "UA-XXXXX-Y") and ordinary words never count as an operator ID:
a false match here could link two unrelated domains into one cluster.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# Not preceded/followed by an ID character, so "MAUA-1234-1" or "G-ABC123456x" do not match.
_B = r"(?<![A-Za-z0-9_-])"
_E = r"(?![A-Za-z0-9_-])"

_PATTERNS: dict[str, re.Pattern[str]] = {
    # GA4: G- + 8-12 upper-case alphanumerics, at least one digit (real IDs are 10 chars).
    "ga4": re.compile(_B + r"G-(?=[A-Z0-9]*\d)[A-Z0-9]{8,12}" + _E),
    # Universal Analytics: UA-<account>-<property>
    "ua": re.compile(_B + r"UA-\d{4,10}-\d{1,3}" + _E),
    # Google Tag Manager container (digit required: rejects the "GTM-XXXX" placeholder)
    "gtm": re.compile(_B + r"GTM-(?=[A-Z0-9]*\d)[A-Z0-9]{4,8}" + _E),
    # AdSense publisher ID
    "adsense": re.compile(_B + r"ca-pub-\d{10,20}" + _E),
}
# Yandex.Metrika counters (no Google IDs on e.g. the Pravda network; measured live by leo-be).
# Normalised to "ym-<counter>". Three embed styles: ym(123, ...), Ya.Metrika({id:123}), mc.yandex.ru/watch/123.
_YM_PATTERNS = (
    re.compile(r"(?<![A-Za-z0-9_$.])ym\(\s*(\d{6,10})\s*,"),
    re.compile(r"Ya\.Metrika2?\(\s*\{[^}]*?\bid\s*:\s*(\d{6,10})"),
    re.compile(r"mc\.yandex\.[a-z]{2,3}/(?:watch|informer)/(\d{6,10})"),
)


@dataclass(frozen=True)
class PageIds:
    analytics_ids: list[str] = field(default_factory=list)  # G-..., UA-..., GTM-..., ym-... (sorted, unique)
    adsense_ids: list[str] = field(default_factory=list)    # ca-pub-... (sorted, unique)

    @property
    def all(self) -> list[str]:
        return [*self.analytics_ids, *self.adsense_ids]


def extract_ids(html: str | None) -> PageIds:
    """IDs found anywhere in `html` (scripts, attributes, noscript iframes). Empty for None/empty input."""
    if not html:
        return PageIds()
    analytics: set[str] = set()
    for key in ("ga4", "ua", "gtm"):
        analytics.update(_PATTERNS[key].findall(html))
    for pat in _YM_PATTERNS:
        analytics.update(f"ym-{n}" for n in pat.findall(html))
    adsense = set(_PATTERNS["adsense"].findall(html))
    return PageIds(sorted(analytics), sorted(adsense))
