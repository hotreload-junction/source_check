"""B8: analysis jobs. One thread per job, status + result in the database, events in memory for SSE.

No queue (DECISIONS Q16): `POST /api/analyses` either starts a job right away or answers 429 when
MAX_CONCURRENT_ANALYSES are already running. Results are cached by normalised URL.
"""
from __future__ import annotations

import logging
import threading
import uuid
from collections import OrderedDict
from datetime import datetime, timezone
from typing import Callable

from sqlalchemy import func, select

from . import db
from .config import Settings, get_settings
from .models import Analysis, ClaimRecord, SourceRecord, utcnow
from .pipeline.assess import audit_records
from .pipeline.run import AnalysisError, AnalysisOutput, analyze

log = logging.getLogger("citetrace.jobs")
ACTIVE = ("queued", "running")


class Busy(Exception):
    def __init__(self, running: int):
        super().__init__(f"{running} analyses running")
        self.running = running


class JobEvents:
    """Append-only event log of one job; late SSE subscribers replay it from the start."""

    def __init__(self) -> None:
        self.items: list[tuple[str, dict]] = []
        self.closed = False
        self._cond = threading.Condition()

    def add(self, event: str, data: dict) -> None:
        with self._cond:
            self.items.append((event, data))
            self._cond.notify_all()

    def close(self) -> None:
        with self._cond:
            self.closed = True
            self._cond.notify_all()

    def wait_from(self, index: int, timeout: float) -> tuple[list[tuple[str, dict]], bool]:
        with self._cond:
            if len(self.items) <= index and not self.closed:
                self._cond.wait(timeout)
            return self.items[index:], self.closed


def iso(dt: datetime | None) -> str | None:
    return dt.replace(tzinfo=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if dt else None


def analysis_dict(row: Analysis) -> dict:
    """Contract `Analysis`."""
    return {"job_id": row.id, "status": row.status,
            "progress": {"step": row.step or "", "message": row.message or "", "pct": row.pct or 0},
            "error": row.error, "created_at": iso(row.created_at), "finished_at": iso(row.finished_at),
            "result": row.result if row.status == "done" else None}


class JobManager:
    def __init__(self, settings: Settings | None = None, analyze_fn: Callable[..., AnalysisOutput] | None = None,
                 keep_events: int = 50):
        self.settings = settings or get_settings()
        self.analyze_fn = analyze_fn or analyze
        self._events: OrderedDict[str, JobEvents] = OrderedDict()
        self._keep = keep_events
        self._lock = threading.Lock()       # makes "count running + insert" atomic

    # ---------------------------------------------------------------- lifecycle
    def recover_stale(self) -> int:
        """Jobs left queued/running by a previous process can never finish: mark them failed."""
        with db.session_scope() as s:
            rows = s.scalars(select(Analysis).where(Analysis.status.in_(ACTIVE))).all()
            for r in rows:
                r.status, r.error, r.finished_at = "failed", "The server restarted while this analysis was running.", utcnow()
            return len(rows)

    # ---------------------------------------------------------------- queries
    def running_count(self) -> int:
        with db.session_scope() as s:
            return s.scalar(select(func.count()).select_from(Analysis).where(Analysis.status.in_(ACTIVE))) or 0

    def find_cached(self, normalized_url: str) -> str | None:
        with db.session_scope() as s:
            return s.scalar(select(Analysis.id).where(Analysis.normalized_url == normalized_url, Analysis.status == "done")
                            .order_by(Analysis.finished_at.desc()).limit(1))

    def find_active(self, normalized_url: str) -> str | None:
        with db.session_scope() as s:
            return s.scalar(select(Analysis.id).where(Analysis.normalized_url == normalized_url, Analysis.status.in_(ACTIVE))
                            .order_by(Analysis.created_at.desc()).limit(1))

    def get(self, job_id: str) -> dict | None:
        with db.session_scope() as s:
            row = s.get(Analysis, job_id)
            return analysis_dict(row) if row else None

    def events(self, job_id: str) -> JobEvents | None:
        with self._lock:
            return self._events.get(job_id)

    # ---------------------------------------------------------------- start
    def submit(self, url: str, normalized_url: str, refresh: bool = False) -> str:
        """Create the job row and start its thread. Raises Busy when the concurrency limit is reached."""
        with self._lock:
            running = self.running_count()
            if running >= self.settings.max_concurrent_analyses:
                raise Busy(running)
            job_id = uuid.uuid4().hex[:8]
            with db.session_scope() as s:
                s.add(Analysis(id=job_id, url=url, normalized_url=normalized_url, status="queued",
                               step="queued", message="Waiting to start", pct=0))
            ev = JobEvents()
            self._events[job_id] = ev
            while len(self._events) > self._keep:
                self._events.popitem(last=False)
        threading.Thread(target=self._run, args=(job_id, url, ev, refresh), name=f"job-{job_id}", daemon=True).start()
        return job_id

    # ---------------------------------------------------------------- the job
    def _emit(self, job_id: str, ev: JobEvents) -> Callable[[str, dict], None]:
        def emit(event: str, data: dict) -> None:
            ev.add(event, data)
            if event == "progress":
                try:
                    with db.session_scope() as s:
                        row = s.get(Analysis, job_id)
                        if row and row.status in ACTIVE:
                            row.status, row.step, row.message, row.pct = "running", data["step"], data["message"], data["pct"]
                except Exception:   # progress is cosmetic; never kill the analysis for it
                    log.warning("could not store progress for %s", job_id, exc_info=True)
        return emit

    def _run(self, job_id: str, url: str, ev: JobEvents, refresh: bool = False) -> None:
        try:
            out = self.analyze_fn(url, settings=self.settings, emit=self._emit(job_id, ev), refresh=refresh)
            self._store_success(job_id, out)
            with db.session_scope() as s:
                done = analysis_dict(s.get(Analysis, job_id))
            ev.add("done", done)
        except AnalysisError as exc:
            self._fail(job_id, ev, str(exc))
        except Exception:
            log.exception("analysis %s crashed", job_id)
            self._fail(job_id, ev, "Something went wrong while analysing this article. Please try again.")
        finally:
            ev.close()

    def _fail(self, job_id: str, ev: JobEvents, message: str) -> None:
        try:
            with db.session_scope() as s:
                row = s.get(Analysis, job_id)
                if row is not None:
                    row.status, row.error, row.finished_at = "failed", message, utcnow()
        except Exception:
            log.exception("could not store failure of %s", job_id)
        ev.add("error", {"message": message})

    def _store_success(self, job_id: str, out: AnalysisOutput) -> None:
        """Result + the audit trail in ONE transaction: a verdict is never shown without its evidence rows."""
        fetches = {sa.url: sa.fetch for a in out.assessments for sa in a.sources if sa.url and sa.fetch}
        by_url = {r["url"]: r["id"] for r in out.source_rows if r["url"]}
        with db.session_scope() as s:
            row = s.get(Analysis, job_id)
            for sr in out.source_rows:
                f = fetches.get(sr["url"]) if sr["url"] else None
                row.sources.append(SourceRecord(
                    source_id=sr["id"], url=sr["url"], domain=sr["domain"], kind=sr["kind"],
                    attribution_text=sr["attribution_text"], depth=sr["depth"], parent_source_id=sr["parent_source_id"],
                    fetch_status=sr["fetch_status"], fetched_via=sr["fetched_via"], text=f.text if f else None))
            for a, cd in zip(out.assessments, out.result["claims"]):
                rec = ClaimRecord(claim_id=cd["id"], text=cd["text"], char_start=cd["char_start"], char_end=cd["char_end"])
                row.claims.append(rec)
                s.flush()
                for v in audit_records(a, rec.pk, lambda sa: by_url.get(sa.url)):
                    rec.verdicts.append(v)
            row.result, row.status, row.step, row.message, row.pct, row.finished_at = (
                out.result, "done", "verdict", "Done", 100, utcnow())
