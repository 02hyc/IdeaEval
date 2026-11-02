"""Self-contained LLM client with rate limiting for TaxoAdapt."""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from typing import Optional

import httpx
from openai import OpenAI

log = logging.getLogger(__name__)

MODEL = os.environ.get("LLM_MODEL", "deepseek-v4-pro")
RPM_DEFAULT = int(os.environ.get("LLM_RPM", "18000"))


class RateLimiter:
    """RPM-only rate limiter. TPM is handled by API-side 429 + retry."""

    def __init__(self, rpm: int = 18_000):
        self._rpm = rpm
        self._lock = threading.Lock()
        self._request_times: list[float] = []

    def acquire(self):
        while True:
            with self._lock:
                now = time.monotonic()
                cutoff = now - 60.0
                self._request_times = [t for t in self._request_times if t > cutoff]

                if len(self._request_times) < self._rpm:
                    self._request_times.append(now)
                    return

            time.sleep(0.05)


class LLMClient:
    """OpenAI-compatible LLM client with rate limiting and retry."""

    def __init__(self, rate_limiter: Optional[RateLimiter] = None,
                 api_key: Optional[str] = None,
                 base_url: Optional[str] = None,
                 model: Optional[str] = None):
        api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        base_url = base_url or os.environ.get("OPENAI_BASE_URL", "")
        self._model = model or MODEL

        max_conn = int(os.environ.get("OPENAI_MAX_CONNECTIONS", "512"))
        max_keepalive = int(os.environ.get("OPENAI_MAX_KEEPALIVE_CONNECTIONS", "256"))
        timeout = float(os.environ.get("OPENAI_TIMEOUT_SECONDS", "600"))

        self._client = OpenAI(
            api_key=api_key,
            base_url=base_url or None,
            max_retries=0,
            timeout=timeout,
            http_client=httpx.Client(
                limits=httpx.Limits(
                    max_connections=max_conn,
                    max_keepalive_connections=max_keepalive,
                ),
                timeout=timeout,
            ),
        )
        self._rate_limiter = rate_limiter
        self._max_retries = 5

    def generate(self, system: str, user: str,
                 max_tokens: int = 65536, temperature: float = 0.1,
                 thinking: Optional[str] = None) -> str:
        """Generate LLM response.

        Args:
            thinking: Per-call override — "enabled", "disabled", or None (use env default).
        """
        if self._rate_limiter:
            self._rate_limiter.acquire()

        env_thinking = os.environ.get("LLM_THINKING", "")
        env_effort = os.environ.get("LLM_REASONING_EFFORT", "")
        active_thinking = thinking if thinking is not None else env_thinking

        for attempt in range(self._max_retries):
            try:
                kwargs: dict = dict(
                    model=self._model,
                    max_tokens=max_tokens,
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                )
                extra_body: dict = {}
                if active_thinking in ("enabled", "disabled"):
                    extra_body["thinking"] = {"type": active_thinking}
                if active_thinking == "enabled" and env_effort:
                    extra_body["reasoning_effort"] = env_effort
                if extra_body:
                    kwargs["extra_body"] = extra_body
                resp = self._client.chat.completions.create(**kwargs)
                result = resp.choices[0].message.content or ""
                usage = getattr(resp, "usage", None)
                if usage:
                    reasoning_tokens = ""
                    ctd = getattr(usage, "completion_tokens_details", None)
                    if ctd:
                        reasoning_tokens = f" reason={getattr(ctd, 'reasoning_tokens', '?')}"
                    log.info("LLM: %d chars, in=%s out=%s%s finish=%s",
                             len(result),
                             getattr(usage, "prompt_tokens", "?"),
                             getattr(usage, "completion_tokens", "?"),
                             reasoning_tokens,
                             resp.choices[0].finish_reason)
                if not result.strip():
                    log.warning("LLM returned empty content! finish_reason=%s",
                                resp.choices[0].finish_reason)
                return result
            except Exception as e:
                err_str = str(e)
                is_fatal = "402" in err_str or "Insufficient Balance" in err_str
                if is_fatal:
                    log.error("Fatal API error (not retrying): %s", err_str[:200])
                    raise
                is_rate_limit = "429" in err_str or "rate" in err_str.lower()
                if attempt < self._max_retries - 1:
                    if is_rate_limit:
                        wait = 5 + attempt * 5
                    else:
                        wait = min(2 ** (attempt + 1), 30)
                    log.warning("LLM attempt %d/%d failed (%s): %s, retry in %ds",
                                attempt + 1, self._max_retries,
                                "rate-limit" if is_rate_limit else "error",
                                err_str[:200], wait)
                    time.sleep(wait)
                else:
                    raise


def parse_json(raw: str) -> Optional[dict | list]:
    """Extract JSON from LLM response."""
    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        lines = [l for l in lines if not l.strip().startswith("```")]
        text = "\n".join(lines).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    for i, ch in enumerate(text):
        if ch in ("{", "["):
            try:
                return json.loads(text[i:])
            except json.JSONDecodeError:
                continue
    return None


def llm_call(llm: LLMClient, system: str, user: str,
             max_tokens: int = 65536,
             thinking: Optional[str] = None) -> Optional[dict | list]:
    """LLM call with automatic JSON parsing."""
    raw = llm.generate(system=system, user=user, max_tokens=max_tokens,
                       thinking=thinking)
    return parse_json(raw)
