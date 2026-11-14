"""Domain age via RDAP (B5).

`https://rdap.org/domain/<domain>` redirects to the registry's RDAP server. Every failure (404, timeout,
rate limit, no registration event, unparsable date) yields `created_at=None, age_days=None`: unknown age is
NEUTRAL and must never count as "young" (AGENTS.md section 7). Results are cached per process.

No threshold lives here: "younger than N days" is decided in `app/verdict.py`.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

import httpx

from ..fetch.urls import registrable_domain

log = logging.getLogger("citetrace.signals.rdap")

RDAP_BASE = "https://rdap.org/domain/"


@dataclass(frozen=True)
class RdapResult:
    domain: str                  # registrable domain that was looked up
    created_at: str | None       # ISO 8601 UTC ("2024-02-01T00:00:00Z")
    age_days: int | None
    error: str | None = None


def parse_registration_date(data: dict) -> datetime | None:
    """Registration date from an RDAP domain object (`events[].eventAction == "registration"`)."""
    for ev in data.get("events") or []:
        if str(ev.get("eventAction", "")).lower() == "registration":
            raw = ev.get("eventDate")
            if not raw:
                continue
            try:
                dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            except ValueError:
                return None
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    return None


class RdapClient:
    def __init__(self, client: httpx.Client | None = None, *, base_url: str = RDAP_BASE,
                 timeout: float = 8.0, now: Callable[[], datetime] | None = None):
        self.client = client or httpx.Client(
            timeout=timeout, follow_redirects=True, headers={"Accept": "application/rdap+json"}
        )
        self.base_url = base_url
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._cache: dict[str, RdapResult] = {}

    def lookup(self, domain: str) -> RdapResult:
        reg = registrable_domain(domain)
        if reg in self._cache:
            return self._cache[reg]
        res = self._fetch(reg)
        self._cache[reg] = res
        return res

    __call__ = lookup

    def _fetch(self, reg: str) -> RdapResult:
        try:
            r = self.client.get(self.base_url + reg)
        except httpx.HTTPError as exc:
            return RdapResult(reg, None, None, error=type(exc).__name__)
        if r.status_code != 200:
            return RdapResult(reg, None, None, error=f"http {r.status_code}")
        try:
            data = r.json()
        except ValueError:
            return RdapResult(reg, None, None, error="bad json")
        created = parse_registration_date(data) if isinstance(data, dict) else None
        if created is None:
            return RdapResult(reg, None, None, error="no registration event")
        age = max((self._now() - created).days, 0)
        iso = created.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        return RdapResult(reg, iso, age)
