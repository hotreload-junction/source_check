"""B8: analysis endpoints (contracts/api.md)."""
from __future__ import annotations

import json
from typing import Iterator

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from .fetch.urls import InvalidURL, normalize_url
from .jobs import Busy, JobManager

router = APIRouter(prefix="/api")


class StartBody(BaseModel):
    url: str
    force: bool = False


def _jobs(request: Request) -> JobManager:
    return request.app.state.jobs


@router.post("/analyses")
def start_analysis(body: StartBody, request: Request) -> JSONResponse:
    try:
        norm = normalize_url(body.url)
    except InvalidURL as exc:
        return JSONResponse(status_code=422, content={"error": "invalid_url", "detail": str(exc)})
    jobs = _jobs(request)
    if not body.force:
        cached = jobs.find_cached(norm)
        if cached:
            return JSONResponse(status_code=200, content={"job_id": cached, "status": "done", "cached": True})
        active = jobs.find_active(norm)           # same article already being analysed: join it, do not start a twin
        if active:
            return JSONResponse(status_code=202, content={"job_id": active, "status": "running", "cached": False})
    try:
        job_id = jobs.submit(body.url.strip(), norm)
    except Busy as exc:
        return JSONResponse(status_code=429, content={
            "error": "busy", "detail": f"{exc.running} analyses running, try again in a minute"})
    return JSONResponse(status_code=202, content={"job_id": job_id, "status": "queued", "cached": False})


@router.get("/analyses/{job_id}")
def get_analysis(job_id: str, request: Request) -> JSONResponse:
    analysis = _jobs(request).get(job_id)
    if analysis is None:
        return JSONResponse(status_code=404, content={"error": "not_found", "detail": "Unknown analysis id"})
    return JSONResponse(content=analysis)


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.get("/analyses/{job_id}/events", response_model=None)
def analysis_events(job_id: str, request: Request) -> StreamingResponse | JSONResponse:
    jobs = _jobs(request)
    current = jobs.get(job_id)
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
