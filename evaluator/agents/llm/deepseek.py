"""
DeepSeek LLM client (Chat Completions API).

DeepSeek's public API (https://api.deepseek.com) speaks the OpenAI-style
**Chat Completions** API, not the newer Responses API. Their thinking mode is
toggled via `extra_body={"thinking": {"type": "enabled"|"disabled"}}` and the
reasoning chain is returned in `message.reasoning_content` (alongside the
normal `message.content`). See:
    https://api-docs.deepseek.com/zh-cn/guides/thinking_mode
    https://api-docs.deepseek.com/zh-cn/guides/tool_calls

This client converts the repo's internal Claude-style message schema to/from
Chat Completions and preserves the reasoning chain as a `thinking` block in
persisted history (not re-sent to the API; DeepSeek does not require it).
"""

import json
from typing import Any, Dict, List, Optional, Tuple

import openai
from openai import AsyncOpenAI

from tenacity import (
    RetryCallState,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from agents.config import AgentConfig, DeepSeekProviderConfig
from agents.tools.tool_registry import normalize_tool_name, validate_tool_input
from agents.utils.exceptions import (
    InvalidLLMResponseError,
    InvalidToolInputError,
    InvalidToolJSONError,
)
from utils.logger import Logger


class LLMDeepSeek:
    """DeepSeek-backed LLM client using Chat Completions + DeepSeek thinking mode."""

    def __init__(
        self,
        provider_config: DeepSeekProviderConfig,
        agent_config: AgentConfig,
        model_id: str,
    ):
        if AsyncOpenAI is None:
            raise ModuleNotFoundError(
                "Missing dependency 'openai'. Install it (e.g., `pip install openai`)."
            )
        self.provider_config = provider_config
        self.agent_config = agent_config
        self.model_id = model_id

        api_key = (
            provider_config.api_key.get_secret_value()
            if provider_config.api_key is not None
            else ""
        ).strip()
        if not api_key:
            raise ValueError(
                "Missing DeepSeek API key. Set SILICONFLOW_API_KEY / DEEPSEEK_API_KEY "
                "or provide it in config.yaml."
            )

        client_kwargs: Dict[str, Any] = {"api_key": api_key, "timeout": 300.0}
        if provider_config.base_url:
            client_kwargs["base_url"] = provider_config.base_url
        self.client = AsyncOpenAI(**client_kwargs)
        self.logger = Logger()

    async def call_llm(
        self,
        messages: List[Dict[str, Any]],
        system_prompt: str,
        tools: Optional[List[Dict[str, Any]]],
        use_cache: bool = True,
        enable_thinking: Optional[bool] = None,
        thinking_budget_tokens: Optional[int] = None,
    ) -> Dict[str, Any]:
        # NOTE: use_cache accepted for interface parity but not used.
        _enable_thinking = (
            enable_thinking
            if enable_thinking is not None
            else self.agent_config.enable_thinking
        )
        _thinking_budget = (
            thinking_budget_tokens
            if thinking_budget_tokens is not None
            else self.agent_config.thinking_budget_tokens
        )

        retryable_exceptions: Tuple[type[BaseException], ...] = (
            InvalidLLMResponseError,
            InvalidToolJSONError,
            InvalidToolInputError,
            ValueError,
        )
        if openai is not None:
            retryable_exceptions = (
                openai.RateLimitError,
                openai.APITimeoutError,
                openai.APIConnectionError,
                openai.InternalServerError,
                *retryable_exceptions,
            )

        @retry(
            stop=stop_after_attempt(self.provider_config.num_retries),
            wait=wait_exponential(
                multiplier=self.provider_config.retry_multiplier,
                min=self.provider_config.retry_start_wait,
                max=60,
            ),
            retry=retry_if_exception_type(retryable_exceptions),
            before_sleep=_before_sleep_log_retry(self.logger),
        )
        async def _call_llm() -> Tuple[
            List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]
        ]:
            openai_messages = self._to_chat_messages(
                system_prompt=system_prompt, messages=messages
            )
            openai_tools = self._to_chat_tools(tools) if tools else None

            params: Dict[str, Any] = {
                "model": self.model_id,
                "messages": openai_messages,
                "max_tokens": self.agent_config.max_tokens,
            }

            # DeepSeek thinking mode: extra_body={"thinking": {"type":"enabled"|"disabled"}}
            params["extra_body"] = {
                "thinking": {"type": "enabled" if _enable_thinking else "disabled"}
            }
            # reasoning_effort: use explicit config if set, otherwise map from budget
            if _enable_thinking:
                if self.agent_config.reasoning_effort:
                    effort = self.agent_config.reasoning_effort
                elif _thinking_budget >= 10000:
                    effort = "high"
                elif _thinking_budget >= 4000:
                    effort = "medium"
                else:
                    effort = "low"
                params["reasoning_effort"] = effort

            # Temperature only applies when thinking is disabled (per DeepSeek behavior).
            if (not _enable_thinking) and self.agent_config.temperature is not None:
                params["temperature"] = self.agent_config.temperature

            if openai_tools:
                params["tools"] = openai_tools
                params["tool_choice"] = "auto"

            attempt_token_usages: List[Dict[str, Any]] = []
            attempt_raw_token_usages: List[Dict[str, Any]] = []

            response = await self.client.chat.completions.create(**params)

            if response and response.usage:
                raw_usage: Dict[str, Any] = {}
                try:
                    dumped = response.usage.model_dump()
                    if isinstance(dumped, dict):
                        raw_usage = dumped
                except Exception:
                    try:
                        dumped = dict(response.usage)  # type: ignore[arg-type]
                        if isinstance(dumped, dict):
                            raw_usage = dumped
                    except Exception:
                        raw_usage = {}

                if raw_usage:
                    attempt_raw_token_usages.append(raw_usage)

                # Normalize Chat-Completions usage → Claude convention.
                # DeepSeek returns prompt_cache_hit_tokens / prompt_cache_miss_tokens
                # at the top level; also prompt_tokens_details.cached_tokens.
                cached = 0
                details = raw_usage.get("prompt_tokens_details")
                if isinstance(details, dict):
                    cached = details.get("cached_tokens", 0) or 0
                if not cached:
                    cached = raw_usage.get("prompt_cache_hit_tokens", 0) or 0

                prompt_tokens = raw_usage.get("prompt_tokens", 0) or 0
                completion_tokens = raw_usage.get("completion_tokens", 0) or 0

                attempt_token_usages.append({
                    "input_tokens": max(prompt_tokens - cached, 0),
                    "output_tokens": completion_tokens,
                    "cache_creation_input_tokens": 0,
                    "cache_read_input_tokens": cached,
                })

            if not response or not response.choices:
                raise InvalidLLMResponseError("[CALL LLM] DeepSeek response is empty")

            msg = response.choices[0].message
            content_blocks: List[Dict[str, Any]] = []
            thinking_blocks: List[Dict[str, Any]] = []

            # Reasoning content (optional, only present in thinking mode).
            reasoning = getattr(msg, "reasoning_content", None) or ""
            if isinstance(reasoning, str) and reasoning.strip():
                thinking_blocks.append({"type": "thinking", "text": reasoning})

            # Final answer text.
            text = self._extract_message_text(msg)
            if text:
                content_blocks.append({"type": "text", "text": text})

            # Tool calls, if any.
            tool_calls = getattr(msg, "tool_calls", None) or []
            for tc in tool_calls:
                if not tc or not tc.id or not tc.function or not tc.function.name:
                    raise InvalidLLMResponseError(
                        f"[CALL LLM] Invalid tool call in DeepSeek response: {tc}"
                    )
                tool_name = normalize_tool_name(tc.function.name)
                raw_args = tc.function.arguments or "{}"
                try:
                    parsed_args = (
                        json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                    )
                except Exception as e:
                    raise InvalidToolJSONError(
                        f"[CALL LLM] Tool '{tool_name}' arguments are not valid JSON: {raw_args}"
                    ) from e
                if parsed_args is None:
                    parsed_args = {}
                if not isinstance(parsed_args, dict):
                    raise InvalidToolJSONError(
                        f"[CALL LLM] Tool '{tool_name}' arguments must decode to an object, "
                        f"got {type(parsed_args).__name__}"
                    )
                validate_tool_input(tool_name, parsed_args)
                content_blocks.append({
                    "type": "tool_use",
                    "id": tc.id,
                    "name": tool_name,
                    "input": parsed_args,
                })

            if not content_blocks:
                raise InvalidLLMResponseError(
                    "[CALL LLM] DeepSeek response had no content and no tool calls"
                )

            return (
                thinking_blocks + content_blocks,
                attempt_token_usages,
                attempt_raw_token_usages,
            )

        llm_content, token_usages, raw_token_usages = await _call_llm()
        result: Dict[str, Any] = {"content": llm_content, "token_usages": token_usages}
        if raw_token_usages:
            result["raw_token_usages"] = raw_token_usages
        return result

    # ------------------------------------------------------------------
    # Schema conversion
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_message_text(msg: Any) -> str:
        content = getattr(msg, "content", None)
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: List[str] = []
            for item in content:
                if isinstance(item, dict):
                    if item.get("type") in ("text", "output_text"):
                        parts.append(item.get("text", "") or "")
                    elif "text" in item and isinstance(item.get("text"), str):
                        parts.append(item.get("text", "") or "")
                    else:
                        parts.append(json.dumps(item, ensure_ascii=False))
                else:
                    parts.append(str(item))
            return "\n".join(p for p in parts if p.strip()).strip()
        if content is None:
            return ""
        return str(content)

    @staticmethod
    def _to_chat_tools(tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        converted: List[Dict[str, Any]] = []
        for t in tools:
            if not t:
                continue
            name = t.get("name")
            if not name:
                continue
            converted.append({
                "type": "function",
                "function": {
                    "name": name,
                    "description": t.get("description", "") or "",
                    "parameters": t.get("input_schema", {"type": "object", "properties": {}}),
                },
            })
        return converted

    def _to_chat_messages(
        self,
        *,
        system_prompt: str,
        messages: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Convert internal Claude-style messages to Chat Completions format."""
        out: List[Dict[str, Any]] = []
        if system_prompt:
            out.append({"role": "system", "content": system_prompt})

        for m in messages:
            role = m.get("role", "user")
            content = m.get("content")
            if role not in ("user", "assistant", "system"):
                role = "user"

            if isinstance(content, str):
                if content.strip():
                    out.append({"role": role, "content": content})
                continue

            if not isinstance(content, list):
                text = str(content)
                if text.strip():
                    out.append({"role": role, "content": text})
                continue

            if role == "assistant":
                text_parts: List[str] = []
                tool_calls: List[Dict[str, Any]] = []
                for block in content:
                    if not isinstance(block, dict):
                        text_parts.append(str(block))
                        continue
                    btype = block.get("type")
                    if btype == "text":
                        text_parts.append(block.get("text", "") or "")
                    elif btype == "tool_use":
                        tool_calls.append({
                            "id": block.get("id"),
                            "type": "function",
                            "function": {
                                "name": normalize_tool_name(block.get("name", "") or ""),
                                "arguments": json.dumps(
                                    block.get("input", {}) or {}, ensure_ascii=False
                                ),
                            },
                        })
                    elif btype in ("thinking", "redacted_thinking", "reasoning"):
                        # Persisted only; do not re-send DeepSeek reasoning content.
                        continue
                    else:
                        text_parts.append(json.dumps(block, ensure_ascii=False))

                assistant_msg: Dict[str, Any] = {"role": "assistant"}
                joined = "\n".join(t for t in text_parts if t.strip()).strip()
                if joined:
                    assistant_msg["content"] = joined
                if tool_calls:
                    assistant_msg["tool_calls"] = tool_calls
                if "content" in assistant_msg or "tool_calls" in assistant_msg:
                    out.append(assistant_msg)
                continue

            # user (or unknown → treat as user)
            pending_text: List[str] = []
            for block in content:
                if not isinstance(block, dict):
                    pending_text.append(str(block))
                    continue
                btype = block.get("type")
                if btype == "text":
                    pending_text.append(block.get("text", "") or "")
                    continue
                if btype == "tool_result":
                    flushed = "\n".join(t for t in pending_text if t.strip()).strip()
                    if flushed:
                        out.append({"role": role, "content": flushed})
                    pending_text = []
                    tool_use_id = block.get("tool_use_id")
                    tool_content = self._flatten_tool_result_content(block.get("content", ""))
                    if tool_use_id:
                        out.append({
                            "role": "tool",
                            "tool_call_id": tool_use_id,
                            "content": tool_content,
                        })
                    else:
                        out.append({"role": role, "content": tool_content})
                    continue
                pending_text.append(json.dumps(block, ensure_ascii=False))

            flushed = "\n".join(t for t in pending_text if t.strip()).strip()
            if flushed:
                out.append({"role": role, "content": flushed})

        return out

    @staticmethod
    def _flatten_tool_result_content(content: Any) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: List[str] = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    parts.append(item.get("text", "") or "")
                else:
                    parts.append(json.dumps(item, ensure_ascii=False))
            return "\n".join(p for p in parts if p.strip()).strip()
        if isinstance(content, dict):
            return json.dumps(content, ensure_ascii=False, indent=2)
        return str(content)


def _before_sleep_log_retry(logger: Logger):
    def before_sleep(retry_state: RetryCallState):
        exc = retry_state.outcome.exception() if retry_state.outcome else None
        if exc is None:
            logger.info(f"[DeepSeek RETRY] Attempt {retry_state.attempt_number} failed (no exception)")
            return None
        logger.info(
            f"[DeepSeek RETRY] Attempt {retry_state.attempt_number} failed: "
            f"{exc.__class__.__name__}: {str(exc)[:300]}"
        )
        return None

    return before_sleep
