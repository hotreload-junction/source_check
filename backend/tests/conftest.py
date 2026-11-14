import pytest
from fastapi.testclient import TestClient

from app import db
from app.config import get_settings


@pytest.fixture(autouse=True)
def sqlite_db(monkeypatch):
    """Never touch MariaDB or any external API in tests (AGENTS.md section 7)."""
    monkeypatch.setenv("DATABASE_URL", "sqlite://")
    get_settings.cache_clear()
    engine = db.init_db("sqlite://")
    yield engine
    engine.dispose()
    get_settings.cache_clear()


@pytest.fixture
def client():
    from app.main import app

    with TestClient(app) as c:
        yield c
