from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy import text

from . import db
from .config import get_version

log = logging.getLogger("citetrace")


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        db.init_db()
    except Exception as exc:  # health must still answer; demo cache needs no DB writes later
        log.warning("database unavailable at startup: %s", exc)
    yield


app = FastAPI(title="CiteTrace", lifespan=lifespan)


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
