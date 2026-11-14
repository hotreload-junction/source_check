"""Step cache: makes a finished analysis reproducible.

Measured (live, same article, same prompt): the LLM calls are NOT deterministic even at temperature 0 (claim sets of
repeated extractions overlapped 0.37 on average), while the AI router returns byte-identical requests from ITS cache
for a short while. We cache every expensive, non-deterministic step ourselves, by the hash of its full input:

  llm    model + system + user prompt  -> the completion (extraction, domain suggestion, verification)
  fetch  normalised URL                -> the page as we read it (text, html, title, date)
  html   URL                           -> raw HTML of a cited source (infra signals)
  exa    request body                  -> the search results

Second run of the same article = same inputs = same outputs = same verdict. `refresh` skips the READ (everything is
recomputed and stored again), which is what `force` and the CLI's --refresh mean. Nothing here is required for
correctness: a broken or unreachable database just means "no cache" (circuit breaker), never a failed analysis.
"""
from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from datetime import timedelta
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from . import db
from .models import StepCacheRecord, utcnow

log = logging.getLogger("citetrace.cache")

# A failed read (blocked, paywalled, unreachable, search error) is cached briefly: an immediate replay of a finished
# article must see the SAME failures (and not re-wait for the same timeouts); a retry after a few hours may succeed.
FAILURE_TTL_S = 6 * 3600


def make_key(*parts: Any) -> str:
    return hashlib.sha256(json.dumps(parts, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


class StepCache(Protocol):
    def get(self, kind: str, key: str) -> Any | None: ...
    def put(self, kind: str, key: str, value: Any, ttl_s: float | None = None) -> None: ...


class NullCache:
    def get(self, kind: str, key: str) -> Any | None:
        return None

    def put(self, kind: str, key: str, value: Any, ttl_s: float | None = None) -> None:
        pass


class MemoryCache:
    """In-process cache (tests, scripts)."""

    def __init__(self) -> None:
        self.data: dict[tuple[str, str], Any] = {}
        self.expires: dict[tuple[str, str], float] = {}
        self.hits = self.misses = 0

    def get(self, kind: str, key: str) -> Any | None:
        v = self.data.get((kind, key))
        if v is not None and self.expires.get((kind, key), float("inf")) < time.time():
            v = None
        self.hits, self.misses = self.hits + (v is not None), self.misses + (v is None)
        return json.loads(json.dumps(v)) if v is not None else None

    def put(self, kind: str, key: str, value: Any, ttl_s: float | None = None) -> None:
        self.data[(kind, key)] = json.loads(json.dumps(value))
        if ttl_s is not None:
            self.expires[(kind, key)] = time.time() + ttl_s
        else:
            self.expires.pop((kind, key), None)


class DbCache:
    """MariaDB-backed. Any database error trips a circuit breaker for `cooldown_s` seconds: the analysis simply runs
    uncached instead of failing or stalling on connect timeouts."""

    def __init__(self, ttl_hours: float | None = None, cooldown_s: float = 60.0):
        self.ttl = timedelta(hours=ttl_hours) if ttl_hours else None
        self.cooldown_s = cooldown_s
        self._down_until = 0.0
        self._lock = threading.Lock()

    def _trip(self, exc: Exception) -> None:
        with self._lock:
            if time.monotonic() >= self._down_until:
                log.warning("step cache disabled for %.0fs (database error: %s)", self.cooldown_s, type(exc).__name__)
            self._down_until = time.monotonic() + self.cooldown_s

    def _ok(self) -> bool:
        return time.monotonic() >= self._down_until

    def get(self, kind: str, key: str) -> Any | None:
        if not self._ok():
            return None
        try:
            with db.session_scope() as s:
                row = s.get(StepCacheRecord, (kind, key))
                if row is None or (self.ttl and utcnow() - row.created_at > self.ttl):
                    return None
                value = json.loads(row.value)
                if isinstance(value, dict) and "__exp" in value:          # entry with its own, shorter lifetime
                    if value["__exp"] < time.time():
                        return None
                    return value["__v"]
                return value
        except (SQLAlchemyError, ValueError) as exc:
            self._trip(exc)
            return None

    def put(self, kind: str, key: str, value: Any, ttl_s: float | None = None) -> None:
        if not self._ok():
            return
        try:
            if ttl_s is not None:
                value = {"__exp": time.time() + ttl_s, "__v": value}
            text = json.dumps(value, ensure_ascii=False)
            with db.session_scope() as s:
                row = s.get(StepCacheRecord, (kind, key))
                if row is None:
                    s.add(StepCacheRecord(kind=kind, key=key, value=text))
                else:
                    row.value, row.created_at = text, utcnow()
        except SQLAlchemyError as exc:
            self._trip(exc)


class RefreshCache:
    """Recompute everything (no reads) but store the fresh results: `force` / `--refresh`."""

    def __init__(self, inner: StepCache):
        self.inner = inner

    def get(self, kind: str, key: str) -> Any | None:
        return None

    def put(self, kind: str, key: str, value: Any, ttl_s: float | None = None) -> None:
        self.inner.put(kind, key, value, ttl_s)


_default: StepCache = NullCache()


def set_default(cache: StepCache) -> None:
    global _default
    _default = cache


def get_default() -> StepCache:
    return _default


def for_run(refresh: bool = False) -> StepCache:
    return RefreshCache(_default) if refresh else _default
