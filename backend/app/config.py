"""Configuration from environment (see ../.env.example). No secrets in code."""
from __future__ import annotations

import os
import subprocess
from functools import lru_cache
from pathlib import Path

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_REPO_ROOT = Path(__file__).resolve().parents[2]
CURRENT_PROMPT_VERSION = "v13"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(_REPO_ROOT / ".env"), env_file_encoding="utf-8", extra="ignore"
    )

    # LLM: Verda's OpenAI-compatible endpoint is the default. Anthropic and Hostinger Router remain explicit options.
    llm_provider: str = "verda"            # verda | anthropic | router | auto (legacy)
    anthropic_api_key: str = ""
    anthropic_base_url: str = ""           # empty = https://api.anthropic.com
    llm_effort: str = "medium"             # Anthropic output_config.effort: low | medium | high | xhigh | max
    ai_router_base_url: str = ""
    ai_router_api_key: str = ""
    verda_base_url: str = "https://inference.verda.com/v1"
    verda_api_key: str = ""
    model_extract: str = "deepseek-4-1-flash-1017"
    model_verify: str = "deepseek-4-1-flash-1017"
    prompt_version: str = "v13"

    @model_validator(mode="after")
    def one_verda_model(self) -> "Settings":
        """Replace legacy production model settings and keep the Verda candidate screen disabled."""
        if self.llm_provider_resolved == "verda":
            legacy = {"claude-haiku-5-5", "claude-haiku-5.5", "claude-sonnet-5-5", "claude-sonnet-5.5",
                      "gemini-4-argon"}
            if not self.model_extract or not self.model_verify or self.model_extract in legacy or self.model_verify in legacy:
                self.model_extract = self.model_verify = "deepseek-4-1-flash-1017"
                # Older .env files may carry a stale audit tag while code already runs the v13 prompt.
                version = self.prompt_version.removeprefix("v")
                current = CURRENT_PROMPT_VERSION.removeprefix("v")
                if version.isdigit() and current.isdigit() and int(version) < int(current):
                    self.prompt_version = CURRENT_PROMPT_VERSION
            if self.model_light in legacy:
                self.model_light = ""
            if self.model_check in legacy:
                self.model_check = ""
            if self.model_screen in legacy:
                self.model_screen = ""
            self.screen_candidates = False
        return self

    # Scraping / search
    context_dev_api_key: str = ""
    exa_api_key: str = ""
    fetch_timeout_s: float = 20.0
    # Context.dev key limit (raised from 10 to 100 on Nov 14 by Context.dev; verified with a 24-request burst)
    context_dev_rpm: int = 100
    # Context.dev patience for SOURCES (articles keep FETCH_TIMEOUT_S): the direct GET is a quick fallback and slow
    # pages are retried from Context.dev's cache
    source_timeout_s: float = 12.0

    # Storage
    database_url: str = "mysql+pymysql://citetrace:citetrace@localhost:3306/citetrace"

    # Send a second identical LLM request when the first is still unanswered after this many seconds (0 = off)
    llm_hedge_after_s: float = 12.0

    # Wall-clock budget for verifying ALL claims of one article; claims still running then become not_checkable
    claim_deadline_s: float = 240.0

    # Detection (start values from DECISIONS.md; tune only on eval data)
    fuzzy_threshold: float = 90.0
    max_claims: int = 6
    # independent readings of the article for claim selection; a sentence needs a strict majority (see extract.py)
    extract_votes: int = 5
    # How the central claims are chosen (pipeline/extract.py): "summary" = the model summarises the article into key points that cite
    # the numbered sentences stating them (centrality from the summary, K=SUMMARY_VOTES readings, one claim per key point, 2 slots for
    # checkable details); "votes" = a classifier votes EXTRACT_VOTES times which sentences are checkable (sourced ones preferred)
    extract_mode: str = "summary"
    summary_votes: int = 3
    summary_detail_slots: int = 2
    # Self-contained check sentence (pipeline/statement.py): one call per claim decides whether the sentence NEEDS its context and,
    # if so, rewrites it minimally; search and verify use it, the shown claim does not change. A claim that is not checkable is
    # replaced by the next-best candidate. CONTEXTUALIZE_CLAIMS=false = search and verify the sentence as written.
    contextualize_claims: bool = True
    # 2 = what the sources cite (depth 2, display-only checks) + citation-circle signal; 3+ follows further hops (+ time and cost); 1 switches it off; 5 = theoretical max
    max_depth: int = Field(default=2, ge=1, le=5)
    # Root or relay (pipeline/origin.py): claims without any checkable source are classified by MODEL_VERIFY;
    # root (own statement / first-hand) stays neutral, relay ("doctors claim") becomes unsourced
    origin_check: bool = True

    # Also LLM-verify Exa candidates that do not look first-hand (other outlets). They can only ever corroborate
    # (never decide), so skipping them saves ~40% of verify calls; turn on for the 'other reporting confirms' note.
    verify_secondary: bool = False
    # Look for copies of the article on other domains (Exa findSimilar): their infrastructure joins the infrastructure signal
    find_copies: bool = True
    # Domain reputation badge (PLAN Q2): first capture in the Wayback Machine (live CDX API, step-cached). Context only.
    wayback_lookup: bool = True
    wayback_timeout_s: float = 6.0

    # Demo cache + methodology data (both must work with every external API down)
    demo_dir: str = ""          # default: backend/demo
    metrics_path: str = ""      # default: eval/results/metrics.json (repo root, or mounted at /app/eval/results)

    # Step cache (app/cache.py): a finished article replays byte for byte. 0 = never expires.
    step_cache: bool = True
    step_cache_ttl_h: float = 0.0

    # Exa Agent (POST /agent/runs): an extra, high-precision discovery source for claims without a link. A new paid API:
    # ~0.025 USD per run at effort "low" (measured: 83% of claims got a verified supporting page, 64% of its URLs verified).
    exa_agent: bool = True
    exa_agent_effort: str = "low"          # minimal | low | medium | high ...
    exa_agent_timeout_s: float = 50.0      # a run still going after this long is dropped (neutral), never waited for
    exa_agent_concurrency: int = 8         # runs in flight per process (Exa allows 50)
    # Fact-check agent (pipeline/factcheck.py, exa-py SDK): ONE Exa Agent run per claim the article gives no usable source for
    # (unsourced, or attributed with the original not found) that looks for pages CONFIRMING or REFUTING it. A refuting page
    # needs a verbatim, fuzzy-checked quote like every verdict. Replaces the support-only agent run for unsourced claims.
    factcheck_agent: bool = True
    factcheck_effort: str = "low"          # minimal | low | medium | high ... (low ~0.025 USD, medium ~0.10 USD per run)
    factcheck_timeout_s: float = 50.0
    # Optional candidate screen (pipeline/assess.py). Disabled by default: every candidate goes directly to MODEL_VERIFY.
    screen_candidates: bool = False
    model_screen: str = ""                 # screening is off by default; use MODEL_VERIFY for every candidate
    # Agent first: for a claim without a link the Exa Agent (attributed: original search; unsourced: fact-check agent) runs alone;
    # OUR own Exa searches (3-5 requests + up to MAX_EVIDENCE_CANDIDATES verifies) only start when the agent's pages settle nothing
    # (attributed: no `supported` original; unsourced: nothing confirmed or refuted). Same latency when the agent succeeds, one
    # search round later when it does not. AGENT_FIRST=false = both side by side as before.
    agent_first: bool = True
    # Display-only checks (a depth >= 2 source against its claim) never reach verdict.py. Empty: use MODEL_VERIFY.
    model_check: str = ""
    # Optional override for small helpers (search plan, attribution naming, check sentence). Empty: use MODEL_EXTRACT.
    model_light: str = ""
    # Claims the article leaves WITHOUT any source: still look for a page that supports them. One that does (verbatim quote,
    # fuzzy-checked) makes the claim `supported`; it is listed in `found_source_ids`, not `source_ids` (the article cites none)
    search_unsourced: bool = True
    # a page found for one unsourced claim is also checked against the other unsourced claims (one verify call per pair)
    cross_check_found: bool = True
    # Source of the source WITHOUT a link: a depth >= 2 passage that names where its information comes from ("the New York Times
    # reported", "according to a study by ...") but links nothing -> the name is read from the passage (one cheap LLM call) and
    # the Exa Agent + our searches look for that page (pipeline/chainsearch.py). Costs ~0.025 USD per search at effort "low".
    chain_search: bool = True
    max_chain_searches: int = 1            # per article, over all hops (cost + the slowest tail of a run)
    chain_search_deadline_s: float = 50.0  # wall clock for the searches of ONE hop; an unfinished one is dropped (neutral)
    # Budgets of ONE analysis (0 = off). When one is used up the expensive extras are skipped, never the claim checks: the result is
    # shallower, still valid, and `audit.skipped` says what was left out. Cost = LLM calls only (Exa / Context.dev are not metered).
    # A longer run is preferred to a skipped source chain: these are safety nets for a run that went wrong, not targets.
    time_budget_s: float = 170.0           # no new source-chain hop / chain search starts after this; running hops are cut to what is left
    chain_start_limit_s: float = 120.0     # a chain search (an Exa Agent run, 20-60 s) only starts before this
    cost_budget_usd: float = 1.0           # LLM spend after which the extras beyond the first hop (chain search, depth >= 3) are skipped
    # The article cites another article of ITS OWN site: read that page and follow what IT cites ("source of the source")
    follow_self_citations: bool = True
    max_self_citation_links: int = 2       # per claim
    max_chain_sources: int = 2             # depth-2 pages followed per self-cited page
    # Claim selection prefers sentences that name a source; this many slots stay open for unsourced ones, so a heavily
    # unsourced article cannot hide behind its few sourced sentences
    min_unsourced_claims: int = 2

    # Attributed claims ("X said ...", no link): the evidence search (pipeline/evidence.py)
    max_evidence_candidates: int = 5
    max_evidence_links: int = 2        # found pages shown below the named source
    # Independent reports that confirm the quote but are NOT the original: False = shown as evidence, verdict stays
    # neutral; True = such a claim becomes `supported`. Off by default: echo chambers repeat each other.
    corroboration_counts: bool = False

    # Runtime
    max_concurrent_analyses: int = 2
    public_base_url: str = ""

    @property
    def light_model(self) -> str:
        """Model for small helper calls (search plan, attribution naming, check sentence). Default: the only configured model."""
        return self.model_light or self.model_extract

    @property
    def check_model(self) -> str:
        """Model for display-only checks (depth >= 2, source of a source)."""
        return self.model_check or self.model_verify

    @property
    def screen_model(self) -> str:
        """Model that screens search candidates, or "" when the screen is off."""
        if not self.screen_candidates:
            return ""
        return self.model_screen or ("claude-haiku-5-5" if self.llm_provider_resolved == "anthropic" else "")

    @property
    def llm_provider_resolved(self) -> str:
        """verda | anthropic | router. "auto" = Anthropic when its key is set, otherwise the router."""
        if self.llm_provider in ("verda", "anthropic", "router"):
            return self.llm_provider
        return "anthropic" if self.anthropic_api_key else "router"

    @property
    def llm_missing(self) -> list[str]:
        """Names of the .env entries the LLM calls still need (empty = configured)."""
        if self.llm_provider_resolved == "anthropic":
            need = (("ANTHROPIC_API_KEY", self.anthropic_api_key),)
        elif self.llm_provider_resolved == "verda":
            need = (("VERDA_API_KEY", self.verda_api_key),)
        else:
            need = (("AI_ROUTER_BASE_URL", self.ai_router_base_url), ("AI_ROUTER_API_KEY", self.ai_router_api_key))
        return [n for n, v in (*need, ("MODEL_EXTRACT", self.model_extract), ("MODEL_VERIFY", self.model_verify)) if not v]


@lru_cache
def get_settings() -> Settings:
    return Settings()


@lru_cache
def get_version() -> str:
    """Git sha for /api/health: $GIT_SHA (docker build), else git, else 'dev'."""
    if os.environ.get("GIT_SHA"):
        return os.environ["GIT_SHA"]
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=_REPO_ROOT, capture_output=True, text=True, timeout=3, check=True,
        )
        return out.stdout.strip() or "dev"
    except Exception:
        return "dev"
