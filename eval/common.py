"""Shared setup for eval scripts. Real model calls use the same Verda client as the shipped backend.

Fetching/search keys are read from ``EVAL_ENV_FILE`` (default: repo ``.env``). The Verda key is loaded into the
process environment only; it is never printed or copied into tracked files. LLM answers are cached per full prompt
under ``eval/runs`` so interrupted runs resume without repeating successful paid calls.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
from pathlib import Path

EVAL = Path(__file__).resolve().parent
ROOT = EVAL.parent
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(EVAL))

RUNS = Path(os.environ.get("EVAL_RUNS_DIR") or EVAL / "runs")       # raw per-item output and the LLM answer cache
RESULTS = Path(os.environ.get("EVAL_RESULTS_DIR") or EVAL / "results")  # per-item verdicts + metrics.json
DATA = EVAL / "data"

MODEL_EXTRACT = "deepseek-4-1-flash-1017"
MODEL_VERIFY = "deepseek-4-1-flash-1017"
PROMPT_VERSION = "v13"
EFFORT = "none"                  # Verda's default reasoning mode; same endpoint/model as production

OVERRIDES = {
    "MODEL_EXTRACT": MODEL_EXTRACT, "MODEL_VERIFY": MODEL_VERIFY,
    "PROMPT_VERSION": PROMPT_VERSION,
    "MODEL_LIGHT": "", "MODEL_CHECK": "", "MODEL_SCREEN": "", "SCREEN_CANDIDATES": "false",
    "CHAIN_SEARCH": "false", "STEP_CACHE": "false",
    "ANTHROPIC_API_KEY": "", "AI_ROUTER_API_KEY": "", "LLM_PROVIDER": "verda",
}

KEYS_FROM_ENV = ("CONTEXT_DEV_API_KEY", "EXA_API_KEY", "VERDA_API_KEY", "VERDA_BASE_URL")


def load_env() -> None:
    path = Path(os.environ.get("EVAL_ENV_FILE") or ROOT / ".env")
    if path.is_file():
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            if k.strip() in KEYS_FROM_ENV:
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    os.environ.update(OVERRIDES)


def settings():
    load_env()
    from app.config import Settings, get_settings
    get_settings.cache_clear()
    return Settings()


def run_id(s) -> str:
    """Identify a result set by every setting that changes model outputs."""
    return f"{s.llm_provider_resolved}:{s.model_extract}:{s.model_verify}:{s.prompt_version}:screen={int(s.screen_candidates)}"


class _DiskCache:
    """Small atomic JSON cache for successful LLM responses; its keys contain no credentials."""

    def __init__(self, path: Path):
        self.path = path
        self.path.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()

    def _file(self, key: str) -> Path:
        return self.path / f"{key}.json"

    def get(self, kind: str, key: str):
        path = self._file(f"{kind}-{key}")
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def put(self, kind: str, key: str, value, ttl_s: float | None = None) -> None:
        path = self._file(f"{kind}-{key}")
        with self.lock:
            fd, tmp = tempfile.mkstemp(dir=self.path, prefix=".tmp-")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(value, f, ensure_ascii=False)
                os.replace(tmp, path)
            finally:
                try:
                    os.unlink(tmp)
                except FileNotFoundError:
                    pass


class EvalClient:
    """Thread-limited adapter around the production client with resumable local caching."""

    def __init__(self, concurrency: int):
        from app.llm import LLMClient, LLMError

        self.llm = LLMClient.from_settings(settings(), cache=_DiskCache(RUNS / "llm_cache"))
        self.sem = threading.Semaphore(concurrency)
        self.limit_hit = threading.Event()     # retained for the resumable runners' shared interface
        self._calls = self._skipped = 0
        self._lock = threading.Lock()
        self._error_type = LLMError

    @property
    def cost_usd(self) -> float:
        return self.llm.usage_since().usd

    def chat_json(self, model: str, system: str, user: str, *, max_tokens: int = 2000):
        if threading.current_thread().name.startswith("d2check"):
            with self._lock:
                self._skipped += 1
            raise self._error_type("skipped in eval: display-only depth check")
        with self.sem:
            result = self.llm.chat_json(model, system, user, max_tokens=max_tokens)
        with self._lock:
            self._calls += 1
        return result

    def stats(self) -> dict:
        usage = self.llm.usage_since()
        with self._lock:
            skipped = self._skipped
        return {"calls": usage.calls, "cache_hits": usage.cached, "skipped_depth_checks": skipped,
                "input_tokens": usage.prompt_tokens, "output_tokens": usage.completion_tokens,
                "usd": round(usage.usd, 4), "unpriced": usage.unpriced}


def client(concurrency: int = 6) -> EvalClient:
    return EvalClient(concurrency)


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text().splitlines() if x.strip()] if path.is_file() else []


def append_jsonl(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
