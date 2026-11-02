from typing import Dict, Any, List, Optional

from anthropic import AsyncAnthropic, BadRequestError

from agents.config import ClaudeTokenCountConfig
from agents.llm.token_counter_base import BaseTokenCounter
from utils.logger import Logger


class ClaudeTokenCounter(BaseTokenCounter):
    """Claude token counter using the custom /v1/messages/count_tokens API."""

    def __init__(self, token_count_config: ClaudeTokenCountConfig):
        super().__init__()
        self.token_count_config = token_count_config
        self.logger = Logger()

        self._api_key = (
            self.token_count_config.api_key.get_secret_value()
            if self.token_count_config.api_key
            else ""
        )
        self._base_url = (self.token_count_config.base_url or "").rstrip("/") or None
        self._client = AsyncAnthropic(api_key=self._api_key, base_url=self._base_url)
        self._accurate_disabled = False

    async def count_tokens(
        self,
        system_prompt: str,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        thinking_enabled: bool = False,
        task_messages: Optional[List[Any]] = None,
    ) -> int:
        count_method = self.token_count_config.method if self.token_count_config else "estimated"
        if count_method == "accurate" and not self._accurate_disabled:
            try:
                return await self.accurate_token_count(system_prompt, messages, tools, thinking_enabled)
            except BaseException as e:
                msg = str(e)
                if "Input is too long" in msg:
                    self.logger.info("[TOKEN_COUNT] Falling back to estimated count (input too long for count_tokens API)")
                else:
                    self.logger.warning(f"[TOKEN_COUNT] Accurate token count failed, falling back to estimated: {e}")
        return await self.estimated_token_count(system_prompt, messages, tools, thinking_enabled)

    async def accurate_token_count(
        self,
        system_prompt: str,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        thinking_enabled: bool = False,
    ) -> int:
        """Accurate token counting using the /v1/messages/count_tokens API."""

        if not thinking_enabled:
            cleaned_messages = self._clean_messages_for_thinking_mode(messages, False)
        else:
            cleaned_messages = messages

        kwargs: Dict[str, Any] = {
            "model": self.token_count_config.model,
            "messages": cleaned_messages,
        }
        if system_prompt:
            kwargs["system"] = system_prompt
        if tools:
            kwargs["tools"] = tools
        if thinking_enabled:
            kwargs["thinking"] = {
                "type": "enabled",
                "budget_tokens": self.token_count_config.thinking_budget_tokens,
            }

        extra_body = {"max_tokens": self.token_count_config.max_tokens}

        retry_count = 0
        while retry_count < self.token_count_config.max_retries:
            try:
                response = await self._client.messages.count_tokens(**kwargs, extra_body=extra_body)
                return response.input_tokens
            except (BadRequestError, ValueError, RuntimeError, TypeError) as e:
                self._accurate_disabled = True
                self.logger.warning(
                    f"[TOKEN_COUNT] count_tokens API not supported by this provider ({e}). "
                    "Permanently falling back to estimated counting for this session."
                )
                raise
            except Exception as e:
                self.logger.error(f"[TOKEN_COUNT] Could not calculate accurate tokens: {e}")
                retry_count += 1
                if retry_count < self.token_count_config.max_retries:
                    self.logger.info(f"[TOKEN_COUNT] Retrying ({retry_count}/{self.token_count_config.max_retries})...")
                else:
                    self.logger.error(
                        f"[TOKEN_COUNT] Failed after {self.token_count_config.max_retries} attempts"
                    )
                    raise

        raise RuntimeError("[TOKEN_COUNT] Exhausted retry loop without result")

    def _clean_messages_for_thinking_mode(
        self, messages: List[Dict[str, Any]], thinking_enabled: bool
    ) -> List[Dict[str, Any]]:
        if thinking_enabled:
            return messages

        cleaned_messages = []
        for message in messages:
            cleaned_message = message.copy()
            content = message.get("content", [])

            if isinstance(content, str):
                cleaned_messages.append(cleaned_message)
                continue

            if isinstance(content, list):
                filtered_content = []
                for item in content:
                    if isinstance(item, dict):
                        item_type = item.get("type")
                        if item_type not in ["thinking", "redacted_thinking"]:
                            filtered_content.append(item)
                    else:
                        filtered_content.append(item)

                if filtered_content:
                    cleaned_message["content"] = filtered_content
                    cleaned_messages.append(cleaned_message)
                elif message.get("role") == "user":
                    cleaned_message["content"] = filtered_content
                    cleaned_messages.append(cleaned_message)
            else:
                cleaned_messages.append(cleaned_message)

        return cleaned_messages
