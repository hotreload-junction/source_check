"""B9: demo cache. The three demo buttons load PRECOMPUTED results, so they work with every external API (and the
database) down. Cases: backend/demo/cases.json; results: backend/demo/results/<key>.json (the contract `Result`)."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select

from . import db
from .config import Settings, get_settings
from .fetch.urls import InvalidURL, normalize_url
from .models import Analysis, utcnow

log = logging.getLogger("citetrace.demo")
PREFIX = "demo-"
DEFAULT_DIR = Path(__file__).resolve().parents[1] / "demo"
LABELS = {"well_sourced", "weakly_sourced", "propaganda_pattern", "insufficient_evidence"}


def demo_dir(settings: Settings | None = None) -> Path:
    s = settings or get_settings()
    return Path(s.demo_dir) if s.demo_dir else DEFAULT_DIR


def job_id(key: str) -> str:
    return f"{PREFIX}{key}"


def load_cases(settings: Settings | None = None) -> list[dict]:
    """Cases that have a precomputed result. A case without one is skipped (a button that cannot load is worse)."""
    d = demo_dir(settings)
    try:
        cases = json.loads((d / "cases.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        log.warning("no usable demo cases in %s: %s", d, exc)
        return []
    out = []
    for c in cases:
        if not isinstance(c, dict) or not all(isinstance(c.get(k), str) and c[k] for k in ("key", "label", "url")):
            log.warning("skipping malformed demo case: %r", c)
            continue
        result = load_result(c["key"], settings)
        if result is None:
            log.info("demo case %s has no precomputed result yet", c["key"])
            continue
        out.append({**c, "result": result})
    return out


def load_result(key: str, settings: Settings | None = None) -> dict | None:
    if not key.replace("-", "").replace("_", "").isalnum():     # keys become file names and job ids
        return None
    try:
        data = json.loads((demo_dir(settings) / "results" / f"{key}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if isinstance(data, dict) and "result" in data and "article" not in data:    # a full Analysis (e.g. from the CLI)
        data = data["result"]
    ok = isinstance(data, dict) and {"article", "claims", "sources", "domains", "graph", "signals", "verdict", "audit"} <= set(data)
    return data if ok else None


def demo_list(settings: Settings | None = None) -> list[dict]:
    """Contract `GET /api/demo`. `expected_verdict` = the case file's value, else what the frozen result says."""
    out = []
    for c in load_cases(settings):
        exp = c.get("expected_verdict")
        out.append({"key": c["key"], "label": c["label"], "url": c["url"], "job_id": job_id(c["key"]),
                    "expected_verdict": exp if exp in LABELS else c["result"]["verdict"]["label"]})
    return out


def analysis_for(job: str, settings: Settings | None = None) -> dict | None:
    """Contract `Analysis` of a demo job straight from the files (no database)."""
    if not job.startswith(PREFIX):
        return None
    result = load_result(job[len(PREFIX):], settings)
    if result is None:
        return None
    stamp = datetime.fromtimestamp((demo_dir(settings) / "results" / f"{job[len(PREFIX):]}.json").stat().st_mtime, timezone.utc)
    iso = stamp.strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"job_id": job, "status": "done", "progress": {"step": "verdict", "message": "Done", "pct": 100},
            "error": None, "created_at": iso, "finished_at": iso, "result": result}


def case_for_url(url: str, settings: Settings | None = None) -> str | None:
    """job id of the demo case whose URL equals `url` (normalised), for answering POSTs from the cache."""
    try:
        norm = normalize_url(url)
    except InvalidURL:
        return None
    for c in load_cases(settings):
        try:
            if normalize_url(c["url"]) == norm:
                return job_id(c["key"])
        except InvalidURL:
            continue
    return None


def seed(settings: Settings | None = None) -> int:
    """Copy the frozen results into the database as done jobs `demo-<key>` (idempotent), so the normal cache path
    (`POST /api/analyses` with a demo URL, `GET`, SSE replay) serves them. Returns how many rows were written."""
    n = 0
    with db.session_scope() as s:
        for c in load_cases(settings):
            jid = job_id(c["key"])
            norm = normalize_url(c["url"])
            row = s.get(Analysis, jid) or s.scalar(select(Analysis).where(Analysis.id == jid))
            if row is None:
                row = Analysis(id=jid, url=c["url"], normalized_url=norm)
                s.add(row)
            row.url, row.normalized_url, row.status = c["url"], norm, "done"
            row.step, row.message, row.pct, row.error = "verdict", "Done", 100, None
            row.result = c["result"]
            row.finished_at = row.finished_at or utcnow()
            n += 1
    return n
