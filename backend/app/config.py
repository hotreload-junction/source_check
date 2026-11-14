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
    prompt_version: str = "v1"

    # Scraping / search
    context_dev_api_key: str = ""
    exa_api_key: str = ""
    fetch_timeout_s: float = 20.0
    # Context.dev key limit (measured: 10 requests/min); raise with a bigger plan
    context_dev_rpm: int = 10

    # Storage
    database_url: str = "mysql+pymysql://citetrace:citetrace@localhost:3306/citetrace"

    # Detection (start values from DECISIONS.md; tune only on eval data)
    fuzzy_threshold: float = 90.0
    max_claims: int = 6
    max_depth: int = 1

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
