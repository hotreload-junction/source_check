"""B8: jobs API with a fake `analyze` (no network). Persistence is checked with a REAL pipeline output."""
import json
import threading
import time

import pytest
from sqlalchemy import func, select

import test_run as tr
from app import db
from app.config import Settings
from app.jobs import JobManager
from app.models import Analysis, ClaimRecord, ClaimVerdictRecord, SourceRecord
from app.pipeline.run import AnalysisError, analyze

URL = "https://www.example.com/story?utm_source=x"


def wait_until(fn, timeout=5.0, step=0.02):
    end = time.time() + timeout
    while time.time() < end:
        v = fn()
        if v:
            return v
        time.sleep(step)
    raise AssertionError("condition not met in time")


def real_output():
    pages = {"https://news-front.net/report-123": tr.src("https://news-front.net/report-123", tr.SRC_BAD, '<script>ga("G-SAME0001")</script>'),
             "https://ministry.gov.uk/report": tr.src("https://ministry.gov.uk/report", tr.SRC_OK, "<html></html>")}
    return analyze(tr.ARTICLE_URL, fetcher=tr.FakeFetcher(tr.art(), pages), llm=tr.router(tr.EXTRACT, tr.verifier),
                   settings=tr.SETTINGS, rdap=tr.FakeRdap({"news-front.net": 55}))


class Fake:
    """analyze() stand-in: emits contract events, optionally blocks, optionally fails."""

    def __init__(self, out=None, fail=None, gate=None):
        self.out, self.fail, self.gate, self.calls = out, fail, gate, []

    def __call__(self, url, settings=None, emit=None, **kw):
        self.calls.append(url)
        emit("progress", {"step": "scrape_article", "message": "Fetching article", "pct": 5})
        emit("node", {"id": "a", "type": "article", "label": "T", "verdict": None, "cluster_id": None, "depth": 0})
        if self.gate:
            assert self.gate.wait(10)
        if self.fail:
            raise self.fail
        emit("progress", {"step": "verify_claims", "message": "Verified 1 of 1 claims", "pct": 60})
        emit("edge", {"id": "e1", "source": "a", "target": "c1", "type": "makes_claim"})
        emit("claim", {"id": "c1"})
        return self.out


@pytest.fixture
def jobs(client):
    m = client.app.state.jobs
    m.settings = Settings(_env_file=None, max_concurrent_analyses=2)
    return m


def post(client, url=URL, **kw):
    return client.post("/api/analyses", json={"url": url, **kw})


def done(client, job_id):
    return wait_until(lambda: (lambda a: a if a["status"] in ("done", "failed") else None)(client.get(f"/api/analyses/{job_id}").json()))


def test_invalid_url_is_422_and_unknown_job_is_404(client):
    for bad in ("not a url", "ftp://x.com/a", ""):
        r = post(client, bad)
        assert r.status_code == 422 and r.json()["error"] == "invalid_url"
    assert client.get("/api/analyses/nope").status_code == 404
    assert client.get("/api/analyses/nope/events").status_code == 404


def test_start_poll_result_cache_and_force(client, jobs):
    jobs.analyze_fn = fake = Fake(real_output())
    r = post(client)
    assert r.status_code == 202 and r.json()["status"] == "queued" and r.json()["cached"] is False
    job_id = r.json()["job_id"]
    a = done(client, job_id)
    assert set(a) == {"job_id", "status", "progress", "error", "created_at", "finished_at", "result"}
    assert a["status"] == "done" and a["progress"]["pct"] == 100 and a["error"] is None and a["finished_at"].endswith("Z")
    assert a["result"]["verdict"]["label"] == "propaganda_pattern"
    # same article, other spelling of the URL -> cache hit, no second pipeline run
    r2 = post(client, "https://example.com/story")
    assert r2.status_code == 200 and r2.json() == {"job_id": job_id, "status": "done", "cached": True}
    assert len(fake.calls) == 1
    r3 = post(client, "https://example.com/story", force=True)
    assert r3.status_code == 202 and r3.json()["job_id"] != job_id
    done(client, r3.json()["job_id"])
    assert len(fake.calls) == 2


def test_running_job_exposes_progress_and_has_no_result_yet(client, jobs):
    gate = threading.Event()
    jobs.analyze_fn = Fake(real_output(), gate=gate)
    job_id = post(client).json()["job_id"]
    a = wait_until(lambda: (lambda x: x if x["progress"]["step"] == "scrape_article" else None)(client.get(f"/api/analyses/{job_id}").json()))
    assert a["status"] == "running" and a["result"] is None and a["finished_at"] is None and a["progress"]["pct"] == 5
    gate.set()
    assert done(client, job_id)["status"] == "done"


def test_concurrency_limit_429_then_free_slot(client, jobs):
    gate = threading.Event()
    jobs.analyze_fn = Fake(real_output(), gate=gate)
    ids = [post(client, f"https://a{i}.com/x").json()["job_id"] for i in range(2)]
    r = post(client, "https://a3.com/x")
    assert r.status_code == 429 and r.json()["error"] == "busy" and "2 analyses running" in r.json()["detail"]
    gate.set()
    for i in ids:
        done(client, i)
    assert post(client, "https://a3.com/x").status_code == 202


def test_same_article_in_flight_is_joined_not_duplicated(client, jobs):
    gate = threading.Event()
    jobs.analyze_fn = fake = Fake(real_output(), gate=gate)
    first = post(client).json()["job_id"]
    second = post(client, "https://example.com/story")
    assert second.status_code == 202 and second.json()["job_id"] == first
    gate.set()
    done(client, first)
    assert len(fake.calls) == 1


def sse_events(client, job_id):
    out = []
    with client.stream("GET", f"/api/analyses/{job_id}/events") as r:
        assert r.headers["content-type"].startswith("text/event-stream")
        assert r.headers["x-accel-buffering"] == "no" and r.headers["cache-control"] == "no-cache"
        event = None
        for line in r.iter_lines():
            if line.startswith("event: "):
                event = line[7:]
            elif line.startswith("data: "):
                out.append((event, json.loads(line[6:])))
    return out


def test_sse_streams_contract_events_in_order_and_ends_with_done(client, jobs):
    gate = threading.Event()
    jobs.analyze_fn = Fake(real_output(), gate=gate)
    job_id = post(client).json()["job_id"]
    threading.Timer(0.3, gate.set).start()          # subscriber is already waiting when events arrive
    ev = sse_events(client, job_id)
    kinds = [e for e, _ in ev]
    assert kinds == ["progress", "node", "progress", "edge", "claim", "done"]
    assert ev[-1][1]["status"] == "done" and ev[-1][1]["result"]["verdict"]["label"] == "propaganda_pattern"
    assert sse_events(client, job_id) == ev          # a late subscriber replays the whole stream


def test_sse_after_restart_replays_outcome_from_database(client, jobs):
    jobs.analyze_fn = Fake(real_output())
    job_id = post(client).json()["job_id"]
    done(client, job_id)
    jobs._events.clear()                              # as after a process restart
    ev = sse_events(client, job_id)
    assert [e for e, _ in ev] == ["progress", "done"] and ev[-1][1]["job_id"] == job_id


def test_analysis_error_is_stored_and_streamed_with_its_message(client, jobs):
    jobs.analyze_fn = Fake(fail=AnalysisError("The article is behind a paywall. Nothing to analyse."))
    job_id = post(client).json()["job_id"]
    a = done(client, job_id)
    assert a["status"] == "failed" and "paywall" in a["error"] and a["result"] is None and a["finished_at"]
    assert [e for e, _ in sse_events(client, job_id)][-1] == "error"
    assert post(client).status_code == 202            # a failed job is never served from the cache


def test_crash_hides_internals_from_the_user(client, jobs):
    jobs.analyze_fn = Fake(fail=RuntimeError("secret-key-abc in traceback"))
    a = done(client, post(client).json()["job_id"])
    assert a["status"] == "failed" and "secret" not in a["error"] and "try again" in a["error"]


def test_audit_trail_is_stored_with_the_result(client, jobs):
    out = real_output()
    jobs.analyze_fn = Fake(out)
    job_id = post(client).json()["job_id"]
    done(client, job_id)
    with db.session_scope() as s:
        claims = s.scalars(select(ClaimRecord).where(ClaimRecord.analysis_id == job_id).order_by(ClaimRecord.claim_id)).all()
        assert [c.claim_id for c in claims] == ["c1", "c2", "c3"]
        rows = {c.claim_id: c.verdicts for c in claims}
        v = rows["c2"][0]
        assert (v.verdict, v.model, v.prompt_version, v.llm_verdict) == ("supported", "m", "vT", "supported")   # model = what the router reported
        assert v.fuzzy_score >= 90 and v.quote_original.startswith("the new programme") and v.source_id == "s2" and v.source_url == "https://ministry.gov.uk/report"
        assert rows["c3"][0].verdict == "unsourced" and rows["c3"][0].source_id is None      # unsourced still leaves a trace
        srcs = {x.source_id: x for x in s.scalars(select(SourceRecord).where(SourceRecord.analysis_id == job_id))}
        assert set(srcs) == {"s1", "s2"} and srcs["s2"].text == tr.SRC_OK and srcs["s1"].fetch_status == "ok"


def test_recover_stale_marks_unfinished_jobs_failed(client, jobs):
    with db.session_scope() as s:
        s.add(Analysis(id="stale1", url="https://x.test/a", normalized_url="x.test/a", status="running"))
        s.add(Analysis(id="fine1", url="https://x.test/b", normalized_url="x.test/b", status="done"))
    assert jobs.recover_stale() == 1
    assert client.get("/api/analyses/stale1").json()["status"] == "failed"
    assert "restarted" in client.get("/api/analyses/stale1").json()["error"]
    assert client.get("/api/analyses/fine1").json()["status"] == "done"
    assert jobs.running_count() == 0
