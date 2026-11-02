"""Multi-provider LLM client supporting OpenAI-compatible and Anthropic APIs."""

from __future__ import annotations

import logging
import os
import time
from typing import Optional

from .config import LLMSettings

log = logging.getLogger(__name__)


class LLMClient:
    """LLM client with automatic retry and exponential backoff."""

    def __init__(self, settings: LLMSettings):
        self.settings = settings
        self._client = None

        if settings.provider == "anthropic":
            import anthropic
            self._client = anthropic.Anthropic(
                api_key=settings.api_key or os.environ.get("ANTHROPIC_API_KEY"),
            )
        elif settings.provider == "openai":
            from openai import OpenAI
            import httpx

            max_connections = int(os.environ.get("OPENAI_MAX_CONNECTIONS", "512"))
            max_keepalive = int(os.environ.get("OPENAI_MAX_KEEPALIVE_CONNECTIONS", "256"))
            timeout_seconds = float(os.environ.get("OPENAI_TIMEOUT_SECONDS", "600"))
            self._client = OpenAI(
                api_key=settings.api_key or os.environ.get("OPENAI_API_KEY"),
                base_url=settings.base_url or None,
                max_retries=0,
                timeout=timeout_seconds,
                http_client=httpx.Client(
                    limits=httpx.Limits(
                        max_connections=max_connections,
                        max_keepalive_connections=max_keepalive,
                    ),
                    timeout=timeout_seconds,
                ),
            )
        else:
            raise ValueError(f"Unknown provider: {settings.provider}")

    def generate(
        self,
        system: str,
        user: str,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
    ) -> str:
        if max_tokens is None:
            max_tokens = self.settings.max_tokens
        if temperature is None:
            temperature = self.settings.temperature

        for attempt in range(self.settings.max_retries):
            try:
                if self.settings.provider == "anthropic":
                    return self._call_anthropic(system, user, max_tokens, temperature)
                else:
                    return self._call_openai(system, user, max_tokens, temperature)
            except Exception as e:
                log.warning("LLM call attempt %d failed: %s", attempt + 1, e)
                if attempt < self.settings.max_retries - 1:
                    wait = min(2 ** (attempt + 1), 60)
                    log.info("Retrying in %ds...", wait)
                    time.sleep(wait)
                else:
                    raise

    def _call_anthropic(self, system: str, user: str, max_tokens: int, temperature: float) -> str:
        kwargs = dict(
            model=self.settings.model,
            max_tokens=max_tokens,
            temperature=temperature,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        if temperature <= 0:
            kwargs.pop("temperature")

        response = self._client.messages.create(**kwargs)
        text_parts = [block.text for block in response.content if hasattr(block, "text")]
        result = "\n".join(text_parts)

        log.info("LLM response: %d chars, usage=in=%d out=%d",
                 len(result), response.usage.input_tokens, response.usage.output_tokens)
        return result

    def _call_openai(self, system: str, user: str, max_tokens: int, temperature: float) -> str:
        response = self._client.chat.completions.create(
            model=self.settings.model,
            max_tokens=max_tokens,
            temperature=temperature,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        result = response.choices[0].message.content or ""
        usage = getattr(response, "usage", None)
        if usage:
            log.info("LLM response: %d chars, usage=in=%s out=%s",
                     len(result),
                     getattr(usage, "prompt_tokens", None),
                     getattr(usage, "completion_tokens", None))
        return result
