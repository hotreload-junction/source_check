"""B9: demo cache + methodology. Everything here must work with the database and every external API down."""
import json
from pathlib import Path

import pytest
from sqlalchemy.exc import OperationalError

from app import db, demo
from app.config import Settings, get_settings
from app.jobs import JobManager
from app.methodology import load_methodology
from app.models import Analysis
from app.verdict import thresholds

import test_run as tr


def fake_result(label="propaganda_pattern", title="T"):
    return {"article": {"url": "https://x.test/a", "domain": "x.test", "title": title, "published_at": None, "text_markdown": "# T\n\nBody."},
            "claims": [], "sources": [], "domains": [], "graph": {"nodes": [], "edges": []},
            "signals": {"evidence": {"fired": False, "claim_ids": [], "detail": ""}, "infrastructure": {"fired": False, "reasons": []},
                        "circle": {"fired": False, "path": None}},
            "verdict": {"label": label, "summary": "s"}, "audit": {"model_extract": "a", "model_verify": "b", "prompt_version": "v", "fuzzy_threshold": 90, "depth": 1, "duration_s": 1.0}}


@pytest.fixture
def demo_dir(tmp_path):
    d = tmp_path / "demo"
    (d / "results").mkdir(parents=True)
    cases = [{"key": "pravda", "label": "Pravda case", "url": "https://www.pravda.example/story?utm_source=a", "expected_verdict": "propaganda_pattern"},
             {"key": "wire", "label": "Wire case", "url": "https://wire.example/story"},
             {"key": "blog", "label": "No result yet", "url": "https://blog.example/post"},
             {"label": "malformed, no key", "url": "https://z.example"}]
    (d / "cases.json").write_text(json.dumps(cases))
    (d / "results" / "pravda.json").write_text(json.dumps(fake_result("propaganda_pattern")))
    (d / "results" / "wire.json").write_text(json.dumps({"job_id": "cli", "status": "done", "result": fake_result("well_sourced")}))   # a CLI Analysis
    return d


@pytest.fixture
def jobs(client, demo_dir):
    m = client.app.state.jobs
    m.settings = Settings(_env_file=None, demo_dir=str(demo_dir), max_concurrent_analyses=2)
    return m


def test_demo_list_has_only_cases_with_results_and_contract_fields(client, jobs):
    r = client.get("/api/demo")
    assert r.status_code == 200
    assert r.json() == [
        {"key": "pravda", "label": "Pravda case", "url": "https://www.pravda.example/story?utm_source=a", "job_id": "demo-pravda", "expected_verdict": "propaganda_pattern"},
        {"key": "wire", "label": "Wire case", "url": "https://wire.example/story", "job_id": "demo-wire", "expected_verdict": "well_sourced"},   # from the frozen result
    ]


def test_missing_or_broken_cases_file_gives_an_empty_list_not_an_error(client, jobs, demo_dir):
    (demo_dir / "cases.json").write_text("{not json")
    assert client.get("/api/demo").json() == []
    (demo_dir / "cases.json").unlink()
    assert client.get("/api/demo").json() == []


def test_result_keys_cannot_escape_the_results_directory(demo_dir):
    s = Settings(_env_file=None, demo_dir=str(demo_dir))
    assert demo.load_result("../cases", s) is None and demo.load_result("a/b", s) is None
    assert demo.analysis_for("demo-../cases", s) is None


def test_incomplete_result_file_is_ignored(demo_dir):
    (demo_dir / "results" / "pravda.json").write_text(json.dumps({"article": {}}))
    assert [c["key"] for c in demo.load_cases(Settings(_env_file=None, demo_dir=str(demo_dir)))] == ["wire"]


def break_database(jobs, monkeypatch):
    def boom(*a, **k):
        raise OperationalError("select 1", {}, Exception("Can't connect to MySQL server"))
    for name in ("get", "find_cached", "find_active", "submit"):
        monkeypatch.setattr(jobs, name, boom)


def test_demo_works_with_the_database_down(client, jobs, monkeypatch):
    break_database(jobs, monkeypatch)
    assert client.get("/api/demo").status_code == 200 and len(client.get("/api/demo").json()) == 2
    a = client.get("/api/analyses/demo-pravda")
    assert a.status_code == 200 and a.json()["status"] == "done" and a.json()["result"]["verdict"]["label"] == "propaganda_pattern"
    assert set(a.json()) == {"job_id", "status", "progress", "error", "created_at", "finished_at", "result"}
    p = client.post("/api/analyses", json={"url": "https://pravda.example/story"})       # the demo URL, another spelling
    assert p.status_code == 200 and p.json() == {"job_id": "demo-pravda", "status": "done", "cached": True}
    with client.stream("GET", "/api/analyses/demo-wire/events") as s:
        text = "".join(s.iter_text())
    assert "event: done" in text and "well_sourced" in text
    assert client.get("/api/methodology").status_code == 200


def test_demo_jobs_never_touch_the_database(client, jobs, monkeypatch):
    calls = []
    monkeypatch.setattr(jobs, "get", lambda *a, **k: calls.append(a) or None)
    assert client.get("/api/analyses/demo-pravda").status_code == 200
    with client.stream("GET", "/api/analyses/demo-pravda/events") as s:
        assert "event: done" in "".join(s.iter_text())
    assert calls == []
    assert client.get("/api/analyses/demo-unknown").status_code == 404 and len(calls) == 1   # only an unknown id asks the DB


def test_other_requests_get_a_clean_503_when_the_database_is_down(client, jobs, monkeypatch):
    break_database(jobs, monkeypatch)
    for r in (client.get("/api/analyses/abc12345"), client.post("/api/analyses", json={"url": "https://other.example/a"})):
        assert r.status_code == 503 and r.json()["error"] == "database_unavailable"
    assert client.get("/api/analyses/demo-nope").status_code == 503      # not a file-backed demo id: the DB would have to answer


def test_seed_copies_results_into_the_database_idempotently(client, jobs):
    assert demo.seed(jobs.settings) == 2 and demo.seed(jobs.settings) == 2
    with db.session_scope() as s:
        rows = s.query(Analysis).filter(Analysis.id.like("demo-%")).all()
        assert sorted(r.id for r in rows) == ["demo-pravda", "demo-wire"] and all(r.status == "done" and r.result for r in rows)
    a = client.get("/api/analyses/demo-wire").json()
    assert a["status"] == "done" and a["result"]["verdict"]["label"] == "well_sourced"
    # normal cache path: the demo URL never triggers a live run (no pipeline fake installed => it would crash if it did)
    jobs.analyze_fn = lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not run"))
    p = client.post("/api/analyses", json={"url": "https://wire.example/story"})
    assert p.status_code == 200 and p.json()["job_id"] == "demo-wire"


def test_force_still_runs_live_for_a_demo_url(client, jobs):
    demo.seed(jobs.settings)
    pages = {"https://news-front.net/report-123": tr.src("https://news-front.net/report-123", tr.SRC_BAD, "<html></html>"),
             "https://ministry.gov.uk/report": tr.src("https://ministry.gov.uk/report", tr.SRC_OK, "<html></html>")}
    from app.pipeline.run import analyze
    jobs.analyze_fn = lambda url, settings=None, emit=None, **k: analyze(
        tr.ARTICLE_URL, fetcher=tr.FakeFetcher(tr.art(), pages), llm=tr.router(tr.EXTRACT, tr.verifier), settings=tr.SETTINGS,
        rdap=tr.FakeRdap({}), emit=emit)
    r = client.post("/api/analyses", json={"url": "https://wire.example/story", "force": True})
    assert r.status_code == 202 and not r.json()["job_id"].startswith("demo-")


# ------------------------------------------------------------------------------------------------ methodology
VALID = {"_note": "x", "generated_at": "2026-11-15T01:00:00Z", "verify_model": "gemini-4-argon", "prompt_version": "v4",
         "pairs": {"n": 40, "n_cross_language": 12, "precision": 0.9, "recall": 0.8, "confusion": {"tp": 16, "fp": 2, "fn": 4, "tn": 18}},
         "articles": {"n_network": 10, "n_wire": 10, "fpr_wire": 0.1, "tpr_network": 0.7, "confusion": {"tp": 7, "fp": 1, "fn": 3, "tn": 9}},
         "thresholds": {"min_bad_claims": 2, "fuzzy": 88}, "tuned_on": "set A", "measured_on": "set B",
         "error_cases": [{"kind": "fp", "url_or_pair_id": "p7", "expected": "supported", "got": "distorted", "note": "n"}]}


def settings_with_metrics(tmp_path, content=None):
    p = tmp_path / "metrics.json"
    if content is not None:
        p.write_text(content if isinstance(content, str) else json.dumps(content))
    return Settings(_env_file=None, metrics_path=str(p), model_verify="mv", prompt_version="vT", fuzzy_threshold=90)


def test_no_metrics_file_is_an_honest_empty_state_never_invented_numbers(tmp_path):
    m = load_methodology(settings_with_metrics(tmp_path))
    for k in ("tp", "fp", "fn", "tn"):
        assert m["pairs"]["confusion"][k] == 0 and m["articles"]["confusion"][k] == 0
    assert m["pairs"]["n"] == 0 and m["error_cases"] == [] and m["verify_model"] == "mv" and m["prompt_version"] == "vT"
    assert m["thresholds"]["min_bad_claims"] == thresholds()["min_bad_claims"] and m["thresholds"]["fuzzy"] == 90   # the real ones


def test_valid_metrics_are_passed_through_with_note_stripped_and_missing_thresholds_filled(tmp_path):
    m = load_methodology(settings_with_metrics(tmp_path, VALID))
    assert m["pairs"]["precision"] == 0.9 and m["articles"]["confusion"] == {"tp": 7, "fp": 1, "fn": 3, "tn": 9}
    assert "_note" not in m and m["error_cases"][0]["kind"] == "fp" and m["tuned_on"] == "set A"
    assert m["thresholds"]["fuzzy"] == 88                                  # as measured, not the live value
    assert m["thresholds"]["young_domain_days"] == thresholds()["young_domain_days"]   # filled in


@pytest.mark.parametrize("bad", ["{broken", "[]", json.dumps({"pairs": {}}), json.dumps({**VALID, "articles": {"n_network": 1}})])
def test_broken_metrics_show_the_empty_state_not_a_crash(tmp_path, bad):
    assert load_methodology(settings_with_metrics(tmp_path, bad))["pairs"]["n"] == 0


def test_methodology_endpoint_matches_the_contract_example_keys(client, jobs, tmp_path):
    ex = json.loads((Path(__file__).resolve().parents[2] / "contracts" / "examples" / "methodology.json").read_text())
    jobs.settings = settings_with_metrics(tmp_path, VALID)
    got = client.get("/api/methodology").json()
    assert set(got) == set(ex) - {"_note"}
    jobs.settings = settings_with_metrics(tmp_path)                       # no file
    assert set(client.get("/api/methodology").json()) == set(ex) - {"_note"}


# ------------------------------------------------------------------------------------------------ build tool
def test_demo_build_import_and_build_write_result_files(demo_dir, tmp_path, monkeypatch, capsys):
    from app import demo_build
    monkeypatch.setenv("DEMO_DIR", str(demo_dir))
    get_settings.cache_clear()
    f = tmp_path / "cli.json"
    f.write_text(json.dumps({"job_id": "cli", "status": "done", "result": fake_result("weakly_sourced")}))
    assert demo_build.main(["--import", "blog", str(f)]) == 0
    assert demo.load_result("blog", get_settings())["verdict"]["label"] == "weakly_sourced"
    assert demo_build.main(["--import", "blog", str(tmp_path / "cli.json")]) == 0
    (tmp_path / "bad.json").write_text(json.dumps({"result": {"article": {}}}))
    assert demo_build.main(["--import", "x", str(tmp_path / "bad.json")]) == 2

    class Out:
        result = fake_result("well_sourced")
    monkeypatch.setattr(demo_build, "analyze", lambda url, settings=None, **kw: Out())
    assert demo_build.main(["--only", "wire"]) == 0
    assert "well_sourced" in capsys.readouterr().out
    assert demo.load_result("wire", get_settings())["verdict"]["label"] == "well_sourced"
    assert demo_build.main(["--only", "pravda"]) == 0 and "!! expected" in capsys.readouterr().out   # expected propaganda_pattern
