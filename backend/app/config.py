"""Configuration from environment (see ../.env.example). No secrets in code."""
from __future__ import annotations

import os
import subprocess
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

_REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(_REPO_ROOT / ".env"), env_file_encoding="utf-8", extra="ignore"
    )

    # LLM: the Anthropic API (ANTHROPIC_API_KEY; MODEL_EXTRACT / MODEL_VERIFY are Claude model ids) or the Hostinger AI Router
    # (AI_ROUTER_*). "auto" = Anthropic when a key is set, else the router.
    llm_provider: str = "auto"             # auto | anthropic | router
    anthropic_api_key: str = ""
    anthropic_base_url: str = ""           # empty = https://api.anthropic.com
    llm_effort: str = "medium"             # Anthropic output_config.effort: low | medium | high | xhigh | max
    ai_router_base_url: str = ""
    ai_router_api_key: str = ""
    model_extract: str = ""
    model_verify: str = ""
    prompt_version: str = "v10"

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
    # 3 = depth 2-3 source chain crawl (no extra LLM calls) + citation-circle signal; 1 switches it off; 5 = theoretical max
    max_depth: int = Field(default=3, ge=1, le=5)
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
    exa_agent_timeout_s: float = 80.0      # a run still going after this long is dropped (neutral), never waited for
    exa_agent_concurrency: int = 8         # runs in flight per process (Exa allows 50)
    # Fact-check agent (pipeline/factcheck.py, exa-py SDK): ONE Exa Agent run per claim the article gives no usable source for
    # (unsourced, or attributed with the original not found) that looks for pages CONFIRMING or REFUTING it. A refuting page
    # needs a verbatim, fuzzy-checked quote like every verdict. Replaces the support-only agent run for unsourced claims.
    factcheck_agent: bool = True
    factcheck_effort: str = "low"          # minimal | low | medium | high ... (low ~0.025 USD, medium ~0.10 USD per run)
    factcheck_timeout_s: float = 80.0
    # Candidate screen (pipeline/assess.py): a page SEARCH found (named / unsourced / fact-check / cross-check) is read first by a
    # cheap model with the SAME verify prompt. "not_mentioned" ends it there (such a page is neutral anyway); anything else goes on
    # to MODEL_VERIFY, which has the last word. Pages the article cites itself are never screened. ~20x cheaper than MODEL_VERIFY
    # on the pages that do not mention the claim, which is most of them. SCREEN_CANDIDATES=false = every candidate goes to MODEL_VERIFY.
    screen_candidates: bool = True
    model_screen: str = ""                 # empty: claude-haiku-5-5 with the Anthropic API, no screen with the router
    # Agent first: for a claim without a link the Exa Agent (attributed: original search; unsourced: fact-check agent) runs alone;
    # OUR own Exa searches (3-5 requests + up to MAX_EVIDENCE_CANDIDATES verifies) only start when the agent's pages settle nothing
    # (attributed: no `supported` original; unsourced: nothing confirmed or refuted). Same latency when the agent succeeds, one
    # search round later when it does not. AGENT_FIRST=false = both side by side as before.
    agent_first: bool = True
    # Display-only checks (a depth >= 2 source against its claim, a page found as the source of a source) use this cheaper model;
    # they never reach verdict.py. Empty: claude-haiku-5-5 with the Anthropic API, MODEL_VERIFY with the router.
    model_check: str = ""
    # Claims the article leaves WITHOUT any source: still look for a page that supports them. One that does (verbatim quote,
    # fuzzy-checked) makes the claim `supported`; it is listed in `found_source_ids`, not `source_ids` (the article cites none)
    search_unsourced: bool = True
    # a page found for one unsourced claim is also checked against the other unsourced claims (one verify call per pair)
    cross_check_found: bool = True
    # Source of the source WITHOUT a link: a depth >= 2 passage that names where its information comes from ("the New York Times
    # reported", "according to a study by ...") but links nothing -> the name is read from the passage (one cheap LLM call) and
    # the Exa Agent + our searches look for that page (pipeline/chainsearch.py). Costs ~0.025 USD per search at effort "low".
    chain_search: bool = True
    max_chain_searches: int = 3            # per article, over all hops
    chain_search_deadline_s: float = 75.0  # wall clock for the searches of ONE hop; an unfinished one is dropped (neutral)
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
    def check_model(self) -> str:
        """Model for display-only checks (depth >= 2, source of a source)."""
        return self.model_check or ("claude-haiku-5-5" if self.llm_provider_resolved == "anthropic" else self.model_verify)

    @property
    def screen_model(self) -> str:
        """Model that screens search candidates, or "" when the screen is off."""
        if not self.screen_candidates:
            return ""
        return self.model_screen or ("claude-haiku-5-5" if self.llm_provider_resolved == "anthropic" else "")

    @property
    def llm_provider_resolved(self) -> str:
        """anthropic | router. "auto" = Anthropic as soon as ANTHROPIC_API_KEY is set."""
        if self.llm_provider in ("anthropic", "router"):
            return self.llm_provider
        return "anthropic" if self.anthropic_api_key else "router"

    @property
    def llm_missing(self) -> list[str]:
        """Names of the .env entries the LLM calls still need (empty = configured)."""
        need = (("ANTHROPIC_API_KEY", self.anthropic_api_key),) if self.llm_provider_resolved == "anthropic" else (
            ("AI_ROUTER_BASE_URL", self.ai_router_base_url), ("AI_ROUTER_API_KEY", self.ai_router_api_key))
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
