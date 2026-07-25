"""Minimal OpenAI-compatible chat client (stdlib urllib)."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any


def is_placeholder_api_key(key: str | None) -> bool:
    if key is None:
        return True
    k = key.strip()
    if not k:
        return True
    upper = k.upper()
    return upper.startswith("YOUR_") or upper in {"CHANGEME", "PLACEHOLDER", "XXX"}


def chat_completions_url(base_url: str) -> str:
    base = base_url.rstrip("/")
    if base.endswith("/chat/completions"):
        return base
    return f"{base}/chat/completions"


def chat_completion(
    *,
    api_key: str,
    base_url: str,
    model: str,
    messages: list[dict[str, str]],
    timeout_sec: float = 90.0,
    temperature: float = 0.2,
    json_response: bool = True,
) -> str:
    if is_placeholder_api_key(api_key):
        raise ValueError(
            "LLM API key is missing or still a placeholder. Set REPOSCOPE_LLM_API_KEY in .env"
        )
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
    }
    if json_response:
        payload["response_format"] = {"type": "json_object"}
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        chat_completions_url(base_url),
        data=data,
        headers={
            "Authorization": f"Bearer {api_key.strip()}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"LLM API HTTP {e.code}: {detail}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"LLM API request failed: {e}") from e

    try:
        return str(body["choices"][0]["message"]["content"])
    except (KeyError, IndexError, TypeError) as e:
        raise RuntimeError(f"Unexpected LLM response shape: {body!r}") from e
