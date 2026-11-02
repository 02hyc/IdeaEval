import asyncio
import json
import os
import re
from typing import Any, Dict, List, Optional, Tuple

import openai
from openai import AsyncOpenAI

from tenacity import RetryCallState, retry, retry_if_exception, stop_after_attempt, stop_never, wait_exponential

from agents.config import OpenAIProviderConfig, AgentConfig
from agents.tools.tool_registry import validate_tool_input
from agents.utils.exceptions import InvalidLLMResponseError, InvalidToolInputError, InvalidToolJSONError, ContentPolicyViolationError
from utils.logger import Logger

# Maximum length of error message to include in retry logs.
# Prevents dumping entire Cloudflare HTML pages (often 5000+ chars) into logs.
_MAX_ERROR_LOG_LENGTH = 500


class LLMRequestTimeoutError(TimeoutError):
    """Raised when one Responses API request exceeds the configured wall timeout."""


class LLMOpenAI:
    """
    OpenAI Responses-API-backed LLM client that conforms to this repo's
    internal message/tool schema.

    Uses client.responses.create() (the new Responses API) instead of
    client.chat.completions.create().

    Internal schema (Claude-style, unchanged):
    - messages: [{"role": "user"|"assistant", "content": <str|List[block]>}]
    - tool_use blocks: {"type":"tool_use","id":str,"name":str,"input":dict}
    - tool_result blocks: {"type":"tool_result","tool_use_id":str,"content":<str|List[block]>}

    Responses API mapping:
    - system prompt         → `instructions` parameter
    - tool_use block        → {"type":"function_call","call_id":id,"name":...,"arguments":json}
    - tool_result block     → {"type":"function_call_output","call_id":id,"output":str}
    - tools schema          → flat {"type":"function","name":...} (no nested "function" key)
    - token usage           → response.usage.input_tokens / output_tokens
    """

    def __init__(
        self,
        provider_config: OpenAIProviderConfig,
        agent_config: AgentConfig,
        model_id: str
    ):
        if AsyncOpenAI is None:
            raise ModuleNotFoundError(
                "Missing dependency 'openai'. Install it (e.g., `pip install openai`) "
                "to use OpenAI-compatible providers."
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
                "Missing API key. Set the appropriate env var or provide it in config.yaml."
            )

        client_kwargs: Dict[str, Any] = {"api_key": api_key, "timeout": 300.0}
        if provider_config.base_url:
            client_kwargs["base_url"] = provider_config.base_url
        self.client = AsyncOpenAI(**client_kwargs)
        self.logger = Logger()
        self._reasoning_effort_supported = True
        self._last_response_id: Optional[str] = None
        self._last_message_count: int = 0

    def _request_timeout_seconds(self) -> Optional[float]:
        raw = os.getenv("RANKER_LLM_CALL_TIMEOUT_SECONDS", "300").strip()
        if not raw:
            return None
        try:
            timeout = float(raw)
        except ValueError:
            return 300.0
        return timeout if timeout > 0 else None

    async def _responses_create(self, params: Dict[str, Any]):
        request = self.client.responses.create(**params)
        timeout = self._request_timeout_seconds()
        if timeout is None:
            return await request
        try:
            return await asyncio.wait_for(request, timeout=timeout)
        except asyncio.TimeoutError as exc:
            raise LLMRequestTimeoutError(
                f"Responses API request exceeded {timeout:g}s wall timeout"
            ) from exc

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
            enable_thinking if enable_thinking is not None
            else self.agent_config.enable_thinking
        )
        _thinking_budget = (
            thinking_budget_tokens if thinking_budget_tokens is not None
            else self.agent_config.thinking_budget_tokens
        )

        # Detect compaction / emergency drop: message list shrank since last call.
        if self._last_response_id is not None and len(messages) < self._last_message_count:
            self._last_response_id = None
            self._last_message_count = 0

        # --- Retryable exception classification ---
        server_retryable_exceptions: Tuple[type[BaseException], ...] = ()
        if openai is not None:
            server_retryable_exceptions = (
                openai.RateLimitError,
                openai.APITimeoutError,
                openai.APIConnectionError,
                openai.InternalServerError,
            )

        llm_retryable_exceptions: Tuple[type[BaseException], ...] = (
            InvalidLLMResponseError,
            InvalidToolJSONError,
            InvalidToolInputError,
            ValueError,
        )
        all_retryable_exceptions = server_retryable_exceptions + llm_retryable_exceptions
        bounded_retry_exceptions: Tuple[type[BaseException], ...] = (
            LLMRequestTimeoutError,
        )

        def _is_server_error(exc: BaseException) -> bool:
            if isinstance(exc, server_retryable_exceptions):
                return True
            if openai is not None and isinstance(exc, openai.APIStatusError):
                return exc.status_code in (408, 502, 503, 504)
            return False

        def is_retryable_error(exc: BaseException) -> bool:
            return (
                _is_server_error(exc)
                or isinstance(exc, all_retryable_exceptions)
                or isinstance(exc, bounded_retry_exceptions)
            )

        num_retries = self.provider_config.num_retries

        def should_stop(retry_state: RetryCallState) -> bool:
            exc = retry_state.outcome.exception() if retry_state.outcome else None
            if exc is not None and _is_server_error(exc):
                return False   # retry indefinitely for server/rate errors
            return retry_state.attempt_number >= num_retries

        @retry(
            stop=should_stop,
            wait=wait_exponential(
                multiplier=self.provider_config.retry_multiplier,
                min=self.provider_config.retry_start_wait,
                max=120,
            ),
            retry=retry_if_exception(is_retryable_error),
            before_sleep=before_sleep_log_retry_attempt(self.logger),
        )
        async def _call_llm() -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
            use_incremental = (
                self._last_response_id is not None
                and len(messages) >= self._last_message_count
                and self._last_message_count > 0
            )

            if use_incremental:
                new_messages = messages[self._last_message_count:]
                instructions, input_items = self._to_responses_input(
                    system_prompt=system_prompt, messages=new_messages
                )
            else:
                instructions, input_items = self._to_responses_input(
                    system_prompt=system_prompt, messages=messages
                )
                # Safety net: strip any reasoning items from full-history input
                input_items = [
                    item for item in input_items
                    if not (isinstance(item, dict) and item.get("type") == "reasoning")
                ]
                # After compaction/emergency drop, the history may contain
                # function_call_output items whose matching function_call was
                # summarised away.  Strip them proactively so the Responses
                # API doesn't reject the request.
                input_items = self._strip_orphaned_function_call_outputs(input_items)

            responses_tools = self._to_responses_tools(tools) if tools else None
            attempt_token_usages: List[Dict[str, Any]] = []
            attempt_raw_token_usages: List[Dict[str, Any]] = []

            params: Dict[str, Any] = {
                "model": self.model_id,
                "instructions": instructions,
                "input": input_items,
                "store": True,
                "max_output_tokens": self.agent_config.max_tokens,
            }

            if responses_tools:
                params["tools"] = responses_tools
                params["tool_choice"] = "auto"

            # Build reasoning config.
            # Use explicit reasoning_effort from config if set; otherwise map
            # thinking budget → low/medium/high/xhigh.
            if self.agent_config.reasoning_effort:
                effort = self.agent_config.reasoning_effort
            elif _enable_thinking:
                if _thinking_budget >= 20000:
                    effort = "xhigh"
                elif _thinking_budget >= 10000:
                    effort = "high"
                elif _thinking_budget >= 5000:
                    effort = "medium"
                else:
                    effort = "low"
            else:
                effort = "low"

            if self._reasoning_effort_supported:
                params["reasoning"] = {"effort": effort, "summary": "auto"}
            if use_incremental and self._last_response_id:
                params["previous_response_id"] = self._last_response_id

            try:
                response = await self._responses_create(params)
            except TypeError as e:
                err_msg = str(e)
                if "reasoning" in err_msg:
                    self._reasoning_effort_supported = False
                    self.logger.warning(
                        "Endpoint rejected reasoning params; "
                        "disabling for all subsequent calls."
                    )
                    params.pop("reasoning", None)
                    response = await self._create_with_content_policy_check(params)
                else:
                    raise
            except openai.BadRequestError as e:
                error_body = getattr(e, "body", {}) or {}
                error_info = (
                    error_body.get("error", {}) if isinstance(error_body, dict) else {}
                )
                error_message = (
                    error_info.get("message", str(e))
                    if isinstance(error_info, dict)
                    else str(e)
                )
                msg_lower = error_message.lower()

                # Detect previous_response_id chain failure — the server
                # no longer has the referenced response. Reset chain and
                # retry with full history.
                #
                # Two patterns:
                # 1. Official OpenAI: mentions "previous_response_id" or
                #    "previous response"
                # 2. SiliconFlow / gateways: "No tool call found for
                #    function call output with call_id ..." — the server
                #    can't match a function_call_output to any known
                #    function_call because the originating response is gone.
                is_response_chain_error = (
                    "previous_response_id" in msg_lower
                    or "previous response" in msg_lower
                    or (
                        "no tool call found" in msg_lower
                        and "call_id" in msg_lower
                    )
                    or (
                        "function_call_output" in msg_lower
                        and "call_id" in msg_lower
                    )
                )
                if is_response_chain_error:
                    self.logger.warning(
                        f"previous_response_id chain broken: {error_message}; "
                        "falling back to full-history mode."
                    )
                    self._last_response_id = None
                    self._last_message_count = 0
                    # Rebuild params in full-history mode
                    instructions, input_items = self._to_responses_input(
                        system_prompt=system_prompt, messages=messages
                    )
                    input_items = [
                        item for item in input_items
                        if not (isinstance(item, dict) and item.get("type") == "reasoning")
                    ]
                    # Strip orphaned function_call_output items whose
                    # call_id has no matching function_call in the input.
                    # This happens when context compaction removed the
                    # assistant message containing the function_call but
                    # kept (or the summary replaced) the user message with
                    # the function_call_output.
                    input_items = self._strip_orphaned_function_call_outputs(input_items)
                    params["input"] = input_items
                    params.pop("previous_response_id", None)
                    response = await self._create_with_content_policy_check(params)

                elif (
                    "reasoning" in msg_lower
                    and "reasoning" in params
                ):
                    self._reasoning_effort_supported = False
                    self.logger.warning(
                        f"Endpoint rejected reasoning params: {error_message}; "
                        "disabling for all subsequent calls."
                    )
                    params.pop("reasoning", None)
                    response = await self._create_with_content_policy_check(params)
                elif self._is_content_policy_error(msg_lower):
                    raise ContentPolicyViolationError(
                        f"Content policy violation: {error_message}"
                    ) from e
                else:
                    raise
            except openai.APIStatusError as e:
                # Non-400 content filter errors (e.g. Azure 403
                # PermissionDeniedError for content safety violations).
                if self._is_content_policy_error(str(e).lower()):
                    raise ContentPolicyViolationError(
                        f"Content policy violation (HTTP {e.status_code}): {e}"
                    ) from e
                raise

            # --- Token usage ---
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

                input_tokens  = raw_usage.get("input_tokens", 0) or 0
                output_tokens = raw_usage.get("output_tokens", 0) or 0
                details       = raw_usage.get("input_tokens_details") or {}
                cached        = (
                    details.get("cached_tokens", 0) or 0
                    if isinstance(details, dict) else 0
                )

                attempt_token_usages.append({
                    "input_tokens": input_tokens - cached,
                    "output_tokens": output_tokens,
                    "cache_creation_input_tokens": 0,
                    "cache_read_input_tokens": cached,
                })

            if not response or not response.output:
                raise InvalidLLMResponseError("[CALL LLM] Responses API returned empty output")

            # --- Detect output-side content filtering ---
            # Azure/OpenAI may accept the input but filter the output,
            # returning status="incomplete" with reason="content_filter".
            resp_status = getattr(response, "status", None)
            if resp_status == "incomplete":
                incomplete_details = getattr(response, "incomplete_details", None)
                reason = getattr(incomplete_details, "reason", None) if incomplete_details else None
                if reason == "content_filter":
                    raise ContentPolicyViolationError(
                        f"Output blocked by content filter (status=incomplete, reason={reason})"
                    )

            # --- Parse output items → content blocks + reasoning blocks ---
            content_blocks: List[Dict[str, Any]] = []
            reasoning_blocks: List[Dict[str, Any]] = []

            for item in response.output:
                if item.type == "message":
                    for block in item.content:
                        if block.type == "output_text" and block.text:
                            content_blocks.append({"type": "text", "text": block.text})

                elif item.type == "function_call":
                    if not item.call_id or not item.name:
                        raise InvalidLLMResponseError(
                            f"[CALL LLM] Invalid function_call item: {item}"
                        )
                    raw_args = item.arguments or "{}"
                    try:
                        parsed_args = (
                            json.loads(raw_args) if isinstance(raw_args, str)
                            else raw_args
                        )
                    except Exception as exc:
                        raise InvalidToolJSONError(
                            f"[CALL LLM] Tool '{item.name}' arguments are not valid JSON: {raw_args}"
                        ) from exc
                    if parsed_args is None:
                        parsed_args = {}
                    if not isinstance(parsed_args, dict):
                        raise InvalidToolJSONError(
                            f"[CALL LLM] Tool '{item.name}' arguments must decode to an object, "
                            f"got {type(parsed_args).__name__}"
                        )
                    validate_tool_input(item.name, parsed_args)
                    content_blocks.append({
                        "type": "tool_use",
                        "id": item.call_id,
                        "name": item.name,
                        "input": parsed_args,
                    })

                elif item.type == "reasoning":
                    # Store reasoning metadata for diagnostics and message history.
                    # Not re-sent to API; server manages continuity via previous_response_id.
                    r_block: Dict[str, Any] = {
                        "type": "reasoning",
                        "id": getattr(item, "id", ""),
                    }
                    enc = getattr(item, "encrypted_content", None)
                    if enc:
                        r_block["encrypted_content"] = enc

                    summary_list = getattr(item, "summary", None) or []
                    if summary_list:
                        r_block["summary"] = [
                            {
                                "type": getattr(sb, "type", "summary_text"),
                                "text": getattr(sb, "text", ""),
                            }
                            for sb in summary_list
                        ]

                    reasoning_blocks.append(r_block)

            if not content_blocks:
                try:
                    self.logger.error(
                        f"[CALL LLM] Empty output — full response: "
                        f"{json.dumps(response.model_dump(), indent=2, default=str)}"
                    )
                except Exception:
                    pass
                raise InvalidLLMResponseError(
                    "[CALL LLM] Responses API had no message content and no function calls"
                )

            # Update chain state for next call.
            resp_id = getattr(response, 'id', None)
            if resp_id:
                self._last_response_id = resp_id
                self._last_message_count = len(messages)
            else:
                # No response ID — cannot establish chain. Reset to full-history.
                self._last_response_id = None
                self._last_message_count = 0

            return (
                content_blocks,
                reasoning_blocks,
                attempt_token_usages,
                attempt_raw_token_usages,
            )

        llm_content, reasoning_blocks, token_usages, raw_token_usages = await _call_llm()
        result: Dict[str, Any] = {"content": llm_content, "token_usages": token_usages}
        if raw_token_usages:
            result["raw_token_usages"] = raw_token_usages
        if reasoning_blocks:
            result["thinking"] = reasoning_blocks
        return result

    # ------------------------------------------------------------------
    # Reasoning support helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _is_content_policy_error(msg_lower: str) -> bool:
        """Check whether an error message indicates a content-policy/safety rejection.

        Covers patterns from Azure Content Safety, OpenAI moderation, and
        common gateway wrappers (SiliconFlow, etc.).
        """
        return (
            ("content" in msg_lower and ("policy" in msg_lower or "filter" in msg_lower or "management" in msg_lower))
            or ("safety" in msg_lower and ("content" in msg_lower or "filter" in msg_lower or "policy" in msg_lower or "violation" in msg_lower))
            or ("filtered" in msg_lower and ("content" in msg_lower or "policy" in msg_lower or "output" in msg_lower))
            or "responsibleai" in msg_lower
            or "jailbreak" in msg_lower
            # Azure / OpenAI "invalid_prompt" wording: "Invalid prompt: your prompt
            # was flagged as potentially violating our usage policy."
            or ("invalid prompt" in msg_lower and ("policy" in msg_lower or "flagged" in msg_lower))
            or ("usage policy" in msg_lower and ("violat" in msg_lower or "flagged" in msg_lower))
            or "invalid_prompt" in msg_lower
        )

    @staticmethod
    def _strip_orphaned_function_call_outputs(
        items: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Remove function_call_output items whose call_id has no matching function_call.

        After a context compaction or emergency drop the conversation history
        may contain a tool result (function_call_output) whose originating
        assistant tool-call (function_call) was summarised away.  The Responses
        API rejects such orphaned outputs with a 400 error.  Stripping them
        lets the request succeed — the model loses the specific tool result
        but retains the surrounding conversation.
        """
        known_call_ids: set[str] = set()
        for item in items:
            if isinstance(item, dict) and item.get("type") == "function_call":
                cid = item.get("call_id")
                if cid:
                    known_call_ids.add(cid)

        cleaned: List[Dict[str, Any]] = []
        stripped = 0
        for item in items:
            if (
                isinstance(item, dict)
                and item.get("type") == "function_call_output"
                and item.get("call_id") not in known_call_ids
            ):
                stripped += 1
                continue
            cleaned.append(item)

        if stripped:
            Logger().warning(
                f"[LLM] Stripped {stripped} orphaned function_call_output item(s) "
                "from full-history input."
            )
        return cleaned

    async def _create_with_content_policy_check(self, params: Dict[str, Any]):
        """Call responses.create and convert content-policy BadRequestErrors."""
        try:
            return await self._responses_create(params)
        except openai.BadRequestError as e:
            error_body = getattr(e, "body", {}) or {}
            error_info = (
                error_body.get("error", {}) if isinstance(error_body, dict) else {}
            )
            error_message = (
                error_info.get("message", str(e))
                if isinstance(error_info, dict)
                else str(e)
            )
            if self._is_content_policy_error(error_message.lower()):
                raise ContentPolicyViolationError(
                    f"Content policy violation: {error_message}"
                ) from e
            raise

    @staticmethod
    def _strip_reasoning_from_input(params: Dict[str, Any]) -> None:
        """Remove reasoning items from the input array (for fallback on unsupported endpoints)."""
        input_items = params.get("input")
        if isinstance(input_items, list):
            params["input"] = [
                item for item in input_items
                if not (isinstance(item, dict) and item.get("type") == "reasoning")
            ]

    # ------------------------------------------------------------------
    # Conversion: internal (Claude-style) → Responses API
    # ------------------------------------------------------------------

    def _to_responses_tools(self, tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Convert Claude tool schema ({name, description, input_schema}) to the
        Responses API flat tool schema.

        Chat Completions:
            {"type": "function", "function": {"name": ..., "description": ..., "parameters": ...}}

        Responses API:
            {"type": "function", "name": ..., "description": ..., "parameters": ...}
        """
        converted: List[Dict[str, Any]] = []
        for t in tools:
            if not t:
                continue
            name = t.get("name")
            if not name:
                continue
            converted.append({
                "type": "function",
                "name": name,
                "description": t.get("description", "") or "",
                "parameters": t.get("input_schema", {"type": "object", "properties": {}}),
            })
        return converted

    def _to_responses_input(
        self,
        *,
        system_prompt: str,
        messages: List[Dict[str, Any]],
    ) -> Tuple[str, List[Dict[str, Any]]]:
        """
        Convert internal Claude-style messages to Responses API input items.

        The system prompt is returned separately as `instructions`.
        Tool use and tool results are converted to function_call /
        function_call_output items at the top level (not nested in messages).

        Internal → Responses API mapping
        ─────────────────────────────────
        user   text block          → {"role":"user", "content": str}
        user   tool_result block   → {"type":"function_call_output","call_id":...,"output":str}
        asst   text block          → {"type":"message","role":"assistant","content":str}
        asst   tool_use block      → {"type":"function_call","call_id":...,"name":...,"arguments":str}
        asst   thinking block      → (skipped)
        """
        input_items: List[Dict[str, Any]] = []

        for msg in messages:
            role    = msg.get("role", "user")
            content = msg.get("content")

            if isinstance(content, str):
                if content.strip():
                    input_items.append({"role": role, "content": content})
                continue

            if not isinstance(content, list):
                text = str(content)
                if text.strip():
                    input_items.append({"role": role, "content": text})
                continue

            if role == "assistant":
                self._convert_assistant_blocks(content, input_items)
            else:
                # user (or unknown → treat as user)
                self._convert_user_blocks(content, input_items)

        return system_prompt, input_items

    def _convert_assistant_blocks(
        self,
        blocks: List[Dict[str, Any]],
        out: List[Dict[str, Any]],
    ) -> None:
        """Flush assistant text and emit function_call items."""
        text_parts: List[str] = []

        for block in blocks:
            if not isinstance(block, dict):
                text_parts.append(str(block))
                continue

            btype = block.get("type")

            if btype == "text":
                text_parts.append(block.get("text", "") or "")

            elif btype == "tool_use":
                # Flush accumulated text as an assistant message first
                self._flush_assistant_text(text_parts, out)
                text_parts = []
                out.append({
                    "type": "function_call",
                    "call_id": block.get("id", ""),
                    "name": block.get("name", ""),
                    "arguments": json.dumps(
                        block.get("input", {}) or {}, ensure_ascii=False
                    ),
                })

            elif btype == "reasoning":
                # Convert reasoning blocks to plain summary text.
                # encrypted_content is never re-sent; the server manages
                # reasoning continuity via previous_response_id.
                summary_blocks = block.get("summary") or []
                summary_texts = []
                if isinstance(summary_blocks, list):
                    for sb in summary_blocks:
                        if isinstance(sb, dict):
                            t = sb.get("text", "")
                            if t:
                                summary_texts.append(t)
                if summary_texts:
                    text_parts.extend(summary_texts)
                # If no summary available, skip silently.

            elif btype in ("thinking", "redacted_thinking"):
                # Claude-specific thinking blocks — skip for OpenAI
                continue

            else:
                # Unknown block type → stringify into text
                text_parts.append(json.dumps(block, ensure_ascii=False))

        self._flush_assistant_text(text_parts, out)

    def _flush_assistant_text(
        self,
        text_parts: List[str],
        out: List[Dict[str, Any]],
    ) -> None:
        text = "\n".join(t for t in text_parts if t.strip()).strip()
        if text:
            out.append({
                "type": "message",
                "role": "assistant",
                "content": text,
            })

    def _convert_user_blocks(
        self,
        blocks: List[Dict[str, Any]],
        out: List[Dict[str, Any]],
    ) -> None:
        """Flush user text and emit function_call_output items."""
        text_parts: List[str] = []

        for block in blocks:
            if not isinstance(block, dict):
                text_parts.append(str(block))
                continue

            btype = block.get("type")

            if btype == "text":
                text_parts.append(block.get("text", "") or "")

            elif btype == "tool_result":
                # Flush text before the tool result
                self._flush_user_text(text_parts, out)
                text_parts = []
                out.append({
                    "type": "function_call_output",
                    "call_id": block.get("tool_use_id", ""),
                    "output": self._flatten_tool_result_content(block.get("content", "")),
                })

            else:
                text_parts.append(json.dumps(block, ensure_ascii=False))

        self._flush_user_text(text_parts, out)

    def _flush_user_text(
        self,
        text_parts: List[str],
        out: List[Dict[str, Any]],
    ) -> None:
        text = "\n".join(t for t in text_parts if t.strip()).strip()
        if text:
            out.append({"role": "user", "content": text})

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
            return "\n".join(p for p in parts if p.strip()).strip()
        if isinstance(content, dict):
            return json.dumps(content, ensure_ascii=False, indent=2)
        return str(content)


def _truncate_error_message(exc: BaseException, max_length: int = _MAX_ERROR_LOG_LENGTH) -> str:
    """Truncate long error messages (e.g. Cloudflare HTML pages) for cleaner logs."""
    msg = str(exc)
    status_match = re.search(r'Error code (\d{3})', msg)
    status_hint = f" [HTTP {status_match.group(1)}]" if status_match else ""
    if len(msg) <= max_length:
        return f"{exc.__class__.__name__}{status_hint}: {msg}"
    truncated = msg[:max_length]
    return f"{exc.__class__.__name__}{status_hint}: {truncated}... (truncated, total {len(msg)} chars)"


def before_sleep_log_retry_attempt(logger: Logger):
    """Tenacity before_sleep callback with truncated error messages and attempt info."""
    def before_sleep(retry_state: RetryCallState):
        exc: BaseException | None = (
            retry_state.outcome.exception() if retry_state.outcome else None
        )
        attempt = retry_state.attempt_number
        wait_info = ""
        if retry_state.next_action and hasattr(retry_state.next_action, "sleep"):
            wait_info = f", next retry in {retry_state.next_action.sleep:.1f}s"
        if exc is None:
            logger.info(f"[LLM RETRY] Attempt {attempt} failed (no exception){wait_info}")
            return None
        error_summary = _truncate_error_message(exc)
        logger.info(f"[LLM RETRY] Attempt {attempt} failed: {error_summary}{wait_info}")
        return None
    return before_sleep
