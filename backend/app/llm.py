"""Thin client for the Hostinger AI Router (OpenAI-compatible /chat/completions). JSON mode, temperature 0."""
from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass

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
                 backoff_s: float = 2.0, client: httpx.Client | None = None):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self.retries = retries
        self.backoff_s = backoff_s
        self.client = client or httpx.Client(timeout=timeout)

    @classmethod
    def from_settings(cls, settings=None) -> "LLMClient":
        from .config import get_settings

        s = settings or get_settings()
        return cls(s.ai_router_base_url, s.ai_router_api_key)

    def chat_json(self, model: str, system: str, user: str, *, max_tokens: int = 2000) -> LLMResult:
        if not (self.base_url and self.api_key and model):
            raise LLMError("AI router not configured (AI_ROUTER_BASE_URL / AI_ROUTER_API_KEY / model)")
        body = {
            "model": model, "temperature": 0, "max_tokens": max_tokens,
            "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        }
        last = ""
        for attempt in range(self.retries + 1):
            if attempt:
                time.sleep(self.backoff_s * attempt)
            try:
                r = self.client.post(f"{self.base_url}/chat/completions", json=body,
                                     headers={"Authorization": f"Bearer {self.api_key}"})
            except httpx.HTTPError as exc:
                last = type(exc).__name__
                continue
            if r.status_code in (429, 500, 502, 503, 504):
                last = f"http {r.status_code}"
                continue
            if r.status_code != 200:
                raise LLMError(f"router http {r.status_code}: {r.text[:200]}")
            try:
                payload = r.json()
                content = payload["choices"][0]["message"]["content"] or ""
            except (ValueError, KeyError, IndexError, TypeError) as exc:
                raise LLMError(f"unexpected router response: {exc}") from exc
            usage = payload.get("usage") or {}
            return LLMResult(
                data=parse_json_loose(content), model=payload.get("model", model),
                prompt_tokens=usage.get("prompt_tokens", 0), completion_tokens=usage.get("completion_tokens", 0),
                credits=usage.get("credits_charged", 0.0),
            )
        raise LLMError(f"router unavailable after retries ({last})")
