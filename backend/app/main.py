from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy import text

from . import cache, db, demo
from .api import router
from .config import get_version
from .jobs import JobManager

log = logging.getLogger("citetrace")


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.jobs = JobManager()
    if app.state.jobs.settings.step_cache:
        cache.set_default(cache.DbCache(ttl_hours=app.state.jobs.settings.step_cache_ttl_h or None))
    try:
        db.init_db()
        stale = app.state.jobs.recover_stale()
        if stale:
            log.warning("marked %d unfinished analyses from the previous run as failed", stale)
        log.info("demo cache: %d case(s) seeded", demo.seed(app.state.jobs.settings))
    except Exception as exc:  # health, /api/demo and /api/methodology must still answer: they read files only
        log.warning("database unavailable at startup: %s", exc)
    yield


app = FastAPI(title="CiteTrace", lifespan=lifespan)
app.include_router(router)


@app.get("/api/health")
def health() -> dict:
    return {"ok": True, "version": get_version()}


@app.get("/api/health/db")
def health_db() -> dict:
    """Separate from /api/health so the smoke test passes with the DB down (demo runs offline)."""
    try:
        with db.get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
        return {"ok": True}
    except Exception as exc:
        return {"ok": False, "error": type(exc).__name__}
