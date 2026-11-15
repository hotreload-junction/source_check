"""LLM client: Anthropic Messages API or an OpenAI-compatible /chat/completions provider (Verda / Hostinger Router).

Anthropic: the official SDK, one JSON object per call. No `temperature` (current models reject sampling parameters), no
assistant prefill (rejected as well): the JSON-only instruction sits in the system prompt and `parse_json_loose` reads the
answer. `output_config.effort` (LLM_EFFORT) sets how much the model thinks; thinking tokens count against `max_tokens`, so a
cut-off answer (`stop_reason: max_tokens`) is retried with a bigger budget, like the router's `finish_reason: length`.
OpenAI-compatible providers: JSON mode, temperature 0. Verda is the production default."""
from __future__ import annotations

import json
import logging
import re
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import asdict, dataclass

import threading
from collections import defaultdict

import httpx

log = logging.getLogger("citetrace.llm")

# USD per million tokens (input, output), Anthropic list prices for prompts up to 100K tokens. Unknown models cost 0 here and
# are reported as unpriced, never guessed.
PRICES_PER_MTOK: dict[str, tuple[float, float]] = {
    "claude-haiku-5-5": (0.10, 0.50), "claude-sonnet-5-5": (2.0, 10.0), "claude-opus-5-5": (4.0, 20.0),
    "claude-sonnet-5": (2.0, 10.0), "claude-opus-5": (5.0, 25.0), "claude-fable-5-2": (8.0, 40.0),
    "deepseek-4-1-flash-1017": (0.15, 0.60),
}


@dataclass
class Usage:
    """What the LLM calls of ONE analysis used. `usd` counts calls that were really made; replays from the step cache are free.
    Hedged duplicate requests whose answer was dropped are not counted (a small under-estimate)."""
    calls: int = 0
    cached: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    usd: float = 0.0
    unpriced: tuple[str, ...] = ()


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


JSON_ONLY = "\n\nAnswer with one JSON object and nothing else: no prose before or after it, no code fences."
RETRY_STATUS = (429, 500, 502, 503, 504, 529)     # 529 = Anthropic "overloaded"


class LLMClient:
    def __init__(self, base_url: str, api_key: str, *, timeout: float = 90.0, retries: int = 2,
                 backoff_s: float = 2.0, client: httpx.Client | None = None, hedge_after: float | None = None,
                 cache=None, provider: str = "router", sdk=None, effort: str = "medium"):
        self.provider = provider    # "router" (OpenAI-compatible) | "anthropic" (Messages API through `sdk`)
        self.sdk = sdk
        self.effort = effort
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self.retries = retries
        self.backoff_s = backoff_s
        self.client = client or httpx.Client(timeout=timeout)
        self.hedge_after = hedge_after
        self.cache = cache          # None = the process-wide default (cache.get_default()), looked up per call
        self._usage: dict[str, list] = defaultdict(lambda: [0, 0, 0, 0])      # model -> calls, cached, prompt tokens, completion tokens
        self._usage_lock = threading.Lock()

    @classmethod
    def from_settings(cls, settings=None, cache=None) -> "LLMClient":
        from .config import get_settings

        s = settings or get_settings()
        if s.llm_provider_resolved == "anthropic":
            return cls.anthropic(s.anthropic_api_key, base_url=s.anthropic_base_url or None, effort=s.llm_effort,
                                 hedge_after=s.llm_hedge_after_s or None, cache=cache)
        if s.llm_provider_resolved == "verda":
            return cls(s.verda_base_url, s.verda_api_key,
                       hedge_after=s.llm_hedge_after_s or None, cache=cache)
        return cls(s.ai_router_base_url, s.ai_router_api_key,
                   hedge_after=s.llm_hedge_after_s or None, cache=cache)

    @classmethod
    def anthropic(cls, api_key: str, *, base_url: str | None = None, timeout: float = 90.0, retries: int = 2,
                  backoff_s: float = 2.0, hedge_after: float | None = None, cache=None, effort: str = "medium",
                  http_client=None) -> "LLMClient":
        """Anthropic Messages API through the official SDK. Retries are ours (`retries`, `backoff_s`), not the SDK's.
        `http_client`: an `anthropic.DefaultHttpxClient` (tests inject a mock transport here)."""
        import anthropic

        sdk = anthropic.Anthropic(api_key=api_key, base_url=base_url, timeout=timeout, max_retries=0,
                                  http_client=http_client) if api_key else None      # no key: every call says "not configured"
        return cls("", api_key, timeout=timeout, retries=retries, backoff_s=backoff_s, hedge_after=hedge_after,
                   cache=cache, provider="anthropic", sdk=sdk, effort=effort)

    def chat_json(self, model: str, system: str, user: str, *, max_tokens: int = 2000) -> LLMResult:
        """One JSON completion. With `hedge_after` set, a request still unanswered after that many seconds is
        sent a second time and the first answer wins. Measured live: verify median 5 s but p90 16 s / max 69 s, and
        a claim waits for its slowest candidate, so the tail is the critical path; this costs ~10% extra calls."""
        from . import cache as cachemod

        cache = self.cache if self.cache is not None else cachemod.get_default()
        key = cachemod.make_key("llm", model, system, user)
        hit = cache.get("llm", key)
        if hit is not None:
            result = LLMResult(**{**hit, "credits": 0.0, "cached": True})
            self._record(model, result)
            return result
        result = self._chat_json_hedged(model, system, user, max_tokens)
        cache.put("llm", key, {k: v for k, v in asdict(result).items() if k != "cached"})
        self._record(model, result)
        return result

    # ---- cost meter -------------------------------------------------------------------------------------------
    def _record(self, model: str, res: LLMResult) -> None:
        with self._usage_lock:
            row = self._usage[res.model or model]
            row[1 if res.cached else 0] += 1
            if not res.cached:
                row[2] += res.prompt_tokens
                row[3] += res.completion_tokens

    def usage_snapshot(self) -> dict[str, list]:
        with self._usage_lock:
            return {m: list(v) for m, v in self._usage.items()}

    def usage_since(self, snapshot: dict[str, list] | None = None) -> Usage:
        """Calls, tokens and USD used since `snapshot` (None = since this client was created)."""
        now = self.usage_snapshot()
        u = Usage()
        unpriced: list[str] = []
        for model, row in now.items():
            base = (snapshot or {}).get(model, [0, 0, 0, 0])
            calls, cached, pt, ct = (row[i] - base[i] for i in range(4))
            u.calls += calls
            u.cached += cached
            u.prompt_tokens += pt
            u.completion_tokens += ct
            price = PRICES_PER_MTOK.get(model)
            if price is None:
                if calls:
                    unpriced.append(model)
                continue
            u.usd += pt / 1e6 * price[0] + ct / 1e6 * price[1]
        u.unpriced = tuple(sorted(unpriced))
        return u

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
        if self.provider == "anthropic":
            return self._chat_json_anthropic(model, system, user, max_tokens)
        if not (self.base_url and self.api_key and model):
            raise LLMError("OpenAI-compatible LLM provider not configured (base URL / API key / model)")
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

    def _chat_json_anthropic(self, model: str, system: str, user: str, max_tokens: int) -> LLMResult:
        import anthropic

        if self.sdk is None or not model:
            raise LLMError("Anthropic not configured (ANTHROPIC_API_KEY / model)")
        MAX_BUDGET = 16000
        budget = max(max_tokens, 1000)     # thinking tokens share the budget: a tiny cap would cut the JSON off
        last = ""
        failures = truncations = 0
        while failures <= self.retries:
            if failures:
                time.sleep(self.backoff_s * failures)
            try:
                msg = self.sdk.messages.create(
                    model=model, max_tokens=budget, system=system + JSON_ONLY,
                    messages=[{"role": "user", "content": user}], output_config={"effort": self.effort})
            except anthropic.APIConnectionError as exc:      # includes timeouts
                last, failures = type(exc).__name__, failures + 1
                continue
            except anthropic.APIStatusError as exc:
                if exc.status_code in RETRY_STATUS:
                    last, failures = f"http {exc.status_code}", failures + 1
                    continue
                raise LLMError(f"anthropic http {exc.status_code}: {str(exc.message)[:200]}") from exc
            if msg.stop_reason == "refusal":
                raise LLMError("the model declined this request (stop_reason refusal)")
            if msg.stop_reason == "max_tokens":
                if budget >= MAX_BUDGET or truncations >= 4:
                    raise LLMError("model output truncated even at the maximum token budget")
                budget, truncations = min(budget * 2, MAX_BUDGET), truncations + 1
                continue
            content = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
            if not content.strip():
                last, failures = "empty completion", failures + 1
                continue
            return LLMResult(data=parse_json_loose(content), model=getattr(msg, "model", None) or model,
                             prompt_tokens=msg.usage.input_tokens, completion_tokens=msg.usage.output_tokens)
        raise LLMError(f"anthropic unavailable after retries ({last})")
