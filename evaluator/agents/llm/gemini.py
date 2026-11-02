"""
Gemini (OpenAI-compatible) LLM client.

Some OpenAI-compatible endpoints (including certain Gemini proxy/providers) do NOT
accept role="developer" messages and require role="system" for system instructions.

This client mirrors `agents/llm/openai.py` but differs in `_to_openai_messages()`
by using role="system" for the system prompt.
"""

import json
import os
from typing import Any, Dict, List, Optional, Tuple

import openai
from openai import AsyncOpenAI

from tenacity import RetryCallState, retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from agents.config import OpenAIProviderConfig, GeminiProviderConfig, AgentConfig
from agents.tools.tool_registry import normalize_tool_name, validate_tool_input
from agents.utils.exceptions import InvalidLLMResponseError, InvalidToolInputError, InvalidToolJSONError
from utils.logger import Logger


class LLMGeminiOpenAICompatible:
    """
    Gemini-backed LLM client that conforms to this repo's internal message/tool schema.

    New architecture:
    - provider_config: Connection info (credentials, endpoints, retry settings)
    - agent_config: Runtime parameters (max_tokens, temperature, thinking, etc.)
    - model_id: Actual model identifier for API calls

    Key difference from `LLMOpenAI`:
    - system prompt is emitted as role='system' (not 'developer')
    """

    def __init__(
        self, 
        provider_config: OpenAIProviderConfig | GeminiProviderConfig,
        agent_config: AgentConfig,
        model_id: str
    ):
        if AsyncOpenAI is None:
            raise ModuleNotFoundError(
                "Missing dependency 'openai'. Install it (e.g., `pip install openai`) to use OpenAI-compatible providers."
            )
        self.provider_config = provider_config
        self.agent_config = agent_config
        self.model_id = model_id
        
        api_key = (
            provider_config.api_key.get_secret_value()
            if provider_config.api_key is not None
            else os.getenv("OPENAI_API_KEY", "")
        ).strip()
        if not api_key:
            raise ValueError("Missing OpenAI API key. Set OPENAI_API_KEY or provide it in config.yaml.")

        client_kwargs: Dict[str, Any] = {"api_key": api_key}
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
        # NOTE: use_cache is accepted for interface parity but not used.
        # Use agent_config values as defaults, allow overrides
        _enable_thinking = enable_thinking if enable_thinking is not None else self.agent_config.enable_thinking
        _thinking_budget = thinking_budget_tokens if thinking_budget_tokens is not None else self.agent_config.thinking_budget_tokens

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
                max=30,
            ),
            retry=retry_if_exception_type(retryable_exceptions),
            before_sleep=before_sleep_log_retry_attempt(self.logger),
        )
        async def _call_llm() -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
            openai_messages = self._to_openai_messages(system_prompt=system_prompt, messages=messages)
            openai_tools = self._to_openai_tools(tools) if tools else None
            attempt_token_usages: List[Dict[str, Any]] = []
            attempt_raw_token_usages: List[Dict[str, Any]] = []

            params_base: Dict[str, Any] = {
                "model": self.model_id,
                "messages": openai_messages,
            }

            # NOTE: Many OpenAI-compatible Gemini gateways do NOT support OpenAI's
            # `reasoning_effort` parameter (not part of standard Chat Completions).
            # We rely on the gateway's default thinking behavior and only manage
            # thought_signature continuity via per-tool-call metadata (see below).

            if _enable_thinking:
                params_base["extra_body"] = {
                    'extra_body': {
                        "google": {
                            "thinking_config": {
                                "thinking_level": "high",
                                "include_thoughts": True
                            }
                        }
                    }
                }
            else:
                params_base["extra_body"] = {
                    'extra_body': {
                        "google": {
                            "thinking_config": {
                                "thinking_level": "low",
                                "include_thoughts": True
                            }
                        }
                    }
                }

            if (not _enable_thinking) and self.agent_config.temperature is not None:
                params_base["temperature"] = self.agent_config.temperature
            if openai_tools:
                params_base["tools"] = openai_tools
                params_base["tool_choice"] = "auto"

            # Prefer the widely supported parameter name for OpenAI-compatible gateways.
            params_base["max_tokens"] = self.agent_config.max_tokens

            response = await self.client.chat.completions.create(**params_base)

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

                # Normalize OpenAI-compatible → Claude convention
                # OpenAI: prompt_tokens INCLUDES cached_tokens
                # Claude: input_tokens EXCLUDES cache tokens
                cached = 0
                details = raw_usage.get("prompt_tokens_details")
                if isinstance(details, dict):
                    cached = details.get("cached_tokens", 0) or 0

                prompt_tokens = raw_usage.get("prompt_tokens", 0) or 0
                completion_tokens = raw_usage.get("completion_tokens", 0) or 0

                normalized = {
                    "input_tokens": prompt_tokens - cached,
                    "output_tokens": completion_tokens,
                    "cache_creation_input_tokens": 0,
                    "cache_read_input_tokens": cached,
                }
                attempt_token_usages.append(normalized)

            if not response or not response.choices:
                raise InvalidLLMResponseError("[CALL LLM] OpenAI response is empty")

            msg = response.choices[0].message
            content_blocks: List[Dict[str, Any]] = []

            # Text content (Gemini proxies may return a string OR a list of parts)
            text = self._extract_message_text(msg)
            if text:
                content_blocks.append({"type": "text", "text": text})

            # Persist Gemini thought signature (for next-turn compatibility in thinking mode)
            signature = self._extract_google_thought_signature_from_message(msg)
            thinking_blocks: List[Dict[str, Any]] = []
            if signature:
                # We store the signature as an internal "thinking" block so it is persisted
                # in conversation history but not sent as user-visible text.
                thinking_blocks.append({"type": "thinking", "signature": signature})

            tool_calls = getattr(msg, "tool_calls", None) or []
            for tc in tool_calls:
                if not tc or not tc.id or not tc.function or not tc.function.name:
                    raise InvalidLLMResponseError(f"[CALL LLM] Invalid tool call in OpenAI response: {tc}")
                tool_name = normalize_tool_name(tc.function.name)
                raw_args = tc.function.arguments or "{}"
                try:
                    parsed_args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                except Exception as e:
                    raise InvalidToolJSONError(
                        f"[CALL LLM] Tool '{tool_name}' arguments are not valid JSON: {raw_args}"
                    ) from e
                if parsed_args is None:
                    parsed_args = {}
                if not isinstance(parsed_args, dict):
                    raise InvalidToolJSONError(
                        f"[CALL LLM] Tool '{tool_name}' arguments must decode to an object, got {type(parsed_args).__name__}"
                    )

                validate_tool_input(tool_name, parsed_args)

                # Gemini gateways may require thought signatures on function/tool calls.
                # In OpenAI-compatible responses, this may be present on each tool_call.
                extra = getattr(tc, "extra_content", None)
                extra_content: Dict[str, Any] | None = extra if isinstance(extra, dict) else None

                tool_use_block: Dict[str, Any] = {"type": "tool_use", "id": tc.id, "name": tool_name, "input": parsed_args}
                if extra_content:
                    tool_use_block["extra_content"] = extra_content
                content_blocks.append(tool_use_block)

            if not content_blocks:
                raise InvalidLLMResponseError("[CALL LLM] OpenAI response had no content and no tool calls")

            # If we have a signature, prepend as internal thinking blocks for persistence.
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

    def _extract_message_text(self, msg: Any) -> str:
        """
        Gemini OpenAI-compatible responses usually provide `message.content` as a string,
        but some proxies return a structured list. Normalize to plain text.
        """
        content = getattr(msg, "content", None)
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: List[str] = []
            for item in content:
                if isinstance(item, str):
                    parts.append(item)
                    continue
                if isinstance(item, dict):
                    # Common shapes: {"type":"text","text":...} or {"text":...}
                    if item.get("type") in ("text", "output_text"):
                        parts.append(item.get("text", "") or "")
                    elif "text" in item and isinstance(item.get("text"), str):
                        parts.append(item.get("text", "") or "")
                    else:
                        parts.append(json.dumps(item, ensure_ascii=False))
                    continue
                parts.append(str(item))
            return "\n".join([p for p in parts if p.strip()]).strip()
        if content is None:
            return ""
        return str(content)

    def _extract_google_thought_signature_from_message(self, msg: Any) -> str:
        """
        Gemini-3-pro-preview returns `extra_content={"google":{"thought_signature":...}}`.
        We persist this signature to keep thinking mode compatible across turns.
        """
        extra = getattr(msg, "extra_content", None)
        if not isinstance(extra, dict):
            return ""
        google = extra.get("google")
        if not isinstance(google, dict):
            return ""
        sig = google.get("thought_signature")
        return sig if isinstance(sig, str) and sig.strip() else ""

    def _extract_latest_google_thought_signature(self, messages: List[Dict[str, Any]]) -> str:
        """
        Find the most recent persisted Gemini thought signature in the message history.
        We store it as an assistant content block: {"type":"thinking","signature": "..."}.
        """
        for m in reversed(messages):
            if not isinstance(m, dict):
                continue
            if m.get("role") != "assistant":
                continue
            content = m.get("content")
            if not isinstance(content, list):
                continue
            for block in reversed(content):
                if not isinstance(block, dict):
                    continue
                if block.get("type") != "thinking":
                    continue
                sig = block.get("signature")
                if isinstance(sig, str) and sig.strip():
                    return sig
        return ""

    def _to_openai_tools(self, tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        converted: List[Dict[str, Any]] = []
        for t in tools:
            if not t:
                continue
            name = t.get("name")
            if not name:
                continue
            converted.append(
                {
                    "type": "function",
                    "function": {
                        "name": name,
                        "description": t.get("description", "") or "",
                        "parameters": t.get("input_schema", {"type": "object", "properties": {}}),
                    },
                }
            )
        return converted

    def _to_openai_messages(self, *, system_prompt: str, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        openai_messages: List[Dict[str, Any]] = []
        if system_prompt:
            # Gemini OpenAI-compatible endpoints may reject role='developer'
            openai_messages.append({"role": "system", "content": system_prompt})

        for m in messages:
            role = m.get("role")
            content = m.get("content")
            if role not in ("user", "assistant", "system", "developer"):
                role = "user"

            # Map internal 'developer' to 'system' for Gemini compatibility
            if role == "developer":
                role = "system"

            if isinstance(content, str):
                if content:
                    openai_messages.append({"role": role, "content": content})
                continue

            if not isinstance(content, list):
                openai_messages.append({"role": role, "content": str(content)})
                continue

            if role == "assistant":
                text_parts: List[str] = []
                tool_calls: List[Dict[str, Any]] = []
                current_signature: Optional[str] = None
                for block in content:
                    if not isinstance(block, dict):
                        text_parts.append(str(block))
                        continue
                    btype = block.get("type")
                    if btype == "text":
                        text_parts.append(block.get("text", "") or "")
                    elif btype == "tool_use":
                        canonical_name = normalize_tool_name(block.get("name", "") or "")
                        # If this tool_use carries Gemini/Google thought-signature metadata,
                        # propagate it onto the OpenAI-compatible tool_call object.
                        extra_content = block.get("extra_content")
                        tool_calls.append(
                            {
                                "id": block.get("id"),
                                "type": "function",
                                "function": {
                                    "name": canonical_name,
                                    "arguments": json.dumps(block.get("input", {}) or {}, ensure_ascii=False),
                                },
                                **(
                                    {"extra_content": extra_content}
                                    if isinstance(extra_content, dict) and extra_content
                                    else {}
                                ),
                            }
                        )
                    elif btype in ("thinking", "redacted_thinking"):
                        # Keep track of a persisted signature (if present) so we can
                        # attach it to tool calls when needed.
                        sig = block.get("signature")
                        if isinstance(sig, str) and sig.strip():
                            current_signature = sig.strip()
                        continue
                    else:
                        text_parts.append(json.dumps(block, ensure_ascii=False))

                assistant_msg: Dict[str, Any] = {"role": "assistant"}
                assistant_content = "\n".join([t for t in text_parts if t.strip()]).strip()
                if assistant_content:
                    assistant_msg["content"] = assistant_content
                if tool_calls:
                    assistant_msg["tool_calls"] = tool_calls
                if "content" in assistant_msg or "tool_calls" in assistant_msg:
                    openai_messages.append(assistant_msg)
                continue

            pending_text_parts: List[str] = []
            for block in content:
                if not isinstance(block, dict):
                    pending_text_parts.append(str(block))
                    continue

                btype = block.get("type")
                if btype == "text":
                    pending_text_parts.append(block.get("text", "") or "")
                    continue

                if btype == "tool_result":
                    user_text = "\n".join([t for t in pending_text_parts if t.strip()]).strip()
                    if user_text:
                        openai_messages.append({"role": role, "content": user_text})
                    pending_text_parts = []

                    tool_use_id = block.get("tool_use_id")
                    tool_content = self._flatten_tool_result_content(block.get("content", ""))
                    if tool_use_id:
                        openai_messages.append({"role": "tool", "tool_call_id": tool_use_id, "content": tool_content})
                    else:
                        openai_messages.append({"role": role, "content": tool_content})
                    continue

                pending_text_parts.append(json.dumps(block, ensure_ascii=False))

            user_text = "\n".join([t for t in pending_text_parts if t.strip()]).strip()
            if user_text:
                openai_messages.append({"role": role, "content": user_text})

        return openai_messages

    def _flatten_tool_result_content(self, content: Any) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: List[str] = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    parts.append(item.get("text", "") or "")
                else:
                    parts.append(json.dumps(item, ensure_ascii=False))
            return "\n".join([p for p in parts if p.strip()]).strip()
        if isinstance(content, dict):
            return json.dumps(content, ensure_ascii=False, indent=2)
        return str(content)


def before_sleep_log_retry_attempt(logger: Logger):
    def before_sleep(retry_state: RetryCallState):
        exc: BaseException | None = retry_state.outcome.exception() if retry_state.outcome else None
        if exc is None:
            logger.info("Retrying after unknown error (no exception available)")
            return None
        logger.info(f"Retrying after {exc.__class__.__name__}: {exc}")
        return None

    return before_sleep
