"""Shared setup for the eval scripts: import path, settings for the eval run, the Claude CLI client.

Keys for fetching / search (Context.dev, Exa) come from an .env file (EVAL_ENV_FILE, default: the repo's .env). The
LLM keys are blanked on purpose: every model call goes through `claude_cli.ClaudeCLI`, so nothing can fall back to paid
API billing by accident.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

EVAL = Path(__file__).resolve().parent
ROOT = EVAL.parent
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(EVAL))

RUNS = EVAL / "runs"            # gitignored: raw per-item output and the LLM answer cache
RESULTS = EVAL / "results"      # committed: per-item verdicts (no third-party article text) + metrics.json
DATA = EVAL / "data"

MODEL_EXTRACT = "claude-haiku-5-5"
MODEL_VERIFY = "claude-sonnet-5-5"
EFFORT = "medium"               # same as the API default in backend/app/config.py (LLM_EFFORT)

# Eval-only switches. Both are display-only features (DECISIONS Sun: chain search + depth checks never reach verdict.py),
# so the article verdicts are the same as with them on; switching them off saves the subscription limit.
OVERRIDES = {
    "MODEL_EXTRACT": MODEL_EXTRACT, "MODEL_VERIFY": MODEL_VERIFY,
    "CHAIN_SEARCH": "false", "STEP_CACHE": "false",
    "ANTHROPIC_API_KEY": "", "AI_ROUTER_API_KEY": "", "LLM_PROVIDER": "router",
}


KEYS_FROM_ENV = ("CONTEXT_DEV_API_KEY", "EXA_API_KEY")


def load_env() -> None:
    path = Path(os.environ.get("EVAL_ENV_FILE") or ROOT / ".env")
    if path.is_file():
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            if k.strip() in KEYS_FROM_ENV:      # only access keys: detection settings stay at the code defaults (as on the VPS)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    os.environ.update(OVERRIDES)


def settings():
    load_env()
    from app.config import Settings, get_settings
    get_settings.cache_clear()
    return Settings()


def client(concurrency: int = 6):
    from claude_cli import ClaudeCLI
    return ClaudeCLI(RUNS / "llm_cache", effort=EFFORT, concurrency=concurrency)


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text().splitlines() if x.strip()] if path.is_file() else []


def append_jsonl(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
