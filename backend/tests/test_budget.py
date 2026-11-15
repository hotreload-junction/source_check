"""Cost meter (llm.Usage) and the time / cost budget of one analysis (pipeline/budget.py). No network."""
import httpx
import pytest

from app import llm as llm_mod
from app.config import Settings
from app.llm import LLMClient, LLMResult, PRICES_PER_MTOK
from app.pipeline.budget import RunBudget
from app.pipeline.run import analyze
from tests.test_depth2 import CLEAN, QUOTE, S2, pages_with
from tests.test_run import ARTICLE_URL, EXTRACT, FakeFetcher, FakeRdap, art, router, verifier


def client():
    return LLMClient("https://r.test/v1", "k", retries=0, client=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500))))


# ------------------------------------------------------------------ cost meter
def test_usage_counts_priced_calls_in_usd_and_replays_are_free():
    c = client()
    c._record("claude-sonnet-5-5", LLMResult({}, "claude-sonnet-5-5", prompt_tokens=1_000_000, completion_tokens=100_000))
    c._record("claude-haiku-5-5", LLMResult({}, "claude-haiku-5-5", prompt_tokens=2_000_000, completion_tokens=0))
    c._record("claude-sonnet-5-5", LLMResult({}, "claude-sonnet-5-5", prompt_tokens=5_000_000, completion_tokens=5_000_000, cached=True))
    u = c.usage_since()
    assert (u.calls, u.cached) == (2, 1) and u.prompt_tokens == 3_000_000
    assert u.usd == pytest.approx(2.0 + 1.0 + 0.2) and u.unpriced == ()          # sonnet 2+1, haiku 0.2; the cached call costs nothing


def test_usage_since_a_snapshot_and_unknown_models_are_reported_not_guessed():
    c = client()
    c._record("mystery-model", LLMResult({}, "mystery-model", prompt_tokens=10, completion_tokens=10))
    snap = c.usage_snapshot()
    c._record("claude-haiku-5-5", LLMResult({}, "claude-haiku-5-5", prompt_tokens=1_000_000))
    u = c.usage_since(snap)
    assert u.calls == 1 and u.usd == pytest.approx(0.10) and u.unpriced == ()
    whole = c.usage_since()
    assert whole.calls == 2 and whole.unpriced == ("mystery-model",) and whole.usd == pytest.approx(0.10)
    assert {"claude-haiku-5-5", "claude-sonnet-5-5"} <= set(PRICES_PER_MTOK)


# ------------------------------------------------------------------ the budget object
def test_budget_blocks_extras_when_time_or_cost_is_used_up_and_says_what_it_skipped():
    s = Settings(_env_file=None, time_budget_s=100.0, cost_budget_usd=0.45, chain_start_limit_s=70.0)
    c = client()
    b = RunBudget(s, c, t0=llm_mod.time.monotonic())
    assert b.over() is None and b.allow("source chain") and b.allow_chain_search() and b.skipped == []
    c._record("claude-sonnet-5-5", LLMResult({}, "claude-sonnet-5-5", prompt_tokens=300_000))          # 0.60 USD
    assert b.over() == "cost" and not b.allow("source chain") and b.skipped == ["source chain (cost budget)"]
    late = RunBudget(s, client(), t0=llm_mod.time.monotonic() - 80)                                     # 80 s in: still inside 100 s, past 70 s
    assert late.over() is None and not late.allow_chain_search() and late.skipped == ["chain search (time budget)"]
    over = RunBudget(s, client(), t0=llm_mod.time.monotonic() - 101)
    assert over.over() == "time" and over.time_left() == 0.0 and not over.allow("depth 3")
    off = RunBudget(Settings(_env_file=None, time_budget_s=0, cost_budget_usd=0, chain_start_limit_s=0), client(), t0=llm_mod.time.monotonic() - 10_000)
    assert off.over() is None and off.allow_chain_search() and off.time_left() == float("inf")        # 0 = off


# ------------------------------------------------------------------ in the pipeline
def run(settings, llm=None):
    pages = pages_with("https://independent.org/doc", {"https://independent.org/doc": __import__("tests.test_run", fromlist=["src"]).src("https://independent.org/doc", "Original document. " * 30, CLEAN)})
    return analyze(ARTICLE_URL, fetcher=FakeFetcher(art(html=CLEAN), pages), llm=llm or router(EXTRACT, verifier), settings=settings,
                   rdap=FakeRdap({"independent.org": 4000})).result


def test_without_budget_pressure_the_source_chain_runs_and_nothing_is_skipped():
    r = run(S2)
    assert any(s["depth"] == 2 for s in r["sources"]) and r["audit"]["skipped"] == []
    assert isinstance(r["audit"]["cost_usd"], float) and r["audit"]["llm_calls"] > 0


def test_time_budget_used_up_skips_the_source_chain_but_never_the_claim_checks():
    r = run(Settings(**{**S2.model_dump(), "time_budget_s": 1e-9}))
    assert all(s["depth"] == 1 for s in r["sources"])
    assert r["audit"]["skipped"] == ["source chain (time budget)"]
    assert len(r["claims"]) == 3 and any(c["verdict"] == "supported" for c in r["claims"])             # the claims were still checked


def test_cost_budget_used_up_skips_the_source_chain():
    holder = {}

    def expensive_verifier(user):
        holder["llm"]._record("claude-sonnet-5-5", LLMResult({}, "claude-sonnet-5-5", prompt_tokens=1_000_000))     # 2 USD per verify call
        return verifier(user)
    llm = router(EXTRACT, expensive_verifier)
    holder["llm"] = llm
    r = run(Settings(**{**S2.model_dump(), "cost_budget_usd": 0.5}), llm)
    assert all(s["depth"] == 1 for s in r["sources"]) and r["audit"]["skipped"] == ["source chain (cost budget)"]
    assert r["audit"]["cost_usd"] >= 2.0 and len(r["claims"]) == 3


def test_light_model_is_haiku_with_anthropic_and_the_extract_model_with_the_router():
    assert Settings(_env_file=None, anthropic_api_key="k", model_extract="claude-sonnet-5-5").light_model == "claude-haiku-5-5"
    assert Settings(_env_file=None, model_extract="router-model").light_model == "router-model"
    assert Settings(_env_file=None, anthropic_api_key="k", model_light="custom").light_model == "custom"
