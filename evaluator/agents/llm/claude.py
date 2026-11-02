from typing import Any, Dict, List, Optional, Union

from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type, RetryCallState
import anthropic
from anthropic import AsyncAnthropic, AsyncAnthropicBedrock
from anthropic.types.message import Message

from agents.config import AnthropicProviderConfig, BedrockProviderConfig, AgentConfig
from agents.utils.exceptions import InvalidLLMResponseError, InvalidToolInputError
from utils.logger import Logger
from agents.tools.tool_registry import validate_tool_input


class LLMClaude:
    """
    Claude LLM client using AWS Bedrock or Anthropic API (native/proxy).

    New architecture:
    - provider_config: Connection info (credentials, endpoints, retry settings)
    - agent_config: Runtime parameters (max_tokens, temperature, thinking, etc.)
    - model_id: Actual model identifier for API calls
    """

    def __init__(
        self,
        provider_config: Union[BedrockProviderConfig, AnthropicProviderConfig],
        agent_config: AgentConfig,
        model_id: str
    ):
        self.provider_config = provider_config
        self.agent_config = agent_config
        self.model_id = model_id
        self._extra_body: Optional[Dict[str, Any]] = None

        if isinstance(provider_config, AnthropicProviderConfig):
            self.client = AsyncAnthropic(
                api_key=provider_config.api_key.get_secret_value(),
                base_url=provider_config.base_url,
            )
            if provider_config.anthropic_version:
                self._extra_body = {"anthropic_version": provider_config.anthropic_version}
        else:
            self.client = AsyncAnthropicBedrock(
                aws_access_key=provider_config.aws_access_key.get_secret_value(),
                aws_secret_key=provider_config.aws_secret_key.get_secret_value(),
                aws_region=provider_config.aws_region,
            )
        self.logger = Logger()

    async def call_llm(
        self,
        messages: List[Dict[str, Any]],
        system_prompt: str,
        tools: List[Dict[str, Any]],
        use_cache: bool = True,
        enable_thinking: Optional[bool] = None,
        thinking_budget_tokens: Optional[int] = None
    ):
        """
        Call the LLM with the given messages and tools.
        
        Args:
            messages: List of message dicts
            system_prompt: System prompt string
            tools: List of tool definitions
            use_cache: Whether to use prompt caching
            enable_thinking: Override agent_config.enable_thinking if provided
            thinking_budget_tokens: Override agent_config.thinking_budget_tokens if provided
        
        Returns:
            Dict with 'content' (list of content blocks) and 'token_usages'
        """
        # Use agent_config values as defaults, allow overrides
        _enable_thinking = enable_thinking if enable_thinking is not None else self.agent_config.enable_thinking
        _thinking_budget = thinking_budget_tokens if thinking_budget_tokens is not None else self.agent_config.thinking_budget_tokens

        retryable_exceptions = (
            anthropic.RateLimitError,
            anthropic.APITimeoutError,
            anthropic.APIConnectionError,
            anthropic.InternalServerError,
            InvalidLLMResponseError,
            InvalidToolInputError,
            ValueError
        )

        @retry(
            stop=stop_after_attempt(self.provider_config.num_retries),
            wait=wait_exponential(
                multiplier=self.provider_config.retry_multiplier, 
                min=self.provider_config.retry_start_wait, 
                max=30
            ),
            retry=retry_if_exception_type(retryable_exceptions),
            before_sleep=before_sleep_log_retry_attempt(self.logger)
        )
        async def _call_llm():
            # Prepare system prompt with cache control if enabled
            if use_cache:
                system_blocks = [
                    {
                        "type": "text",
                        "text": system_prompt,
                        "cache_control": {"type": "ephemeral"}
                    }
                ]
            else:
                system_blocks = system_prompt

            # Mark the last content block of the last message with cache_control
            # so all prior turns are cached between rounds.
            cached_messages = messages
            if use_cache and cached_messages:
                cached_messages = [m if i < len(messages) - 1 else dict(m) for i, m in enumerate(messages)]
                last_msg = cached_messages[-1]
                content = last_msg.get("content")
                if isinstance(content, list) and content:
                    last_block = content[-1]
                    if isinstance(last_block, dict) and "cache_control" not in last_block:
                        content = content.copy()
                        content[-1] = {**last_block, "cache_control": {"type": "ephemeral"}}
                        last_msg["content"] = content
                elif isinstance(content, str):
                    last_msg["content"] = [
                        {"type": "text", "text": content, "cache_control": {"type": "ephemeral"}}
                    ]

            request_params = {
                "system": system_blocks,
                "model": self.model_id,
                "messages": cached_messages,
                "max_tokens": self.agent_config.max_tokens,
            }
            
            # Add tools only if provided (API doesn't accept None)
            if tools:
                cached_tools = tools
                if use_cache:
                    # Mark the last tool with cache control to cache all tools
                    cached_tools = tools.copy()
                    if len(cached_tools) > 0:
                        cached_tools[-1] = {**cached_tools[-1], "cache_control": {"type": "ephemeral"}}
                request_params["tools"] = cached_tools
            
            # Add thinking configuration if enabled
            if _enable_thinking:
                if self.agent_config.thinking_type == "adaptive":
                    # Claude 4.6+: adaptive thinking (model decides when/how much to think)
                    request_params["thinking"] = {"type": "adaptive"}
                else:
                    # Claude 4.5 and earlier: manual budget
                    request_params["thinking"] = {
                        "type": "enabled",
                        "budget_tokens": _thinking_budget,
                    }
                # When thinking is enabled, temperature must be 1 (or not set)
            else:
                # Only set temperature when thinking is disabled
                if self.agent_config.temperature is not None:
                    request_params["temperature"] = self.agent_config.temperature
            
            if self.agent_config.top_p is not None:
                request_params["top_p"] = self.agent_config.top_p

            if self._extra_body:
                request_params["extra_body"] = self._extra_body

            # Use streaming to handle long requests (required when thinking is enabled
            # or requests may take >10 minutes)
            content = []
            thinking_content = []
            usage_data = {}
            attempt_token_usages: List[Dict[str, Any]] = []
            attempt_raw_token_usages: List[Dict[str, Any]] = []

            async with self.client.messages.stream(**request_params) as stream:
                async for event in stream:
                    # Handle different event types from streaming
                    pass  # Events are accumulated, we get final message at the end

                # Get the final message after stream completes
                response: Message = await stream.get_final_message()

            if response and response.usage:
                usage_dump = response.usage.model_dump()
                attempt_token_usages.append(usage_dump)
                attempt_raw_token_usages.append(usage_dump)

            if response and response.content:
                for block in response.content:
                    if block.type == "text":
                        content.append(block.model_dump())
                    elif block.type == "thinking":
                        # Handle thinking blocks - store separately
                        thinking_content.append(block.model_dump())
                    elif block.type == "tool_use":
                        if block.name is None or block.input is None:
                            raise InvalidLLMResponseError(f"[CALL LLM] Invalid tool use in LLM response. Block: {block.model_dump()}")

                        tool_use_input = block.input if block.input else {}
                        validate_tool_input(block.name, tool_use_input)

                        content.append(block.model_dump())

                    else:
                        raise InvalidLLMResponseError(f"[CALL LLM] Invalid content type in LLM response. Block: {block.model_dump()}")
            else:
                raise InvalidLLMResponseError("[CALL LLM] LLM response is empty")
            
            if not content and not thinking_content:
                raise InvalidLLMResponseError("[CALL LLM] LLM response is empty")
            
            return content, thinking_content, attempt_token_usages, attempt_raw_token_usages

        (
            llm_responded_content,
            thinking_content,
            token_usages,
            raw_token_usages,
        ) = await _call_llm()
        
        result = {
            "content": llm_responded_content,
            "token_usages": token_usages
        }
        if raw_token_usages:
            result["raw_token_usages"] = raw_token_usages
        
        # Include thinking content if present
        if thinking_content:
            result["thinking"] = thinking_content
        
        return result


def before_sleep_log_retry_attempt(logger: Logger):
    def before_sleep(retry_state: RetryCallState):
        exc: BaseException | None = retry_state.outcome.exception() if retry_state.outcome else None
        if exc is None:
            logger.info("Retrying after unknown error (no exception available)")
            return None
        logger.info(f"Retrying after {exc.__class__.__name__}: {exc}")
        return None
    return before_sleep
