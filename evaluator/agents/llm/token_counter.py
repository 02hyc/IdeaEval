from typing import Dict, Any, List, Optional, Union, Any as AnyType

from agents.config import ClaudeTokenCountConfig, OpenAITokenCountConfig
from agents.llm.claude_token_counter import ClaudeTokenCounter
from agents.llm.openai_token_counter import OpenAITokenCounter


class TokenCounter:
    """Dispatches to Claude or OpenAI token counter based on config."""

    def __init__(self, token_count_config: Optional[Union[ClaudeTokenCountConfig, OpenAITokenCountConfig]] = None):
        if isinstance(token_count_config, OpenAITokenCountConfig):
            self._impl = OpenAITokenCounter(token_count_config)
        elif isinstance(token_count_config, ClaudeTokenCountConfig):
            self._impl = ClaudeTokenCounter(token_count_config)
        else:
            self._impl = None

    async def count_tokens(
        self,
        system_prompt: str,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        thinking_enabled: bool = False
    ) -> int:
        if self._impl is None:
            raise ValueError("TokenCounter requires a configured ClaudeTokenCountConfig or OpenAITokenCountConfig")
        return await self._impl.count_tokens(
            system_prompt=system_prompt,
            messages=messages,
            tools=tools,
            thinking_enabled=thinking_enabled
        )