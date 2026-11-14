"""Thin client for the Hostinger AI Router (OpenAI-compatible /chat/completions). JSON mode, temperature 0."""
from __future__ import annotations

import json
import logging
import re
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import asdict, dataclass

import httpx

log = logging.getLogger("citetrace.llm")


class LLMError(RuntimeError):
    pass


@dataclass
class LLMResult:
    data: dict
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    credits: float = 0.0
    cached: bool = False        # replayed from our step cache: no call, no cost


def parse_json_loose(content: str) -> dict:
    """JSON mode usually returns clean JSON; some models still wrap it in ``` fences or add prose."""
    content = content.strip()
    fenced = re.match(r"^```(?:json)?\s*(.*?)\s*```$", content, re.S)
    if fenced:
        content = fenced.group(1)
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        start, end = content.find("{"), content.rfind("}")
        if start < 0 or end <= start:
            raise LLMError(f"no JSON object in model output: {content[:120]!r}")
        try:
            data = json.loads(content[start:end + 1])
        except json.JSONDecodeError as exc:
            raise LLMError(f"invalid JSON from model: {exc}") from exc
    if not isinstance(data, dict):
        raise LLMError("model returned JSON that is not an object")
    return data


class LLMClient:
    def __init__(self, base_url: str, api_key: str, *, timeout: float = 90.0, retries: int = 2,
                 backoff_s: float = 2.0, client: httpx.Client | None = None, hedge_after: float | None = None,
                 cache=None):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self.retries = retries
        self.backoff_s = backoff_s
        self.client = client or httpx.Client(timeout=timeout)
        self.hedge_after = hedge_after
        self.cache = cache          # None = the process-wide default (cache.get_default()), looked up per call

    @classmethod
    def from_settings(cls, settings=None, cache=None) -> "LLMClient":
        from .config import get_settings

        s = settings or get_settings()
        return cls(s.ai_router_base_url, s.ai_router_api_key,
                   hedge_after=s.llm_hedge_after_s or None, cache=cache)

    def chat_json(self, model: str, system: str, user: str, *, max_tokens: int = 2000) -> LLMResult:
        """One JSON completion. With `hedge_after` set, a request still unanswered after that many seconds is
        sent a second time and the first answer wins. Measured live: verify median 5 s but p90 16 s / max 69 s, and
        a claim waits for its slowest candidate, so the tail is the critical path; this costs ~10% extra calls."""
        from . import cache as cachemod

        cache = self.cache if self.cache is not None else cachemod.get_default()
        key = cachemod.make_key("llm", model, system, user)
        hit = cache.get("llm", key)
        if hit is not None:
            return LLMResult(**{**hit, "credits": 0.0, "cached": True})
        result = self._chat_json_hedged(model, system, user, max_tokens)
        cache.put("llm", key, {k: v for k, v in asdict(result).items() if k != "cached"})
        return result

    def _chat_json_hedged(self, model: str, system: str, user: str, max_tokens: int) -> LLMResult:
        if not self.hedge_after:
            return self._chat_json(model, system, user, max_tokens)
        pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="llm-hedge")
        try:
            first = pool.submit(self._chat_json, model, system, user, max_tokens)
            done, _ = wait([first], timeout=self.hedge_after)
            if done:
                return first.result()
            second = pool.submit(self._chat_json, model, system, user, max_tokens)
            pending = {first, second}
            error: Exception | None = None
            while pending:
                done, pending = wait(pending, return_when=FIRST_COMPLETED)
                for f in done:
                    if f.exception() is None:
                        return f.result()
                    error = f.exception()
            raise error  # both failed
        finally:
            pool.shutdown(wait=False)   # the slower request finishes in the background; its result is dropped

    def _chat_json(self, model: str, system: str, user: str, max_tokens: int) -> LLMResult:
        if not (self.base_url and self.api_key and model):
            raise LLMError("AI router not configured (AI_ROUTER_BASE_URL / AI_ROUTER_API_KEY / model)")
        MAX_BUDGET = 8000
        body = {
            "model": model, "temperature": 0, "max_tokens": max_tokens,
            "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        }
        last = ""
        failures = 0       # transient errors / empty completions, bounded by self.retries
        truncations = 0    # budget doublings, bounded separately (they are not failures)
        while True:
            if failures > self.retries:
                break
            if failures:
                time.sleep(self.backoff_s * failures)
            try:
                r = self.client.post(f"{self.base_url}/chat/completions", json=body,
                                     headers={"Authorization": f"Bearer {self.api_key}"})
            except httpx.HTTPError as exc:
                last, failures = type(exc).__name__, failures + 1
                continue
            if r.status_code in (429, 500, 502, 503, 504):
                last, failures = f"http {r.status_code}", failures + 1
                continue
            if r.status_code != 200:
                raise LLMError(f"router http {r.status_code}: {r.text[:200]}")
            try:
                payload = r.json()
                choice = payload["choices"][0]
                content = choice["message"]["content"] or ""
            except (ValueError, KeyError, IndexError, TypeError) as exc:
                raise LLMError(f"unexpected router response: {exc}") from exc
            if choice.get("finish_reason") == "length":
                # Observed live: the budget also covers the model's hidden reasoning, so a small max_tokens gives
                # empty or cut-off JSON. Never parse a truncated answer; retry with a bigger budget.
                if body["max_tokens"] >= MAX_BUDGET or truncations >= 4:
                    raise LLMError("model output truncated even at the maximum token budget")
                body["max_tokens"] = min(body["max_tokens"] * 2, MAX_BUDGET)
                truncations += 1
                continue
            if not content.strip():   # observed live: sporadic empty completions -> retry
                last, failures = "empty completion", failures + 1
                continue
            usage = payload.get("usage") or {}
            return LLMResult(
                data=parse_json_loose(content), model=payload.get("model", model),
                prompt_tokens=usage.get("prompt_tokens", 0), completion_tokens=usage.get("completion_tokens", 0),
                credits=usage.get("credits_charged", 0.0),
            )
        raise LLMError(f"router unavailable after retries ({last})")
