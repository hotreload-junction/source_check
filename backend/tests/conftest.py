import httpx
import pytest
from fastapi.testclient import TestClient

from app import cache, db
from app.config import get_settings


@pytest.fixture(autouse=True)
def _never_a_real_database(monkeypatch, tmp_path):
    """Never touch MariaDB or any external API in tests (AGENTS.md section 7).

    Every test points DATABASE_URL at its own SQLite FILE (not ":memory:": job threads and request threads need
    their own connections). The file is only created when a test actually uses the database (lazy init_db)."""
    # A test that reaches the real network through the module-level httpx helpers (client=None) fails LOUDLY: the code
    # under test falls back to them when no client is injected, and tools/check must never call external services.
    def _no_network(*a, **k):
        raise AssertionError("a test tried to call the real network (inject a mock httpx client)")
    for name in ("post", "get", "request", "stream"):
        monkeypatch.setattr(httpx, name, _no_network)
    # the Wayback client (Q2) uses its own httpx.Client, which the patch above does not reach: tests get a stub that
    # never leaves the process (module-level test Settings are built before this fixture, so an env switch is too late)
    from app.pipeline import run as _run
    from app.signals.reputation import ArchiveResult

    class _NoWayback:
        def __init__(self, *a, **k):
            pass

        def lookup(self, domain):
            return ArchiveResult(domain, None, "disabled in tests")
    monkeypatch.setattr(_run, "WaybackClient", _NoWayback)
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'test.db'}")
    # nor the real demo cache / eval metrics: a developer's local precomputed results must not change test outcomes
    monkeypatch.setenv("DEMO_DIR", str(tmp_path / "no-demo"))
    monkeypatch.setenv("METRICS_PATH", str(tmp_path / "no-metrics.json"))
    get_settings.cache_clear()
    db._engine, db._session_factory = None, None
    cache.set_default(cache.NullCache())       # a test opts in to caching explicitly
    yield
    if db._engine is not None:
        db._engine.dispose()
    db._engine, db._session_factory = None, None
    cache.set_default(cache.NullCache())
    get_settings.cache_clear()


@pytest.fixture
def sqlite_db(_never_a_real_database):
    return db.init_db()


@pytest.fixture
def client(sqlite_db):
    from app.main import app

    with TestClient(app) as c:
        yield c
