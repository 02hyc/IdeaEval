"""Lightweight Anthropic API LLM client for paper2proposal.

Supports extended thinking and streaming via the standard Anthropic SDK.
Self-contained; no dependency on the rest of the benchmark package.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import anthropic
import yaml
from anthropic import AsyncAnthropic

logger = logging.getLogger(__name__)


@dataclass
class LLMConfig:
    """Flat config parsed from config.yaml."""

    model_id: str
    api_key: str
    base_url: str
    # Only needed when routing through a Bedrock-backed proxy; omitted otherwise.
    anthropic_version: str | None = None
    max_tokens: int = 64000
    temperature: float = 0.0
    enable_thinking: bool = True
    thinking_budget_tokens: int = 30000
    num_retries: int = 3
    retry_multiplier: float = 1.0
    retry_start_wait: float = 1.0


def load_llm_config(config_path: str | Path) -> LLMConfig:
    """Load LLM config from YAML, resolving env var placeholders."""
    data = yaml.safe_load(Path(config_path).read_text(encoding="utf-8"))

    models = data.get("models", {})
    agents = data.get("agents", {})
    agent_cfg = agents.get("converter", {})
    model_alias = agent_cfg.get("model", "")
    model_cfg = models.get(model_alias, {})

    def _resolve(val: str) -> str:
        if isinstance(val, str) and val.startswith("${") and val.endswith("}"):
            env_name = val[2:-1]
            return os.environ.get(env_name, "")
        return val

    return LLMConfig(
        model_id=model_cfg.get("model_id", ""),
        api_key=_resolve(model_cfg.get("api_key", "")),
        base_url=_resolve(model_cfg.get("base_url", "")),
        anthropic_version=model_cfg.get("anthropic_version"),
        max_tokens=agent_cfg.get("max_tokens", 64000),
        temperature=agent_cfg.get("temperature", 0.0),
        enable_thinking=agent_cfg.get("enable_thinking", True),
        thinking_budget_tokens=agent_cfg.get("thinking_budget_tokens", 30000),
        num_retries=model_cfg.get("num_retries", 3),
        retry_multiplier=model_cfg.get("retry_multiplier", 1.0),
        retry_start_wait=model_cfg.get("retry_start_wait", 1.0),
    )


class LLMClient:
    """Async Anthropic API client with retry and extended thinking support."""

    def __init__(self, config: LLMConfig):
        self.config = config
        self.client = AsyncAnthropic(
            api_key=config.api_key,
            base_url=config.base_url or None,
        )

    async def call(
        self,
        messages: list[dict[str, Any]],
        system_prompt: str,
    ) -> dict[str, Any]:
        """Single LLM call. Returns dict with 'content' list of blocks."""
        cfg = self.config

        request_params: dict[str, Any] = {
            "system": [{"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral"}}],
            "model": cfg.model_id,
            "messages": messages,
            "max_tokens": cfg.max_tokens,
        }

        if cfg.enable_thinking:
            request_params["thinking"] = {
                "type": "enabled",
                "budget_tokens": cfg.thinking_budget_tokens,
            }
        else:
            request_params["temperature"] = cfg.temperature

        if cfg.anthropic_version:
            request_params["extra_body"] = {"anthropic_version": cfg.anthropic_version}

        last_exc: Exception | None = None
        for attempt in range(cfg.num_retries):
            try:
                return await self._stream_call(request_params)
            except (
                anthropic.RateLimitError,
                anthropic.APITimeoutError,
                anthropic.APIConnectionError,
                anthropic.InternalServerError,
            ) as exc:
                last_exc = exc
                if attempt >= cfg.num_retries - 1:
                    raise
                import asyncio
                delay = cfg.retry_start_wait * (cfg.retry_multiplier ** attempt)
                logger.warning(
                    "Attempt %d/%d failed (%s), retrying in %.1fs",
                    attempt + 1, cfg.num_retries, type(exc).__name__, delay,
                )
                await asyncio.sleep(delay)

        raise last_exc  # type: ignore[misc]

    async def _stream_call(self, request_params: dict[str, Any]) -> dict[str, Any]:
        content: list[dict[str, Any]] = []

        async with self.client.messages.stream(**request_params) as stream:
            async for _ in stream:
                pass
            response = await stream.get_final_message()

        if not response or not response.content:
            raise ValueError("LLM returned empty response")

        for block in response.content:
            if block.type == "text":
                content.append({"type": "text", "text": block.text})
            elif block.type == "thinking":
                pass  # discard thinking blocks for output

        return {"content": content}
