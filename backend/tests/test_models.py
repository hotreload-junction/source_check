from sqlalchemy import inspect

from app import db
from app.models import Analysis, ClaimRecord, ClaimVerdictRecord, SourceRecord


def test_tables_created(sqlite_db):
    names = set(inspect(sqlite_db).get_table_names())
    assert {"analyses", "claims", "sources", "claim_verdicts", "domains"} <= names


def test_audit_trail_roundtrip():
    with db.session_scope() as s:
        a = Analysis(id="a1", url="https://x.test/a", normalized_url="x.test/a")
        c = ClaimRecord(claim_id="c1", text="Claim text")
        c.verdicts.append(ClaimVerdictRecord(
            source_id="s1", verdict="supported", quote_original="verbatim", fuzzy_score=97.5,
            llm_verdict="supported", model="m", prompt_version="v1",
        ))
        a.claims.append(c)
        a.sources.append(SourceRecord(source_id="s1", kind="link", url="https://y.test"))
        s.add(a)
    with db.session_scope() as s:
        v = s.get(Analysis, "a1").claims[0].verdicts[0]
        assert (v.fuzzy_score, v.model, v.prompt_version, v.quote_original) == (97.5, "m", "v1", "verbatim")
        assert s.get(Analysis, "a1").status == "queued"
