"""Fact-check agent (pipeline/factcheck.py + its use in assess.py). No network: the Exa SDK client is a fake, the LLM and the
fetcher are the same stubs test_assess.py uses."""
import json
from types import SimpleNamespace

import httpx
import pytest

from app import cache as cachemod
from app.config import Settings
from app.llm import LLMClient
from app.pipeline import assess as assessmod, factcheck
from app.pipeline.assess import ClaimAssessment, SourceAssessment
from app.pipeline.evidence import PLAN_PROMPT
from app.pipeline.factcheck import FactHit, FactResult, build_query, run_factcheck
from app.pipeline.verify import FACTCHECK_ADDENDUM, SYSTEM_PROMPT, VerifyResult, verify_claim
from test_assess import QUOTE, TEXT, claim, exa_client, page, run

ART = "https://news.test/a"
FOUND = "https://registry.test/milleis"
OTHER = "https://other.test/story"
SETTINGS = Settings(_env_file=None, model_verify="vm", exa_api_key="exa", fuzzy_threshold=90, prompt_version="vT",
                    search_unsourced=True, exa_agent=False, factcheck_agent=True)


# ------------------------------------------------------------------------------------------------ the SDK client
class FakeRuns:
    def __init__(self, structured=None, status="completed", create_errors=(), poll_error=None, cost=0.025):
        self.structured, self.status, self.create_errors, self.poll_error, self.cost = structured, status, list(create_errors), poll_error, cost
        self.created, self.cancelled = [], []

    def create(self, **kw):
        self.created.append(kw)
        if self.create_errors:
            raise self.create_errors.pop(0)
        return SimpleNamespace(id="run1", status="queued")

    def poll_until_finished(self, run_id, **kw):
        if self.poll_error:
            raise self.poll_error
        return SimpleNamespace(id=run_id, status=self.status, cost_dollars=SimpleNamespace(total=self.cost),
                               output=SimpleNamespace(structured=self.structured, text=None, grounding=None))

    def cancel(self, run_id):
        self.cancelled.append(run_id)


def fake_exa(**kw):
    runs = FakeRuns(**kw)
    return SimpleNamespace(agent=SimpleNamespace(runs=runs)), runs


def test_query_asks_for_confirming_or_refuting_pages_and_keeps_the_attribution():
    q = build_query("Bank X was acquired by Y.", "para", "news.test", None, who="investigative journalists", venue="OCCRP")
    assert "CONFIRMS it or REFUTES it" in q and "news.test" in q and "investigative journalists" in q and "OCCRP" in q
    assert "attributed" not in build_query("C.", "p", "news.test", None).lower().replace("attribution", "")


def test_run_factcheck_parses_filters_and_caches(monkeypatch):
    structured = {"found": True, "sources": [
        {"url": FOUND, "stance": "refutes", "kind": "primary", "quote": "q"},
        {"url": "https://news.test/other", "stance": "confirms", "kind": "independent"},      # the article's own site
        {"url": "ftp://x.test/y", "stance": "confirms"},
        {"url": OTHER, "stance": "bogus", "kind": "bogus", "quote": None},
        {"url": FOUND, "stance": "confirms"}]}                                                  # duplicate
    exa, runs = fake_exa(structured=structured)
    cache = cachemod.MemoryCache()
    r = run_factcheck("query", api_key="k", article_url=ART, effort="low", exa=exa, cache=cache)
    assert [(h.url, h.stance, h.kind) for h in r.hits] == [(FOUND, "refutes", "primary"), (OTHER, "confirms", "independent")]
    assert r.cost == 0.025 and r.status == "completed" and not r.cached and r.found is True
    created = runs.created[0]
    assert created["effort"] == "low" and created["query"] == "query" and created["output_schema"] is factcheck.SCHEMA
    again, runs2 = fake_exa()
    r2 = run_factcheck("query", api_key="k", article_url=ART, effort="low", exa=again, cache=cache)
    assert r2.cached and [h.url for h in r2.hits] == [h.url for h in r.hits] and runs2.created == []


def test_run_factcheck_failures_are_neutral(monkeypatch):
    assert run_factcheck("q", api_key="", article_url=ART, cache=cachemod.MemoryCache()).status == "disabled"
    exa, _ = fake_exa(status="failed")
    assert run_factcheck("q", api_key="k", article_url=ART, exa=exa, cache=cachemod.MemoryCache()).hits == []
    exa, _ = fake_exa(create_errors=[ValueError("Request failed with status code 500: boom")])
    r = run_factcheck("q", api_key="k", article_url=ART, exa=exa, cache=cachemod.MemoryCache())
    assert r.hits == [] and r.status == "unavailable"


def test_run_factcheck_timeout_cancels_the_run_and_is_not_cached():
    exa, runs = fake_exa(poll_error=TimeoutError("slow"))
    cache = cachemod.MemoryCache()
    r = run_factcheck("q", api_key="k", article_url=ART, exa=exa, cache=cache)
    assert r.status == "timeout" and runs.cancelled == ["run1"]
    assert cache.get("factcheck", cachemod.make_key("factcheck", {"query": "q", "effort": "low"})) is None


def test_run_factcheck_retries_a_429(monkeypatch):
    monkeypatch.setattr(factcheck.time, "sleep", lambda s: None)
    exa, runs = fake_exa(structured={"found": False, "sources": []},
                         create_errors=[ValueError("Request failed with status code 429: slow down")])
    r = run_factcheck("q", api_key="k", article_url=ART, exa=exa, cache=cachemod.MemoryCache())
    assert r.status == "completed" and len(runs.created) == 2


# ------------------------------------------------------------------------------------------------ verify mode
def test_factcheck_verify_mode_drops_the_date_rule_and_adds_the_addendum():
    seen = []

    def handler(request):
        seen.append(json.loads(request.content)["messages"])
        c = json.dumps({"quote_original": "", "quote_translation": None, "language": "en", "verdict": "not_mentioned", "explanation": "x."})
        return httpx.Response(200, json={"model": "vm", "choices": [{"message": {"content": c}}]})
    llm = LLMClient("https://r.test/v1", "k", retries=0, client=httpx.Client(transport=httpx.MockTransport(handler)))
    verify_claim("C.", TEXT, llm=llm, settings=SETTINGS, factcheck=True)
    verify_claim("C.", TEXT, llm=llm, settings=SETTINGS)
    assert seen[0][0]["content"] == SYSTEM_PROMPT + FACTCHECK_ADDENDUM and "ARTICLE DATE:" not in seen[0][1]["content"]
    assert seen[1][0]["content"] == SYSTEM_PROMPT        # every other path keeps the frozen prompt


# ------------------------------------------------------------------------------------------------ assess: unsourced
def _llm(by_marker=None, verdict="contradicted", quote=QUOTE):
    """Planner stub + verify stub. `by_marker`: {text marker in SOURCE: verdict}."""
    def handler(request):
        msgs = json.loads(request.content)["messages"]
        if msgs[0]["content"] == PLAN_PROMPT:
            c = {"speaker": None, "venue": None, "occasion": None, "queries": ["q"], "phrases": []}
        else:
            v = next((val for m, val in (by_marker or {}).items() if m in msgs[-1]["content"]), verdict)
            c = {"quote_original": quote if v != "not_mentioned" else "", "quote_translation": None, "language": "en",
                 "verdict": v, "explanation": f"The page says {v}.", "source_type": "original"}
        return httpx.Response(200, json={"model": "vm", "choices": [{"message": {"content": json.dumps(c)}}]})
    return LLMClient("https://r.test/v1", "k", retries=0, client=httpx.Client(transport=httpx.MockTransport(handler)))


@pytest.fixture
def agent(monkeypatch):
    """The fact-check agent returns whatever the test puts into `box`."""
    box = SimpleNamespace(hits=[], calls=0)

    def fake(query, **kw):
        box.calls += 1
        return FactResult(list(box.hits), bool(box.hits), 0.025, 1.0, "completed")
    monkeypatch.setattr(assessmod, "run_factcheck", fake)
    return box


def test_unsourced_claim_refuted_by_a_verified_page_is_contradicted(agent):
    agent.hits = [FactHit(FOUND, "refutes", "primary", "ignored")]
    (a,), _ = run([claim(1)], {FOUND: page(FOUND)}, llm=_llm(), exa=exa_client(None), settings=SETTINGS)
    assert a.verdict == "contradicted" and a.best.url == FOUND and a.best.factchecked
    assert a.evidence is not None and a.evidence.quote_original == QUOTE and a.evidence.fuzzy_score >= 90
    assert a.explanation.startswith("The article gives no source for this claim.") and "contradicts the claim" in a.explanation
    assert "registry.test" in a.explanation


def test_a_refutation_without_a_verifiable_quote_stays_unsourced(agent):
    agent.hits = [FactHit(FOUND, "refutes", "primary", "a quote the agent made up")]
    (a,), _ = run([claim(1)], {FOUND: page(FOUND)}, llm=_llm(quote="A sentence that is nowhere in the source text at all, honestly."),
                  exa=exa_client(None), settings=SETTINGS)
    assert a.verdict == "unsourced" and a.evidence is None      # never trust an unchecked quote, never count it against the article


def test_agent_stance_label_is_never_trusted(agent):
    agent.hits = [FactHit(FOUND, "refutes", "primary")]        # the agent says "refutes", the verify model says supported
    (a,), _ = run([claim(1)], {FOUND: page(FOUND)}, llm=_llm(verdict="supported"), exa=exa_client(None), settings=SETTINGS)
    assert a.verdict == "supported" and a.best.factchecked


def test_page_that_neither_confirms_nor_refutes_is_ignored(agent):
    agent.hits = [FactHit(FOUND, "refutes")]
    (a,), _ = run([claim(1)], {FOUND: page(FOUND)}, llm=_llm(verdict="not_mentioned"), exa=exa_client(None), settings=SETTINGS)
    assert a.verdict == "unsourced" and a.sources == []


def test_confirming_and_refuting_pages_disagree_neutral(agent):
    other_text = "A different outlet reports something else entirely about the same matter, in other words."
    agent.hits = [FactHit(FOUND, "confirms"), FactHit(OTHER, "refutes")]
    (a,), _ = run([claim(1)], {FOUND: page(FOUND), OTHER: page(OTHER, text=QUOTE + " " + other_text)},
                  llm=_llm({other_text: "contradicted"}, verdict="supported"), exa=exa_client(None), settings=SETTINGS)
    assert a.verdict == "not_checkable" and "disagree" in a.explanation and a.best is None and a.evidence is None
    assert {s.url for s in a.sources} == {FOUND, OTHER}


def test_factcheck_replaces_the_support_only_agent_for_unsourced_claims(agent, monkeypatch):
    called = []
    monkeypatch.setattr(assessmod, "find_sources", lambda *a, **k: called.append(1))
    s = Settings(_env_file=None, model_verify="vm", exa_api_key="exa", prompt_version="vT", search_unsourced=True, exa_agent=True,
                 factcheck_agent=True)
    run([claim(1)], {}, llm=_llm(), exa=exa_client(None), settings=s)
    assert called == [] and agent.calls == 1


def test_factcheck_off_keeps_the_old_behaviour(agent):
    agent.hits = [FactHit(FOUND, "refutes")]
    off = SETTINGS.model_copy(update={"factcheck_agent": False})
    (a,), _ = run([claim(1)], {FOUND: page(FOUND)}, llm=_llm(), exa=exa_client(None), settings=off)
    assert a.verdict == "unsourced" and agent.calls == 0


# ------------------------------------------------------------------------------------------------ assess: attributed
def test_attributed_claim_whose_original_is_missing_is_refuted_by_the_facts(agent):
    agent.hits = [FactHit(FOUND, "refutes")]
    (a,), _ = run([claim(1, attrs=["investigative journalists"])], {FOUND: page(FOUND)}, llm=_llm(), exa=exa_client(None), settings=SETTINGS)
    assert a.verdict == "contradicted" and a.best.url == FOUND and a.evidence.quote_original == QUOTE
    assert a.explanation.startswith("The original statement could not be found") and "contradicts the claim" in a.explanation


def test_attributed_claim_confirmed_by_facts_stays_neutral_like_any_corroboration(agent):
    agent.hits = [FactHit(FOUND, "confirms")]
    (a,), _ = run([claim(1, attrs=["X"])], {FOUND: page(FOUND)}, llm=_llm(verdict="supported"), exa=exa_client(None), settings=SETTINGS)
    assert a.verdict == "not_checkable" and a.sources[-1].corroborated and a.evidence is None
    on = SETTINGS.model_copy(update={"corroboration_counts": True})
    (b,), _ = run([claim(1, attrs=["X"])], {FOUND: page(FOUND)}, llm=_llm(verdict="supported"), exa=exa_client(None), settings=on)
    assert b.verdict == "supported"


def test_attributed_claim_with_a_found_original_never_starts_the_agent(agent):
    url = "https://www.orig.gov.test/interview"
    (a,), _ = run([claim(1, attrs=["the Ministry"])], {url: page(url)}, llm=_llm(verdict="supported"), exa=exa_client(url), settings=SETTINGS)
    assert a.verdict == "supported" and agent.calls == 0


def test_attributed_claim_agent_failure_leaves_the_neutral_answer(agent, monkeypatch):
    monkeypatch.setattr(assessmod, "run_factcheck", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("sdk changed")))
    (a,), _ = run([claim(1, attrs=["Zelenskyy"])], {}, llm=_llm(), exa=exa_client(None), settings=SETTINGS)
    assert a.verdict == "not_checkable" and "Zelenskyy" in a.explanation


def test_linked_claims_never_use_the_agent(agent):
    (a,), _ = run([claim(1, ["https://src.test/x"])], {"https://src.test/x": page("https://src.test/x", "unreachable")}, llm=_llm(), settings=SETTINGS)
    assert a.verdict == "not_checkable" and agent.calls == 0


# ------------------------------------------------------------------------------------------------ result shape (run.py)
def test_attributed_refutation_is_listed_as_the_evidence_page_of_the_claim():
    from app.pipeline.run import _Registry, _claim_dict

    c = claim(1, attrs=["investigative journalists"])
    v = VerifyResult("contradicted", "contradicted", QUOTE, None, "en", 100.0, "Owner differs.", "m", "vT")
    sa = SourceAssessment(FOUND, "named", None, page(FOUND), v, "contradicted", factchecked=True)
    a = ClaimAssessment(c, "contradicted", None, "x", [sa], sa)
    reg, events = _Registry(), []
    d = _claim_dict(a, reg, lambda e, data: events.append(e))
    assert d["verdict"] == "contradicted" and d["source_ids"] == ["s1"] and d["evidence"]["source_id"] == "s1"
    assert reg.rows[0]["url"] == FOUND and reg.rows[0]["kind"] == "named"
