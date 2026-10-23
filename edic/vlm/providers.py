"""Vision-LLM providers over plain HTTPS (no SDKs, so the app stays small).

The assist only ever receives small crops of the hip area where the number sits - never faces, never full photos.
"""
from __future__ import annotations

import base64
import json
import re
import time

import httpx

PROMPT = (
    "This is a crop around the hips of a person at a sports competition. "
    "If a competitor number tag is clearly legible, answer with JSON only: "
    '{"number": <integer>, "confidence": <0..1>}. '
    'If there is no number tag, it is covered, cut off, or you are not sure of every digit, answer {"number": null, "confidence": 0}. '
    "Never guess. Digits only, 1 to 3 digits."
)

DEFAULT_MODELS = {"anthropic": "claude-haiku-5-5", "openai": "gpt-4.1-mini", "gemini": "gemini-2.0-flash"}
# rough list prices in USD per million input tokens, only for the cost estimate shown before a run
PRICE_PER_MTOK = {"anthropic": 1.0, "openai": 0.4, "gemini": 0.1}


class VlmError(RuntimeError):
    pass


def parse_reply(text: str) -> tuple[int | None, float]:
    """Pull {"number", "confidence"} out of a model reply; anything unclear counts as 'no number'."""
    m = re.search(r"\{.*?\}", text or "", re.S)
    if not m:
        return None, 0.0
    try:
        d = json.loads(m.group(0))
    except ValueError:
        return None, 0.0
    n = d.get("number")
    try:
        n = int(n) if n is not None else None
    except (TypeError, ValueError):
        n = None
    if n is None or not 1 <= n <= 999:
        return None, 0.0
    try:
        conf = float(d.get("confidence", 0.5))
    except (TypeError, ValueError):
        conf = 0.5
    return n, max(0.0, min(1.0, conf))


class Provider:
    name = ""

    def __init__(self, api_key: str, model: str | None = None, client: httpx.Client | None = None):
        if not api_key:
            raise VlmError("no API key set")
        self.key = api_key
        self.model = model or DEFAULT_MODELS[self.name]
        self.client = client or httpx.Client(timeout=httpx.Timeout(60, connect=15))

    def read_number(self, jpeg: bytes) -> tuple[int | None, float]:
        b64 = base64.b64encode(jpeg).decode()
        for attempt in range(4):
            r = self._request(b64)
            if r.status_code in (429, 500, 502, 503, 529):
                time.sleep(min(2 ** attempt, 20))
                continue
            if r.status_code in (401, 403):
                raise VlmError("the API key was refused")
            if r.status_code >= 400:
                raise VlmError(f"{self.name} returned {r.status_code}: {r.text[:200]}")
            return parse_reply(self._text(r.json()))
        raise VlmError(f"{self.name} kept rate-limiting; try again later")

    def _request(self, b64): raise NotImplementedError
    def _text(self, data): raise NotImplementedError


class Anthropic(Provider):
    name = "anthropic"

    def _request(self, b64):
        return self.client.post("https://api.anthropic.com/v1/messages", headers={
            "x-api-key": self.key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json={"model": self.model, "max_tokens": 60, "messages": [{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": b64}},
                {"type": "text", "text": PROMPT}]}]})

    def _text(self, data):
        return "".join(b.get("text", "") for b in data.get("content", []))


class OpenAI(Provider):
    name = "openai"

    def _request(self, b64):
        return self.client.post("https://api.openai.com/v1/chat/completions", headers={"Authorization": f"Bearer {self.key}"},
                                json={"model": self.model, "max_tokens": 60, "response_format": {"type": "json_object"},
                                      "messages": [{"role": "user", "content": [
                                          {"type": "text", "text": PROMPT},
                                          {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}", "detail": "low"}}]}]})

    def _text(self, data):
        return data["choices"][0]["message"]["content"] or ""


class Gemini(Provider):
    name = "gemini"

    def _request(self, b64):
        return self.client.post(f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent",
                                headers={"x-goog-api-key": self.key},
                                json={"contents": [{"parts": [{"text": PROMPT}, {"inline_data": {"mime_type": "image/jpeg", "data": b64}}]}],
                                      "generationConfig": {"maxOutputTokens": 60, "responseMimeType": "application/json"}})

    def _text(self, data):
        parts = data["candidates"][0]["content"]["parts"]
        return "".join(p.get("text", "") for p in parts)


PROVIDERS = {"anthropic": Anthropic, "openai": OpenAI, "gemini": Gemini}


def make(name: str, api_key: str, model: str | None = None, client=None) -> Provider:
    if name not in PROVIDERS:
        raise VlmError(f"unknown provider {name!r}")
    return PROVIDERS[name](api_key, model, client)
