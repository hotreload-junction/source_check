"""Runs only against a real MariaDB: `TEST_MARIADB_URL=mysql+pymysql://user:pw@127.0.0.1:3306/db pytest tests/test_mariadb_integration.py`.

Skipped in tools/check and CI (AGENTS.md section 7: no external services there). Uses unique ids and removes its rows.
"""
import os
import uuid

import pytest

URL = os.environ.get("TEST_MARIADB_URL")
pytestmark = pytest.mark.skipif(not URL, reason="TEST_MARIADB_URL not set (needs a real MariaDB)")


def test_schema_roundtrip_utf8_json_and_large_text_on_mariadb():
    from sqlalchemy import inspect

    from app import db
    from app.models import Analysis, ClaimRecord, ClaimVerdictRecord, DomainRecord, SourceRecord

    engine = db.init_db(URL)       # create_all is idempotent: safe on an existing database
    assert {"analyses", "claims", "sources", "claim_verdicts", "domains"} <= set(inspect(engine).get_table_names())
    assert "source_url" in {c["name"] for c in inspect(engine).get_columns("claim_verdicts")}
    job = "it" + uuid.uuid4().hex[:8]
    dom = f"{job}.example"
    try:
        with db.session_scope() as s:
            a = Analysis(id=job, url="https://x.test/a", normalized_url=f"{job}.test/a", result={"ru": "Привет 🌍", "n": [1, {"k": None}]})
            c = ClaimRecord(claim_id="c1", text="Утверждение 🌍")
            c.verdicts.append(ClaimVerdictRecord(source_id="s1", source_url="https://y.test", verdict="supported",
                                                 quote_original="Привет, мир 🌍", fuzzy_score=97.5, model="m", prompt_version="v4"))
            a.claims.append(c)
            a.sources.append(SourceRecord(source_id="s1", kind="link", text="Б" * 3_000_000))
            s.add(a)
            s.add(DomainRecord(domain=dom, analytics_ids=["G-X"], adsense_ids=[]))
        with db.session_scope() as s:
            a = s.get(Analysis, job)
            assert a.result == {"ru": "Привет 🌍", "n": [1, {"k": None}]}
            assert a.claims[0].verdicts[0].quote_original == "Привет, мир 🌍" and a.claims[0].verdicts[0].fuzzy_score == 97.5
            assert len(a.sources[0].text) == 3_000_000
    finally:
        with db.session_scope() as s:
            for obj in (s.get(Analysis, job), s.get(DomainRecord, dom)):
                if obj is not None:
                    s.delete(obj)
