"""The Anthropic Messages API path of LLMClient (official SDK, mock transport: no network, no key)."""
import json

import anthropic
import httpx2
import pytest

from app.config import Settings
from app.llm import JSON_ONLY, LLMClient, LLMError


def message(text='{"verdict": "supported"}', stop="end_turn", blocks=None, model="claude-haiku-5-5"):
    return {"id": "msg_1", "type": "message", "role": "assistant", "model": model, "stop_reason": stop, "stop_sequence": None,
            "content": blocks if blocks is not None else [{"type": "text", "text": text}],
            "usage": {"input_tokens": 12, "output_tokens": 7}}


def client(responses, seen=None, retries=2):
    """`responses`: list of (status, json) served in order (the last one repeats)."""
    calls = iter(range(10 ** 6))

    def handler(request):
        i = min(next(calls), len(responses) - 1)
        if seen is not None:
            seen.append({"url": str(request.url), "headers": dict(request.headers), "body": json.loads(request.content)})
        status, payload = responses[i]
        return httpx2.Response(status, json=payload)
    http = anthropic.DefaultHttpxClient(transport=httpx2.MockTransport(handler))
    return LLMClient.anthropic("sk-ant-test", retries=retries, backoff_s=0, http_client=http, effort="low")


def test_request_shape_and_json_answer():
    seen = []
    res = client([(200, message())], seen).chat_json("claude-haiku-5-5", "SYSTEM RULES", "USER TEXT", max_tokens=300)
    assert res.data == {"verdict": "supported"} and res.model == "claude-haiku-5-5"
    assert (res.prompt_tokens, res.completion_tokens) == (12, 7)
    req = seen[0]
    assert req["url"].endswith("/v1/messages") and "sk-ant-test" in req["headers"]["authorization"]
    body = req["body"]
    assert body["model"] == "claude-haiku-5-5" and body["system"] == "SYSTEM RULES" + JSON_ONLY
    assert body["messages"] == [{"role": "user", "content": "USER TEXT"}]
    assert body["output_config"] == {"effort": "low"} and body["max_tokens"] >= 1000
    assert "temperature" not in body and "top_p" not in body                  # rejected by current models
    assert body["messages"][-1]["role"] == "user"                              # no assistant prefill


def test_thinking_blocks_are_skipped_and_fenced_json_is_read():
    blocks = [{"type": "thinking", "thinking": "hmm", "signature": "s"}, {"type": "text", "text": '```json\n{"a": 1}\n```'}]
    assert client([(200, message(blocks=blocks))]).chat_json("m", "s", "u").data == {"a": 1}


def test_overloaded_and_rate_limited_are_retried():
    err = {"type": "error", "error": {"type": "overloaded_error", "message": "busy"}}
    seen = []
    res = client([(529, err), (429, err), (200, message())], seen).chat_json("m", "s", "u")
    assert res.data == {"verdict": "supported"} and len(seen) == 3


def test_gives_up_after_the_retries():
    err = {"type": "error", "error": {"type": "overloaded_error", "message": "busy"}}
    seen = []
    with pytest.raises(LLMError, match="unavailable"):
        client([(529, err)], seen, retries=1).chat_json("m", "s", "u")
    assert len(seen) == 2


def test_client_errors_are_not_retried():
    err = {"type": "error", "error": {"type": "authentication_error", "message": "invalid x-api-key"}}
    seen = []
    with pytest.raises(LLMError, match="401"):
        client([(401, err)], seen).chat_json("m", "s", "u")
    assert len(seen) == 1


def test_a_cut_off_answer_is_retried_with_a_bigger_budget():
    seen = []
    res = client([(200, message('{"verd', stop="max_tokens")), (200, message())], seen).chat_json("m", "s", "u", max_tokens=1500)
    assert res.data == {"verdict": "supported"}
    assert [r["body"]["max_tokens"] for r in seen] == [1500, 3000]


def test_refusal_and_empty_completion():
    with pytest.raises(LLMError, match="declined"):
        client([(200, message("", stop="refusal"))]).chat_json("m", "s", "u")
    seen = []
    with pytest.raises(LLMError, match="empty completion"):
        client([(200, message(""))], seen, retries=1).chat_json("m", "s", "u")
    assert len(seen) == 2


def test_not_configured_without_key_or_model():
    with pytest.raises(LLMError, match="not configured"):
        LLMClient.anthropic("").chat_json("m", "s", "u")
    with pytest.raises(LLMError, match="not configured"):
        client([(200, message())]).chat_json("", "s", "u")


def test_from_settings_picks_the_provider():
    on = Settings(_env_file=None, anthropic_api_key="k", model_extract="a", model_verify="b", ai_router_base_url="https://r/v1", ai_router_api_key="x")
    assert on.llm_provider_resolved == "anthropic" and LLMClient.from_settings(on, cache=None).provider == "anthropic"
    off = Settings(_env_file=None, ai_router_base_url="https://r/v1", ai_router_api_key="x")
    assert off.llm_provider_resolved == "router" and LLMClient.from_settings(off).provider == "router"
    forced = Settings(_env_file=None, llm_provider="router", anthropic_api_key="k")
    assert forced.llm_provider_resolved == "router"
    assert on.llm_missing == [] and "ANTHROPIC_API_KEY" in Settings(_env_file=None, llm_provider="anthropic").llm_missing
