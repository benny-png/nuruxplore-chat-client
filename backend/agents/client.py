"""DeepSeek LLM client (OpenAI-compatible, DeepInfra).

Wraps a single chat-completion call and reports usage + wall time so the
orchestrator can surface ``tokens_in / tokens_out / cost_est / duration_s`` per
run — that is exactly the tokens/time the user asked us to keep an eye on.

Uses ``requests`` (already a dependency) against DeepInfra's OpenAI-compatible
endpoint, so we don't pull in the full OpenAI SDK. Cost estimate is a rough
per-1M-token price for the DeepSeek flash model; it is informational only.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import requests

from . import config

# Rough per-1M-token pricing (USD) for the DeepSeek flash model. Informational.
PRICE_PER_1M_IN = 0.15
PRICE_PER_1M_OUT = 0.6


@dataclass
class Call:
    """One LLM call's result plus its token/time accounting."""

    text: str
    model: str
    in_tokens: int = 0
    out_tokens: int = 0
    duration_s: float = 0.0

    @property
    def cost_est(self) -> float:
        dollars = PRICE_PER_1M_IN * self.in_tokens / 1_000_000 + PRICE_PER_1M_OUT * self.out_tokens / 1_000_000
        return round(dollars, 6)


class LLMClient:
    """Thin OpenAI-compatible client for DeepInfra chat completions."""

    def __init__(self, api_key: str | None = None, base_url: str | None = None, model: str | None = None):
        self.api_key = api_key or config.api_key()
        self.base_url = (base_url or config.base_url()).rstrip("/")
        self.model = model or config.model()

    def complete(
        self,
        system: str,
        user: str,
        *,
        temperature: float = 0.4,
        max_tokens: int = 2048,
        model: str | None = None,
    ) -> Call:
        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload: dict[str, Any] = {
            "model": model or self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        # Retry transient failures (network/timeout, 5xx, 429, provider overload).
        # Generation calls are slow, so a single attempt can exceed the timeout
        # under concurrency; a short backoff retry makes the graph robust.
        start = time.monotonic()
        attempts = 1 + config.retries()
        resp = None
        for attempt in range(attempts):
            try:
                resp = requests.post(url, headers=headers, json=payload, timeout=config.timeout())
            except requests.exceptions.RequestException as exc:
                if attempt < attempts - 1:
                    time.sleep(2.0 * (attempt + 1))
                    continue
                raise AgentsError(f"DeepInfra request failed: {exc}") from exc
            if resp.status_code == 200:
                break
            if resp.status_code in (429, 500, 502, 503, 504) and attempt < attempts - 1:
                time.sleep(2.0 * (attempt + 1))
                continue
            raise AgentsError(f"DeepInfra returned HTTP {resp.status_code}: {resp.text[:300]}")
        duration = time.monotonic() - start
        if resp is None:
            raise AgentsError("DeepInfra request failed: no response")

        body = resp.json()
        usage = body.get("usage") or {}
        text = ""
        try:
            text = body["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError):
            pass
        return Call(
            text=text.strip(),
            model=payload["model"],
            in_tokens=int(usage.get("prompt_tokens", 0)),
            out_tokens=int(usage.get("completion_tokens", 0)),
            duration_s=round(duration, 2),
        )


class AgentsError(RuntimeError):
    """Raised when the agent LLM layer fails (key missing, HTTP error, timeout)."""
