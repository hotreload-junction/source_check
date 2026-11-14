"""Configuration from environment (see ../.env.example). No secrets in code."""
from __future__ import annotations

import os
import subprocess
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

_REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(_REPO_ROOT / ".env"), env_file_encoding="utf-8", extra="ignore"
    )

    # LLM (Hostinger AI Router)
    ai_router_base_url: str = ""
    ai_router_api_key: str = ""
    model_extract: str = ""
    model_verify: str = ""
    prompt_version: str = "v7"

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
    claim_deadline_s: float = 120.0

    # Detection (start values from DECISIONS.md; tune only on eval data)
    fuzzy_threshold: float = 90.0
    max_claims: int = 6
    # independent readings of the article for claim selection; a sentence needs a strict majority (see extract.py)
    extract_votes: int = 5
    max_depth: int = 2          # 2 = depth 2 (what the sources cite, no extra LLM calls) + citation-circle signal; 1 switches it off

    # Also LLM-verify Exa candidates that do not look first-hand (other outlets). They can only ever corroborate
    # (never decide), so skipping them saves ~40% of verify calls; turn on for the 'other reporting confirms' note.
    verify_secondary: bool = False

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
    # Claims the article leaves WITHOUT any source: still look for a page that supports them. One that does (verbatim quote,
    # fuzzy-checked) makes the claim `supported`; it is listed in `found_source_ids`, not `source_ids` (the article cites none)
    search_unsourced: bool = True
    # a page found for one unsourced claim is also checked against the other unsourced claims (one verify call per pair)
    cross_check_found: bool = True
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
