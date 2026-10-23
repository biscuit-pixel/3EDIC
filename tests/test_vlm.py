"""VLM provider tests against mocked HTTP (no real API calls)."""
import json

import httpx
import pytest

from edic.vlm import providers

JPEG = b"\xff\xd8\xff\xe0fake"


def client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


@pytest.mark.parametrize("text,want", [
    ('{"number": 41, "confidence": 0.9}', (41, 0.9)),
    ('{"number": null, "confidence": 0}', (None, 0.0)),
    ('Sure! {"number": "32", "confidence": 0.8} hope that helps', (32, 0.8)),
    ("I can't tell", (None, 0.0)),
    ('{"number": 5000, "confidence": 1}', (None, 0.0)),           # out of range -> no number
    ('{"number": 7, "confidence": 7}', (7, 1.0)),                 # confidence is clamped
    ('{"number": 0, "confidence": 1}', (None, 0.0)),
])
def test_parse_reply(text, want):
    assert providers.parse_reply(text) == want


def test_anthropic_request_and_reply():
    seen = {}

    def handler(req):
        body = json.loads(req.content)
        seen.update(url=str(req.url), key=req.headers["x-api-key"], kind=body["messages"][0]["content"][0]["type"])
        return httpx.Response(200, json={"content": [{"type": "text", "text": '{"number": 36, "confidence": 0.95}'}]})
    assert providers.make("anthropic", "k1", client=client(handler)).read_number(JPEG) == (36, 0.95)
    assert seen == {"url": "https://api.anthropic.com/v1/messages", "key": "k1", "kind": "image"}


def test_openai_request_and_reply():
    seen = {}

    def handler(req):
        seen["auth"] = req.headers["authorization"]
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"number": 6, "confidence": 0.9}'}}]})
    assert providers.make("openai", "k2", client=client(handler)).read_number(JPEG) == (6, 0.9)
    assert seen["auth"] == "Bearer k2"


def test_gemini_key_goes_in_a_header_not_the_url():
    seen = {}

    def handler(req):
        seen.update(url=str(req.url), key=req.headers["x-goog-api-key"])
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": '{"number": 41, "confidence": 0.8}'}]}}]})
    assert providers.make("gemini", "k3", client=client(handler)).read_number(JPEG) == (41, 0.8)
    assert "k3" not in seen["url"] and seen["key"] == "k3"


def test_retries_on_rate_limit(monkeypatch):
    monkeypatch.setattr(providers.time, "sleep", lambda s: None)
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        return httpx.Response(429) if calls["n"] < 3 else httpx.Response(200, json={"content": [{"type": "text", "text": '{"number": 12, "confidence": 0.7}'}]})
    assert providers.make("anthropic", "k", client=client(handler)).read_number(JPEG) == (12, 0.7)
    assert calls["n"] == 3


def test_bad_key_is_an_error_not_a_silent_no_number():
    with pytest.raises(providers.VlmError, match="refused"):
        providers.make("anthropic", "bad", client=client(lambda r: httpx.Response(401))).read_number(JPEG)
    with pytest.raises(providers.VlmError):
        providers.make("anthropic", "")
    with pytest.raises(providers.VlmError):
        providers.make("nope", "k")


def test_persistent_rate_limit_gives_up(monkeypatch):
    monkeypatch.setattr(providers.time, "sleep", lambda s: None)
    with pytest.raises(providers.VlmError, match="rate-limiting"):
        providers.make("anthropic", "k", client=client(lambda r: httpx.Response(429))).read_number(JPEG)
