"""B8: analysis endpoints (contracts/api.md)."""
from __future__ import annotations

import json
from typing import Iterator

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel
from sqlalchemy.exc import SQLAlchemyError

from . import demo
from .fetch.urls import InvalidURL, normalize_url
from .jobs import Busy, JobManager
from .methodology import load_methodology

router = APIRouter(prefix="/api")


class StartBody(BaseModel):
    url: str
    force: bool = False


def _jobs(request: Request) -> JobManager:
    return request.app.state.jobs


def _db_down() -> JSONResponse:
    return JSONResponse(status_code=503, content={"error": "database_unavailable",
                                                  "detail": "The database is not reachable right now. Try again shortly."})


def _load(jobs: JobManager, job_id: str) -> dict | None:
    """Demo jobs come straight from the precomputed files: no database round trip, so they are instant and survive a
    database outage (the files are the source of truth, the database copy only serves the normal cache path)."""
    if job_id.startswith(demo.PREFIX):
        from_file = demo.analysis_for(job_id, jobs.settings)
        if from_file is not None:
            return from_file
    return jobs.get(job_id)


@router.get("/demo")
def demo_cases(request: Request) -> JSONResponse:
    return JSONResponse(content=demo.demo_list(_jobs(request).settings))


@router.get("/methodology")
def methodology(request: Request) -> JSONResponse:
    return JSONResponse(content=load_methodology(_jobs(request).settings))


@router.post("/analyses")
def start_analysis(body: StartBody, request: Request) -> JSONResponse:
    try:
        norm = normalize_url(body.url)
    except InvalidURL as exc:
        return JSONResponse(status_code=422, content={"error": "invalid_url", "detail": str(exc)})
    jobs = _jobs(request)
    try:
        if not body.force:
            demo_job = demo.case_for_url(body.url, jobs.settings)      # answered from files: works with the database down
            if demo_job:
                return JSONResponse(status_code=200, content={"job_id": demo_job, "status": "done", "cached": True})
            cached = jobs.find_cached(norm)
            if cached:
                return JSONResponse(status_code=200, content={"job_id": cached, "status": "done", "cached": True})
            active = jobs.find_active(norm)       # same article already being analysed: join it, do not start a twin
            if active:
                return JSONResponse(status_code=202, content={"job_id": active, "status": "running", "cached": False})
        job_id = jobs.submit(body.url.strip(), norm, refresh=body.force)   # force = recompute every step, not a replay
    except Busy as exc:
        return JSONResponse(status_code=429, content={
            "error": "busy", "detail": f"{exc.running} analyses running, try again in a minute"})
    except SQLAlchemyError:
        return _db_down()
    return JSONResponse(status_code=202, content={"job_id": job_id, "status": "queued", "cached": False})


@router.get("/analyses/{job_id}")
def get_analysis(job_id: str, request: Request) -> JSONResponse:
    try:
        analysis = _load(_jobs(request), job_id)
    except SQLAlchemyError:
        return _db_down()
    if analysis is None:
        return JSONResponse(status_code=404, content={"error": "not_found", "detail": "Unknown analysis id"})
    return JSONResponse(content=analysis)


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.get("/analyses/{job_id}/events", response_model=None)
def analysis_events(job_id: str, request: Request) -> StreamingResponse | JSONResponse:
    jobs = _jobs(request)
    try:
        current = _load(jobs, job_id)
    except SQLAlchemyError:
        return _db_down()
    if current is None:
        return JSONResponse(status_code=404, content={"error": "not_found", "detail": "Unknown analysis id"})

    def stream() -> Iterator[str]:
        ev = jobs.events(job_id)
        if ev is None:     # finished before this process started (or evicted): replay the outcome only
            if current["status"] == "done":
                yield _sse("progress", {"step": "verdict", "message": "Done", "pct": 100})
                yield _sse("done", current)
            elif current["status"] == "failed":
                yield _sse("error", {"message": current["error"] or "The analysis failed."})
            return
        index = 0
        while True:
            items, closed = ev.wait_from(index, timeout=15)
            for event, data in items:
                yield _sse(event, data)
            index += len(items)
            if closed and not items:
                return
            if not items:
                yield ": keepalive\n\n"      # keeps proxies from closing an idle stream

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"})
