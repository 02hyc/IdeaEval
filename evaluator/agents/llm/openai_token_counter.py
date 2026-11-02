"""
OpenAI token counter using tiktoken.

Since GPT-5.2 and similar models discard reasoning tokens from context,
we only need tiktoken to count tokens - no need to track API-reported usage.
"""
import json
from typing import Dict, Any, List, Optional, Any as AnyType

from agents.config import OpenAITokenCountConfig
from agents.llm.token_counter_base import BaseTokenCounter

import tiktoken


class OpenAITokenCounter(BaseTokenCounter):
    """OpenAI-compatible token counter using tiktoken."""

    def __init__(self, token_count_config: OpenAITokenCountConfig):
        super().__init__()
        self.token_count_config = token_count_config
        self._encoding = tiktoken.get_encoding("o200k_base")

    def _encode(self, text: str) -> list:
        """Encode text with tiktoken, allowing all special tokens as normal text.
        
        This prevents crashes when user content (e.g. academic papers) contains
        strings like '<|endoftext|>' that tiktoken treats as special tokens.
        """
        return self._encoding.encode(text, disallowed_special=())

    async def count_tokens(
        self,
        system_prompt: str,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        thinking_enabled: bool = False
    ) -> int:
        """
        Count tokens using tiktoken.
        
        Since GPT-5.2 discards reasoning tokens from context, we only need
        tiktoken to estimate the input tokens.
        
        Note: task_messages is unused but kept for API compatibility with ClaudeTokenCounter.
        """
        return await self._count_with_tiktoken(system_prompt, messages, tools)

    async def _count_with_tiktoken(
        self,
        system_prompt: str,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> int:
        """Count tokens using tiktoken."""
        openai_messages = self._to_openai_messages(
            system_prompt=system_prompt,
            messages=messages,
        )
        openai_tools = self._to_openai_tools(tools) if tools else None

        token_count = 0
        for msg in openai_messages:
            token_count += self._count_message_tokens(msg)

        if openai_tools:
            token_count += len(self._encode(json.dumps(openai_tools, ensure_ascii=False)))

        # Add overhead for request structure
        token_count += 20
        return token_count

    def _count_message_tokens(self, message: Dict[str, Any]) -> int:
        """Count tokens in a single message."""
        tokens = 0
        
        role = message.get("role", "")
        if role:
            tokens += len(self._encode(role))

        content = message.get("content")
        if isinstance(content, str):
            tokens += len(self._encode(content))
        elif isinstance(content, list):
            for part in content:
                tokens += len(self._encode(json.dumps(part, ensure_ascii=False)))

        tool_calls = message.get("tool_calls")
        if tool_calls:
            tokens += len(self._encode(json.dumps(tool_calls, ensure_ascii=False)))

        tool_call_id = message.get("tool_call_id")
        if tool_call_id:
            tokens += len(self._encode(tool_call_id))

        # Message overhead (role/content separators)
        return tokens + 4

    def _to_openai_tools(self, tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Convert Claude tool format to OpenAI format."""
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

    def _to_openai_messages(self, *, system_prompt: str, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Convert internal message format to OpenAI format for token counting."""
        openai_messages: List[Dict[str, Any]] = []
        
        if system_prompt:
            openai_messages.append({"role": "developer", "content": system_prompt})

        for m in messages:
            role = m.get("role")
            content = m.get("content")
            if role not in ("user", "assistant", "developer"):
                role = "user"

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
                                "name": block.get("name"),
                                "arguments": json.dumps(block.get("input", {}) or {}, ensure_ascii=False),
                            },
                        })
                    elif btype in ("thinking", "redacted_thinking"):
                        # Thinking blocks are not sent to OpenAI
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

            # User/system messages with tool_result blocks
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
                        openai_messages.append({
                            "role": "tool", 
                            "tool_call_id": tool_use_id, 
                            "content": tool_content
                        })
                    else:
                        openai_messages.append({"role": role, "content": tool_content})
                    continue

                pending_text_parts.append(json.dumps(block, ensure_ascii=False))

            user_text = "\n".join([t for t in pending_text_parts if t.strip()]).strip()
            if user_text:
                openai_messages.append({"role": role, "content": user_text})

        return openai_messages

    def _flatten_tool_result_content(self, content: Any) -> str:
        """Flatten tool result content to a string."""
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
