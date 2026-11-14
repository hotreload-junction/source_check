"""Engine/session handling. `create_all` is idempotent, so it doubles as our migration for the hackathon."""
from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from .config import get_settings
from .models import Base

_init_lock = threading.RLock()     # init_db can be reached from several threads at once (first requests, job threads)
_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


def make_engine(url: str) -> Engine:
    # connect_timeout: an unreachable database must fail fast, or every request (even the file-backed demo ones that
    # only TRY the database first) would hang until the OS gives up
    kwargs: dict = {"pool_pre_ping": True, "connect_args": {"connect_timeout": 5}}
    if url.startswith("sqlite"):
        kwargs = {"connect_args": {"check_same_thread": False, "timeout": 15}}
    return create_engine(url, **kwargs)


def init_db(url: str | None = None) -> Engine:
    """(Re)bind the global engine and create missing tables."""
    global _engine, _session_factory
    with _init_lock:
        engine = make_engine(url or get_settings().database_url)
        Base.metadata.create_all(engine)
        _engine = engine
        _session_factory = sessionmaker(engine, expire_on_commit=False)
        return engine


def get_engine() -> Engine:
    if _engine is None:
        with _init_lock:
            if _engine is None:
                init_db()
    assert _engine is not None
    return _engine


@contextmanager
def session_scope() -> Iterator[Session]:
    if _session_factory is None:
        with _init_lock:
            if _session_factory is None:      # another thread may have finished the init while we waited
                init_db()
    assert _session_factory is not None
    session = _session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
