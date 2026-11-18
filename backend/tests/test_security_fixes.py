"""Regression tests for the security / robustness fixes of the code audit (docs/security-audit.md)."""
import httpx
import pytest

from app.config import Settings
from app.fetch import Fetcher
from app.fetch import fetcher as fetchmod
from app.fetch.netguard import BlockedAddress, check_target, is_public_hostname, make_request_hook
from app.fetch.urls import normalize_url
from app.pipeline import assess as assessmod
from app.pipeline import exa
from app.pipeline.verify import fuzzy_score

PUBLIC = {"example.org": ["93.184.215.14"], "news.test": ["93.184.215.14"], "rebind.example": ["10.0.0.5"],
          "mixed.example": ["93.184.215.14", "127.0.0.1"], "v6.example": ["::ffff:127.0.0.1"]}


def resolver(host, port):
    if host not in PUBLIC:
        raise OSError("no such host")
    return PUBLIC[host]


# ---------------------------------------------------------------- SSRF guard
@pytest.mark.parametrize("host", ["localhost", "LOCALHOST.", "db", "backend", "printer.local", "x.internal", "127.0.0.1",
                                  "10.1.2.3", "192.168.0.1", "169.254.169.254", "::1", "[::1]", "fd00::1", "0.0.0.0",
                                  "0x7f000001", "2130706433", "127.1", "::ffff:127.0.0.1", "", None])
def test_internal_hosts_are_not_public(host):
    assert not is_public_hostname(host)


@pytest.mark.parametrize("host", ["example.org", "www.bbc.co.uk", "93.184.215.14", "2606:2800:21f:cb07:6820:80da:af6b:8b2c"])
def test_public_hosts_pass(host):
    assert is_public_hostname(host)


@pytest.mark.parametrize("host", ["rebind.example", "mixed.example", "v6.example", "unknown.example"])
def test_names_resolving_inside_are_blocked(host):
    with pytest.raises(BlockedAddress):
        check_target(host, None, "https", resolver)


def test_ports_and_schemes_are_restricted():
    check_target("example.org", 8443, "https", resolver)
    for port in (22, 3306, 6379, 8000):
        with pytest.raises(BlockedAddress):
            check_target("example.org", port, "http", resolver)
    with pytest.raises(BlockedAddress):
        check_target("example.org", None, "file", resolver)


def test_redirect_to_an_internal_address_is_refused():
    """The hook runs before every hop: a public page that redirects to the metadata service must not be read."""
    seen = []

    def handler(request):
        seen.append(str(request.url))
        if request.url.host == "example.org":
            return httpx.Response(302, headers={"location": "http://169.254.169.254/latest/meta-data/"})
        return httpx.Response(200, text="SECRET " * 100, headers={"content-type": "text/html"})
    client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True,
                          event_hooks={"request": [make_request_hook(resolver)]})
    f = Fetcher(client=client, backoff_s=0)
    r = f.fetch_source("https://example.org/a", "https://news.test/x")
    assert r.status == "unreachable" and r.error == "BlockedAddress"
    assert seen == ["https://example.org/a"]            # the internal address was never requested


def test_default_fetcher_client_carries_the_guard():
    f = Fetcher()
    try:
        assert fetchmod.guard_request in f.client.event_hooks["request"]
    finally:
        f.client.close()


@pytest.mark.parametrize("url", ["http://localhost:8000/api/health", "http://169.254.169.254/latest/meta-data/",
                                 "http://db:3306/", "http://[::1]/", "http://10.0.0.1/admin"])
def test_api_refuses_internal_urls(client, url):
    r = client.post("/api/analyses", json={"url": url})
    assert r.status_code == 422 and r.json()["error"] == "invalid_url"


def test_api_refuses_overlong_urls(client):
    r = client.post("/api/analyses", json={"url": "https://example.org/" + "a" * 5000})
    assert r.status_code == 422


def test_cache_key_fits_the_column_and_does_not_collide():
    from app.api import cache_key

    a = normalize_url("https://example.org/" + "a" * 2000 + "1")
    b = normalize_url("https://example.org/" + "a" * 2000 + "2")
    for q in (None, "is this true?"):
        ka, kb = cache_key(a, q), cache_key(b, q)
        assert len(ka) <= 512 and len(kb) <= 512
        assert ka != kb                                  # before: both truncated to the same 490-char prefix
    assert cache_key("https://example.org/a", None) == "https://example.org/a"


# ---------------------------------------------------------------- URL normalisation
def test_ipv6_hosts_keep_their_brackets():
    assert normalize_url("http://[2001:db8::1]:8080/x/") == "http://[2001:db8::1]:8080/x"
    assert normalize_url("https://[2001:db8::1]/") == "https://[2001:db8::1]"


def test_bad_port_is_invalid_not_a_crash():
    from app.fetch.urls import InvalidURL

    with pytest.raises(InvalidURL):
        normalize_url("http://example.org:99999/")


# ---------------------------------------------------------------- download size cap
def test_oversized_pages_are_not_read(monkeypatch):
    monkeypatch.setattr(fetchmod, "MAX_DOWNLOAD_BYTES", 1000)
    body = "<html><body>" + "x" * 5000 + "</body></html>"
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, text=body, headers={"content-type": "text/html"})))
    f = Fetcher(client=client, backoff_s=0)
    r = f.fetch_source("https://example.org/huge", "https://news.test/x")
    assert r.status == "blocked" and "larger than" in r.error
    assert f.fetch_html("https://example.org/huge") is None


def test_binary_content_type_is_not_downloaded():
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, content=b"\x00" * 10, headers={"content-type": "application/zip"})))
    r = Fetcher(client=client, backoff_s=0).fetch_source("https://example.org/f.zip", "https://news.test/x")
    assert r.status == "blocked" and "content-type" in r.error


# ---------------------------------------------------------------- quote first: no superset quotes
def test_quote_that_wraps_a_short_source_with_invented_text_fails():
    src = "The ministry said 40 people were injured in the blast on Tuesday."
    invented = src + " The attack was ordered by the president personally and planned for months."
    assert fuzzy_score(invented, src) < 90
    assert fuzzy_score(src, src) == 100
    assert fuzzy_score("40 people were injured in the blast", src) == 100


# ---------------------------------------------------------------- robustness
def test_exa_retry_after_http_date_does_not_raise(monkeypatch):
    monkeypatch.setattr(exa.time, "sleep", lambda s: None)
    calls = []

    def handler(request):
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(429, headers={"retry-after": "Wed, 21 Oct 2026 07:28:00 GMT"})
        return httpx.Response(200, json={"results": [{"url": "https://ministry.example.gov/x", "title": "T"}]})
    hits = exa.find_candidates("The ministry", "claim", api_key="k", client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert [h.url for h in hits] == ["https://ministry.example.gov/x"]


def test_one_crashing_claim_does_not_end_the_analysis(monkeypatch):
    from app.pipeline.extract import ExtractedClaim

    def boom(claim, *a, **k):
        if claim.id == "c1":
            raise KeyError("unexpected answer shape")
        return assessmod.ClaimAssessment(claim, "unsourced", None, assessmod.NO_SOURCE)
    monkeypatch.setattr(assessmod, "assess_claim", boom)
    claims = [ExtractedClaim("c1", "One.", 0, 4, [], []), ExtractedClaim("c2", "Two.", 5, 9, [], [])]
    res = assessmod.assess_claims(claims, "https://news.test/a", fetcher=object(), llm=None,
                                  settings=Settings(_env_file=None, search_unsourced=False, exa_agent=False))
    assert [r.verdict for r in res] == ["not_checkable", "unsourced"]


def test_llm_usage_with_null_token_counts():
    from app.llm import LLMClient

    def handler(request):
        return httpx.Response(200, json={"model": "m", "usage": {"prompt_tokens": None, "completion_tokens": None},
                                         "choices": [{"message": {"content": "{\"ok\": true}"}}]})
    llm = LLMClient("https://r.test/v1", "k", retries=0, client=httpx.Client(transport=httpx.MockTransport(handler)))
    res = llm.chat_json("m", "s", "u")
    assert (res.prompt_tokens, res.completion_tokens) == (0, 0)
    assert llm.usage_since().calls == 1


def test_exa_agent_bad_answer_shape_is_neutral(monkeypatch):
    from app.pipeline import exa_agent

    monkeypatch.setattr(exa_agent.time, "sleep", lambda s: None)

    def handler(request):
        if request.method == "POST":
            return httpx.Response(200, json={"id": "r1"})
        return httpx.Response(200, json={"status": "completed", "output": {"structured": {"sources": ["not-a-dict"]},
                                                                          "grounding": ["also-not-a-dict"]}})
    res = exa_agent.find_sources({"query": "q"}, api_key="k", article_url="https://news.test/a", timeout_s=30,
                                 client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert res.status == "completed" and res.hits == []


def test_orphaned_active_row_does_not_block_new_jobs(client):
    """A job whose final status write was lost stays 'running' in the database: it must not hold a slot forever."""
    from datetime import timedelta

    from app import db
    from app.jobs import JobManager
    from app.models import Analysis, utcnow

    m = JobManager(settings=Settings(_env_file=None, max_concurrent_analyses=1))
    with db.session_scope() as s:
        s.add(Analysis(id="old1", url="https://example.org/a", normalized_url="https://example.org/a", status="running",
                       created_at=utcnow() - timedelta(hours=2)))
    assert m.running_count() == 0
    assert m.find_active("https://example.org/a") is None
    with db.session_scope() as s:
        s.add(Analysis(id="new1", url="https://example.org/b", normalized_url="https://example.org/b", status="running"))
    assert m.running_count() == 1
    assert m.find_active("https://example.org/b") == "new1"


def test_extraction_failure_does_not_leak_provider_error_text():
    import test_run as tr
    from app.llm import LLMClient
    from app.pipeline.run import AnalysisError, analyze

    def handler(request):
        return httpx.Response(401, text='{"error": "Incorrect API key provided: sk-live-abc***xyz"}')
    llm = LLMClient("https://r.test/v1", "k", retries=0, client=httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(AnalysisError) as err:
        analyze(tr.ARTICLE_URL, fetcher=tr.FakeFetcher(tr.art(), {}), llm=llm, settings=tr.SETTINGS, rdap=tr.FakeRdap({}))
    assert "sk-live" not in str(err.value) and "401" not in str(err.value)


def test_pdf_upload_is_refused_before_parsing_when_busy(client, monkeypatch):
    """No free analysis slot: the 20 MB PDF must not be parsed at all (anonymous CPU/memory DoS otherwise)."""
    from app import api

    def must_not_parse(*a, **k):
        raise AssertionError("the PDF was parsed although the server is busy")
    monkeypatch.setattr(api, "pdf_to_article", must_not_parse)
    monkeypatch.setattr(client.app.state.jobs, "running_count", lambda: 99)
    r = client.post("/api/analyses/pdf", content=b"%PDF-1.4\n" + b"0" * 100)
    assert r.status_code == 429 and r.json()["error"] == "busy"
