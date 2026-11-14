"""Domain reputation badges (PLAN Q2): context for the reader, NEVER a verdict signal (DECISIONS "SOURCE QUALITY").

Three independent, deterministic facts per domain, each with its provenance:
1. Wikipedia Perennial Sources (WP:RSP): the community's status for the outlet, from a dated snapshot in `data/`.
2. Tranco rank: position in a research top-sites list (popularity, NOT reliability: rt.com ranks ~2,700), dated snapshot.
3. Wayback Machine first capture (live CDX API, step-cached). Pages archived long BEFORE the current RDAP registration
   date mean the domain existed before and was registered again (expired-domain reuse is a known laundering tactic).

Lookups that fail leave the field null and add no note: unknown is neutral. Nothing here touches `verdict.py`.
Snapshots are rebuilt with `python -m app.signals.reputation_build` (manual, network).
"""
from __future__ import annotations

import gzip
import json
import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Callable

import httpx

from ..fetch.urls import registrable_domain

log = logging.getLogger("citetrace.signals.reputation")

DATA_DIR = Path(__file__).resolve().parent / "data"
RSP_FILE = DATA_DIR / "wikipedia_rsp.json"
TRANCO_FILE = DATA_DIR / "tranco_top100k.txt.gz"
TRANCO_META_FILE = DATA_DIR / "tranco_meta.json"
TRANCO_KEEP = 100_000

# WP:RSP status codes (Wikipedia:Reliable sources/Perennial sources/Status: "m" renders as No consensus)
RSP_STATUS = {"gr": "generally_reliable", "nc": "no_consensus", "m": "no_consensus",
              "gu": "generally_unreliable", "d": "deprecated"}
_STATUS_TEXT = {"generally_reliable": "generally reliable", "no_consensus": "no consensus",
                "generally_unreliable": "generally unreliable", "deprecated": "deprecated"}

# a capture this long before the registration date cannot be clock or timezone noise
REREGISTERED_MIN_DAYS = 365
WAYBACK_CDX = "https://web.archive.org/cdx/search/cdx"
WAYBACK_CONCURRENCY = 2
WAYBACK_BACKOFF_S = 300.0


# --- snapshots ---------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Snapshots:
    rsp: dict[str, list[dict]]       # registrable domain -> RSP rows that list it (several: "Fox News (politics)" ...)
    rsp_date: str | None
    tranco: dict[str, int]           # registrable domain -> rank
    tranco_id: str | None
    tranco_date: str | None


@lru_cache(maxsize=1)
def snapshots() -> Snapshots:
    """Load the dated snapshots once per process. A missing/broken file = that badge is off (never a crash)."""
    rsp: dict[str, list[dict]] = {}
    rsp_date = None
    try:
        data = json.loads(RSP_FILE.read_text(encoding="utf-8"))
        rsp_date = data.get("retrieved")
        for e in data["entries"]:
            row = {k: e[k] for k in ("source", "statuses", "blacklisted", "last_discussed")}
            for d in e["domains"]:
                if registrable_domain(d) == d:           # host-level entries (baike.baidu.com) do not rate the whole site
                    rsp.setdefault(d, []).append(row)
    except Exception as exc:
        log.warning("RSP snapshot unavailable: %s", exc)
    tranco: dict[str, int] = {}
    tid = tdate = None
    try:
        with gzip.open(TRANCO_FILE, "rt", encoding="utf-8") as f:
            for rank, line in enumerate(f, 1):
                d = line.strip()
                if d and d not in tranco:
                    tranco[d] = rank
        meta = json.loads(TRANCO_META_FILE.read_text(encoding="utf-8"))
        tid, tdate = meta.get("list_id"), meta.get("created_on")
    except Exception as exc:
        log.warning("Tranco snapshot unavailable: %s", exc)
        tranco = {}
    return Snapshots(rsp, rsp_date, tranco, tid, tdate)


# --- Wayback Machine ---------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class ArchiveResult:
    domain: str
    first_archived_at: str | None    # "YYYY-MM-DD"; None = never archived OR lookup failed (see error)
    error: str | None = None


class WaybackClient:
    """First capture of a domain from the Wayback CDX API (ascending order, limit 1). Never raises.

    archive.org refuses connections for a while after bursts (measured: 4 parallel lookups, then "connection refused"),
    so at most WAYBACK_CONCURRENCY requests are in flight per process, and after a connection error / 429 every lookup
    answers "unknown" at once for WAYBACK_BACKOFF_S instead of hammering the service."""

    _slots = threading.BoundedSemaphore(WAYBACK_CONCURRENCY)
    _down_until = 0.0                      # shared by all instances: one process = one IP

    def __init__(self, client: httpx.Client | None = None, *, timeout: float = 6.0,
                 clock: Callable[[], float] = time.monotonic):
        self.client = client or httpx.Client(timeout=timeout, follow_redirects=True,
                                             headers={"User-Agent": "CiteTrace/1.0 (source checking)"})
        self._cache: dict[str, ArchiveResult] = {}
        self._clock = clock

    def lookup(self, domain: str) -> ArchiveResult:
        reg = registrable_domain(domain)
        if reg not in self._cache:
            self._cache[reg] = self._fetch(reg)
        return self._cache[reg]

    __call__ = lookup

    def _fetch(self, reg: str) -> ArchiveResult:
        if self._clock() < WaybackClient._down_until:
            return ArchiveResult(reg, None, "backing off")
        try:
            with WaybackClient._slots:
                r = self.client.get(WAYBACK_CDX, params={"url": reg, "limit": "1", "fl": "timestamp", "output": "json"})
            if r.status_code in (429, 503):
                self._back_off()
            if r.status_code != 200:
                return ArchiveResult(reg, None, f"http {r.status_code}")
            rows = r.json()
            if len(rows) < 2:                       # header only / [] = never archived (a real answer, not an error)
                return ArchiveResult(reg, None)
            ts = str(rows[1][0])
            return ArchiveResult(reg, f"{ts[0:4]}-{ts[4:6]}-{ts[6:8]}")
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            self._back_off()
            return ArchiveResult(reg, None, type(exc).__name__)
        except (httpx.HTTPError, ValueError, IndexError, TypeError) as exc:
            return ArchiveResult(reg, None, type(exc).__name__)

    def _back_off(self) -> None:
        WaybackClient._down_until = self._clock() + WAYBACK_BACKOFF_S
        log.warning("Wayback unavailable: lookups answer 'unknown' for %ss", WAYBACK_BACKOFF_S)


# --- annotation --------------------------------------------------------------------------------------------------

def add_reputation(domains: list[dict], archive: Callable[[str], ArchiveResult] | None = None,
                   skip: frozenset[str] | set[str] = frozenset()) -> None:
    """Set the contract fields `wikipedia_rsp`, `tranco_rank`, `first_archived_at`, `reputation_notes` on every Domain.
    `archive=None` switches the Wayback lookup off. `skip`: domains not to look up live (platforms). Never raises."""
    snap = snapshots()
    archived: dict[str, ArchiveResult] = {}
    if archive is not None:
        from concurrent.futures import ThreadPoolExecutor
        todo = [d["domain"] for d in domains if d["domain"] not in skip]
        if todo:
            with ThreadPoolExecutor(max_workers=min(WAYBACK_CONCURRENCY, len(todo)), thread_name_prefix="wayback") as pool:
                for dom, res in zip(todo, pool.map(lambda x: _safe(archive, x), todo)):
                    archived[dom] = res
    for d in domains:
        dom = d["domain"]
        rows = snap.rsp.get(dom, [])
        d["wikipedia_rsp"] = [{**r, "snapshot": snap.rsp_date} for r in rows]
        d["tranco_rank"] = snap.tranco.get(dom)
        arch = archived.get(dom)
        d["first_archived_at"] = arch.first_archived_at if arch else None
        d["reputation_notes"] = _notes(d, snap, arch)


def _safe(archive: Callable[[str], ArchiveResult], dom: str) -> ArchiveResult:
    try:
        return archive(dom)
    except Exception as exc:          # a broken lookup is "unknown", never a failed analysis
        return ArchiveResult(dom, None, type(exc).__name__)


def _notes(d: dict, snap: Snapshots, arch: ArchiveResult | None) -> list[str]:
    notes = []
    for r in d["wikipedia_rsp"]:
        status = " / ".join(_STATUS_TEXT[s] for s in r["statuses"])
        extra = " and on Wikipedia's spam blacklist" if r["blacklisted"] else ""
        last = f", last discussed {r['last_discussed']}" if r["last_discussed"] else ""
        notes.append(f"Wikipedia Perennial Sources rates \"{r['source']}\" {status}{extra}{last} (snapshot {snap.rsp_date})")
    if snap.tranco:
        tag = f"Tranco list {snap.tranco_id}, {snap.tranco_date}"
        if d["tranco_rank"]:
            notes.append(f"Rank {d['tranco_rank']:,} among the most visited sites ({tag}); popularity, not reliability")
        else:
            notes.append(f"Not among the {TRANCO_KEEP:,} most visited sites ({tag})")
    if arch is not None and arch.error is None:
        if arch.first_archived_at is None:
            notes.append("Never captured by the Wayback Machine")
        else:
            notes.append(f"First captured by the Wayback Machine on {arch.first_archived_at}")
            gap = _days_between(arch.first_archived_at, d.get("created_at"))
            if gap is not None and gap >= REREGISTERED_MIN_DAYS:
                notes.append(f"Archived pages predate the current registration ({str(d['created_at'])[:10]}) by "
                             f"{gap // 365} year(s) or more: the domain existed before and was registered again")
    return notes


def _days_between(first_archived: str, created_at: str | None) -> int | None:
    """Days from the first capture to the RDAP registration date (positive = captured BEFORE registration)."""
    if not created_at:
        return None
    try:
        a = datetime.fromisoformat(first_archived).replace(tzinfo=timezone.utc)
        c = datetime.fromisoformat(str(created_at).replace("Z", "+00:00"))
        c = c if c.tzinfo else c.replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return (c - a).days
