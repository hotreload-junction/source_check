"""LLM client for the evaluation: every model call goes through the Claude Code CLI (`claude -p`), logged in with the
operator's own Claude subscription (leo's decision, Sun 15 Nov: no API credits left for the eval).

Drop-in for `app.llm.LLMClient`: the pipeline only ever calls `chat_json(model, system, user, max_tokens=...)`.
- Our system prompt REPLACES Claude Code's (`--system-prompt`), no tools, no settings / MCP / CLAUDE.md from the project
  (the subprocess runs in an empty directory). The CLI adds ~0.9k tokens of its own context per call.
- ANTHROPIC_API_KEY is removed from the subprocess environment, so the CLI uses the subscription, never API billing.
- Answers are cached on disk by a hash of (model, system, user): a run that stops at the usage limit resumes for free.
- Usage limit: the first call that reports it sets `limit_hit`; every later call fails at once (LLMError), and the
  harness discards every item that was running at that moment (a half-checked article must never enter the metrics).
- Depth >= 2 checks (`Source.check`, display only, never read by verdict.py) are skipped to save the limit: they run in
  threads named "d2check", and a failed check simply leaves the key out (pipeline/depth2.py).
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from app.llm import JSON_ONLY, LLMError, LLMResult, parse_json_loose

# Only these mean the SUBSCRIPTION is used up; a plain 429 / overloaded is a short throttle and is retried.
LIMIT_MARKERS = ("usage limit", "limit reached", "5-hour", "five-hour", "weekly limit", "out of extra usage")
THROTTLE_MARKERS = ("rate limit", "rate_limit", "overloaded", "429", "529", "too many requests")


class ClaudeCLI:
    def __init__(self, cache_dir: Path, *, effort: str = "medium", concurrency: int = 6, timeout_s: float = 240.0,
                 skip_threads: tuple[str, ...] = ("d2check",)):
        self.cache_dir = cache_dir
        cache_dir.mkdir(parents=True, exist_ok=True)
        self.effort, self.timeout_s, self.skip_threads = effort, timeout_s, skip_threads
        self.sem = threading.Semaphore(concurrency)
        self.limit_hit = threading.Event()
        self.lock = threading.Lock()
        self.cost_usd = 0.0          # API list-price equivalent the CLI reports (what the same calls would cost on the API)
        self.calls = self.cache_hits = self.skipped = 0
        self.cwd = tempfile.mkdtemp(prefix="eval-claude-")
        self.env = {k: v for k, v in os.environ.items() if k not in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL")}
        self.env["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] = "1"

    def _key(self, model: str, system: str, user: str) -> Path:
        h = hashlib.sha256(json.dumps([model, system, user, self.effort]).encode()).hexdigest()
        return self.cache_dir / f"{h}.json"

    def chat_json(self, model: str, system: str, user: str, *, max_tokens: int = 2000) -> LLMResult:
        if threading.current_thread().name.startswith(self.skip_threads):
            with self.lock:
                self.skipped += 1
            raise LLMError("skipped in eval: display-only depth check")
        path = self._key(model, system, user)
        if path.is_file():
            hit = json.loads(path.read_text())
            with self.lock:
                self.cache_hits += 1
            return LLMResult(data=hit["data"], model=hit["model"], cached=True)
        if self.limit_hit.is_set():
            raise LLMError("subscription usage limit reached")
        for attempt in range(5):
            try:
                return self._call(model, system, user, path)
            except _Throttled as exc:
                self._log(f"throttled (attempt {attempt + 1}): {exc}")
                time.sleep(15 * (attempt + 1))
        raise LLMError("claude CLI throttled after retries")

    def _log(self, msg: str) -> None:
        with self.lock, (self.cache_dir.parent / "cli_errors.log").open("a") as f:
            f.write(time.strftime("%H:%M:%S ") + msg[:500] + "\n")

    def _call(self, model: str, system: str, user: str, path: Path) -> LLMResult:
        with self.sem:
            if self.limit_hit.is_set():
                raise LLMError("subscription usage limit reached")
            cmd = ["claude", "-p", "--model", model, "--effort", self.effort, "--tools", "", "--setting-sources", "",
                   "--strict-mcp-config", "--no-session-persistence", "--output-format", "json",
                   "--system-prompt", system + JSON_ONLY]
            try:
                proc = subprocess.run(cmd, input=user, capture_output=True, text=True, timeout=self.timeout_s,
                                      cwd=self.cwd, env=self.env)
            except subprocess.TimeoutExpired as exc:
                raise LLMError("claude CLI timed out") from exc
        out = proc.stdout.strip()
        try:
            d = json.loads(out) if out else {}
        except json.JSONDecodeError:
            d = {}
        text = str(d.get("result") or "") + " " + proc.stderr[-500:]
        with self.lock:
            self.calls += 1
            self.cost_usd += float(d.get("total_cost_usd") or 0.0)
        if proc.returncode != 0 or d.get("is_error") or d.get("subtype") not in (None, "success"):
            low = text.lower()
            if any(m in low for m in LIMIT_MARKERS):
                self._log(f"LIMIT: status={d.get('api_error_status')} {text.strip()}")
                self.limit_hit.set()
                raise LLMError("subscription usage limit reached")
            if d.get("api_error_status") in (429, 529) or any(m in low for m in THROTTLE_MARKERS):
                raise _Throttled(f"status={d.get('api_error_status')} {text.strip()[:200]}")
            self._log(f"error: status={d.get('api_error_status')} rc={proc.returncode} {text.strip()}")
            raise LLMError(f"claude CLI error: {text.strip()[:200]}")
        data = parse_json_loose(str(d.get("result") or ""))
        used = next(iter((d.get("modelUsage") or {}).keys()), model)
        mu = d.get("modelUsage") or {}
        used = model if model in mu else used
        path.write_text(json.dumps({"data": data, "model": used}))
        u = d.get("usage") or {}
        return LLMResult(data=data, model=used, prompt_tokens=int(u.get("input_tokens") or 0) + int(u.get("cache_read_input_tokens") or 0)
                         + int(u.get("cache_creation_input_tokens") or 0), completion_tokens=int(u.get("output_tokens") or 0))

    def stats(self) -> dict:
        return {"calls": self.calls, "cache_hits": self.cache_hits, "skipped_depth_checks": self.skipped,
                "api_equivalent_usd": round(self.cost_usd, 2), "limit_hit": self.limit_hit.is_set()}


class _Throttled(Exception):
    pass
