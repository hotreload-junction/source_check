"""PLAN Q2: domain reputation badges. Offline: snapshots from the repo, Wayback through httpx.MockTransport."""
import httpx
import pytest

from app.signals.reputation import ArchiveResult, WaybackClient, add_reputation, snapshots
from app.signals.reputation_build import parse_rsp

WIKITEXT = """{| class="wikitable sortable perennial-sources"
<onlyinclude>
|- class="s-d" id="RT"
| [[RT (TV network)|RT]] {{WP:RSPSHORTCUT|WP:RT.COM}}
| {{WP:RSPSTATUS|d}}
| {{rsnl|295|RfC: RT|2020|rfc=y}}
| {{WP:RSPLAST|2022}}
| There is consensus that RT is an unreliable source.
| {{WP:RSPUSES|rt.com|www.russiatoday.com|aa.com.tr/en}}
|- class="s-gu" id="Natural News"
| [[Natural News]]
| {{WP:RSPSTATUS|gu|b=y}}
| x
| {{WP:RSPLAST|2019|stale=n}}
| Blacklisted.
| {{WP:RSPUSES|naturalnews.com}}
|- class="s-m" id="Polygon"
| Polygon
| {{WP:RSPSTATUS|m}} {{WP:RSPSTATUS|gr}}
| x
| no last
| x
| {{WP:RSPUSES|polygon.com}}
|- class="s-gr" id="No uses"
| {{WP:RSPSTATUS|gr}}
| x
|}"""


def test_parse_rsp_rows():
    rows = {r["source"]: r for r in parse_rsp(WIKITEXT)}
    assert set(rows) == {"RT", "Natural News", "Polygon"}          # a row without domains is dropped
    assert rows["RT"] == {"source": "RT", "statuses": ["deprecated"], "blacklisted": False, "last_discussed": 2022,
                          "domains": ["rt.com", "russiatoday.com"]}  # www. stripped, path entries (aa.com.tr/en) skipped
    assert rows["Natural News"]["blacklisted"] is True and rows["Natural News"]["statuses"] == ["generally_unreliable"]
    assert rows["Polygon"]["statuses"] == ["no_consensus", "generally_reliable"] and rows["Polygon"]["last_discussed"] is None


def test_committed_snapshots_load():
    s = snapshots()
    assert s.rsp_date and s.tranco_id and s.tranco_date
    assert len(s.tranco) > 90_000 and len(s.rsp) > 500
    assert [r["statuses"] for r in s.rsp["reuters.com"]] == [["generally_reliable"]]
    assert "deprecated" in s.rsp["rt.com"][0]["statuses"]
    assert s.tranco["google.com"] == 1


def _domains(*names, created=None):
    return [{"domain": n, "created_at": created, "age_days": None} for n in names]


def test_badges_and_notes_without_archive():
    doms = _domains("reuters.com", "unknown-site-xyz.example")
    add_reputation(doms, None)
    reuters, unknown = doms
    assert reuters["wikipedia_rsp"][0]["source"] == "Reuters" and reuters["wikipedia_rsp"][0]["snapshot"]
    assert isinstance(reuters["tranco_rank"], int) and reuters["first_archived_at"] is None
    assert any("generally reliable" in n for n in reuters["reputation_notes"])
    assert any("popularity, not reliability" in n for n in reuters["reputation_notes"])
    assert unknown["wikipedia_rsp"] == [] and unknown["tranco_rank"] is None
    assert unknown["reputation_notes"] == [n for n in unknown["reputation_notes"] if n.startswith("Not among")]
    assert not any("Wayback" in n for n in reuters["reputation_notes"])     # lookup off: no archive note at all


def test_reregistered_domain_note():
    doms = _domains("dropped.example", created="2026-09-01T00:00:00Z")
    add_reputation(doms, lambda d: ArchiveResult(d, "2011-03-05"))
    notes = doms[0]["reputation_notes"]
    assert doms[0]["first_archived_at"] == "2011-03-05"
    assert any("predate the current registration (2026-09-01)" in n and "registered again" in n for n in notes)


def test_no_reregistration_note_for_normal_history():
    doms = _domains("normal.example", created="2010-01-01T00:00:00Z")
    add_reputation(doms, lambda d: ArchiveResult(d, "2010-06-01"))
    assert not any("registered again" in n for n in doms[0]["reputation_notes"])


def test_failed_or_crashing_lookup_is_neutral():
    def boom(d):
        raise RuntimeError("down")
    for archive in (boom, lambda d: ArchiveResult(d, None, "ReadTimeout")):
        doms = _domains("x.example", created="2026-09-01T00:00:00Z")
        add_reputation(doms, archive)
        assert doms[0]["first_archived_at"] is None
        assert not any("Wayback" in n or "registered again" in n for n in doms[0]["reputation_notes"])


def test_never_archived_is_a_real_answer():
    doms = _domains("fresh.example")
    add_reputation(doms, lambda d: ArchiveResult(d, None))
    assert "Never captured by the Wayback Machine" in doms[0]["reputation_notes"]


def test_skip_domains_are_not_looked_up():
    called = []
    doms = _domains("x.com")
    add_reputation(doms, lambda d: called.append(d) or ArchiveResult(d, "2006-01-01"), skip={"x.com"})
    assert called == [] and doms[0]["first_archived_at"] is None


@pytest.fixture(autouse=True)
def _wayback_up():
    WaybackClient._down_until = 0.0
    yield
    WaybackClient._down_until = 0.0


def _client(handler, clock=None):
    return WaybackClient(httpx.Client(transport=httpx.MockTransport(handler)), **({"clock": clock} if clock else {}))


def test_wayback_client_parses_first_capture():
    seen = []

    def handler(req):
        seen.append(req.url.params["url"])
        return httpx.Response(200, json=[["timestamp"], ["19961018151258"]])
    c = _client(handler)
    assert c.lookup("www.reuters.com") == ArchiveResult("reuters.com", "1996-10-18")
    c.lookup("reuters.com")
    assert seen == ["reuters.com"]                    # registrable domain, cached per process


def test_wayback_client_empty_and_errors():
    assert _client(lambda r: httpx.Response(200, json=[])).lookup("a.example") == ArchiveResult("a.example", None)
    assert _client(lambda r: httpx.Response(503)).lookup("a.example").error == "http 503"
    WaybackClient._down_until = 0.0                    # the 503 started a back-off; next case on a fresh service

    def down(req):
        raise httpx.ConnectTimeout("t")
    res = _client(down).lookup("a.example")
    assert res.first_archived_at is None and res.error == "ConnectTimeout"


class _Archive2001:
    def lookup(self, domain):
        return ArchiveResult(domain, "2001-01-01")


def test_pipeline_sets_badges_and_they_never_change_the_verdict():
    """End to end through analyze(): every Domain carries the Q2 fields; reputation is context, not a signal."""
    from app.pipeline.run import analyze
    import tests.test_run as tr

    def go(archive):
        return analyze(tr.ARTICLE_URL, fetcher=tr.FakeFetcher(tr.art(), tr.pages.__wrapped__()), llm=tr.router(tr.EXTRACT, tr.verifier),
                       settings=tr.SETTINGS, rdap=tr.FakeRdap({"news-front.net": 55}), archive=archive).result
    plain = go(None)
    # every domain "archived in 2001": with a 55-day-old registration the note says it was registered again
    rich = go(_Archive2001())
    for d in rich["domains"]:
        assert {"wikipedia_rsp", "tranco_rank", "first_archived_at", "reputation_notes"} <= set(d)
        assert d["first_archived_at"] == "2001-01-01"
    young = next(d for d in rich["domains"] if d["domain"] == "news-front.net")
    assert any("registered again" in n for n in young["reputation_notes"])
    assert rich["verdict"] == plain["verdict"] and rich["signals"] == plain["signals"]
    assert all(d["first_archived_at"] is None for d in plain["domains"])


def test_connection_refused_backs_off_instead_of_hammering():
    calls, now = [], [1000.0]

    def handler(req):
        calls.append(req.url.params["url"])
        if len(calls) == 1:
            raise httpx.ConnectError("refused")
        return httpx.Response(200, json=[["timestamp"], ["20100101000000"]])
    c = _client(handler, clock=lambda: now[0])
    assert c.lookup("a.example").error == "ConnectError"
    assert c.lookup("b.example").error == "backing off" and calls == ["a.example"]   # no request while backing off
    now[0] += 301
    assert c.lookup("c.example").first_archived_at == "2010-01-01"


def test_429_backs_off_too():
    c = _client(lambda r: httpx.Response(429), clock=lambda: 5.0)
    assert c.lookup("a.example").error == "http 429"
    assert c.lookup("b.example").error == "backing off"
