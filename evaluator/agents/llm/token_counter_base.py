import io
import json
import base64
from typing import Dict, Any, List, Optional

import httpx

from utils.logger import Logger


class BaseTokenCounter:
    """Shared token counting helpers (estimation + media support)."""

    def __init__(self):
        self.logger = Logger()

    async def estimated_token_count(
        self,
        system_prompt: str,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        thinking_enabled: bool = False,
    ) -> int:
        # TODO: count_tokens api is not supported in LiteLLM, so we use the following method to count tokens
        # This method is not accurate, but it's the best we can do for now
        #
        # IMPORTANT: We use a conservative ratio of 3 chars per token (not 4) to avoid underestimation.
        # This is safer because underestimation leads to "input too long" errors.
        CHARS_PER_TOKEN = 3  # Conservative estimate (was 4, but that underestimates)

        token_count = 0

        # Add base overhead for message structure (roles, JSON formatting, etc.)
        # Each message has ~10-20 tokens of structural overhead
        MESSAGE_OVERHEAD = 15
        token_count += len(messages) * MESSAGE_OVERHEAD

        token_count += await self.count_system_prompt_tokens(system_prompt)
        token_count += await self.count_tool_tokens(tools)
        for message in messages:
            content = message["content"]
            # Handle both string and list content formats
            if isinstance(content, str):
                token_count += len(content) / CHARS_PER_TOKEN
            elif isinstance(content, list):
                for item in content:
                    item_type = item.get("type", "")
                    if item_type == "text":
                        token_count += len(item.get("text", "")) / CHARS_PER_TOKEN
                    elif item_type == "tool_use":
                        # Tool use has overhead: name, id, etc.
                        token_count += 20  # Overhead for tool metadata
                        token_count += len(json.dumps(item.get("input", {}))) / CHARS_PER_TOKEN
                    elif item_type == "tool_result":
                        # Tool result has overhead: tool_use_id, is_error, etc.
                        token_count += 15  # Overhead for tool result metadata
                        # Handle tool_result with flexible structure
                        if "content" in item:
                            content_data = item["content"]
                            if isinstance(content_data, str):
                                token_count += len(content_data) / CHARS_PER_TOKEN
                            elif isinstance(content_data, list):
                                # Recursively handle content blocks in tool results
                                for block in content_data:
                                    if isinstance(block, dict) and block.get("type") == "text":
                                        token_count += len(block.get("text", "")) / CHARS_PER_TOKEN
                                    else:
                                        token_count += len(json.dumps(block)) / CHARS_PER_TOKEN
                            else:
                                token_count += len(json.dumps(content_data)) / CHARS_PER_TOKEN
                        else:
                            # Fallback: estimate tokens from entire item except type and tool_use_id
                            fallback_data = {k: v for k, v in item.items() if k not in ["type", "tool_use_id"]}
                            token_count += len(json.dumps(fallback_data)) / CHARS_PER_TOKEN
                    elif item_type == "thinking":
                        token_count += len(item.get("thinking", "")) / CHARS_PER_TOKEN
                        # Add signature tokens if present
                        if "signature" in item:
                            token_count += len(item["signature"]) / CHARS_PER_TOKEN
                    elif item_type == "document":
                        token_count += await self._count_pdf_tokens(item)
                    elif item_type == "image":
                        token_count += await self._count_image_tokens(item)
                    else:
                        # Unknown type - estimate conservatively
                        self.logger.warn(
                            f"[TOKEN_COUNT] Unknown content type: {item.get('type')}, estimating from JSON"
                        )
                        token_count += len(json.dumps(item)) / CHARS_PER_TOKEN

        return token_count

    async def count_system_prompt_tokens(self, system_prompt: str) -> int:
        # Conservative: 3 chars per token + overhead for system prompt structure
        CHARS_PER_TOKEN = 3
        return len(system_prompt) / CHARS_PER_TOKEN + 50  # 50 tokens overhead for system prompt wrapper

    async def count_tool_tokens(self, tools: List[Dict[str, Any]]) -> int:
        # Conservative: 3 chars per token + overhead per tool
        CHARS_PER_TOKEN = 3
        TOOL_OVERHEAD = 30  # Each tool has structural overhead
        token_count = 0
        if tools:
            for tool in tools:
                token_count += TOOL_OVERHEAD
                token_count += len(json.dumps(tool)) / CHARS_PER_TOKEN
        return token_count

    async def _count_image_tokens(self, content: Dict[str, Any]) -> int:
        """Count tokens for image content using formula: (width * height) / 750"""
        try:
            from PIL import Image

            source = content.get("source", {})

            if source.get("type") == "base64":
                # Handle base64 data
                base64_data = source.get("data")
                if not base64_data:
                    return 0

                # Decode base64 to get image dimensions
                image_data = base64.b64decode(base64_data)
                image = Image.open(io.BytesIO(image_data))
                width, height = image.size

            elif source.get("type") == "url":
                # Handle URL data
                url = source.get("url")
                if not url:
                    return 0

                # Fetch image from URL
                response = httpx.get(url, timeout=30.0)
                response.raise_for_status()
                image_data = response.content
                image = Image.open(io.BytesIO(image_data))
                width, height = image.size

            else:
                # Unknown source type, return 0
                return 0

            # Calculate tokens using formula: (width * height) / 750
            tokens = int((width * height) / 750)
            return tokens

        except Exception as e:
            # If we can't process the image, return a default estimate
            self.logger.error(f"[TOKEN_COUNT] Could not calculate image tokens: {e}")
            return 1000  # Default fallback

    async def _count_pdf_tokens(self, content: Dict[str, Any]) -> int:
        """Count tokens for PDF content using formula: pages * 2500 + (tokens when pages viewed as images)"""
        try:
            import PyPDF2

            source = content.get("source", {})

            if source.get("type") == "base64":
                # Handle base64 data
                base64_data = source.get("data")
                if not base64_data:
                    return 0

                # Decode base64 to get PDF data
                pdf_data = base64.b64decode(base64_data)
                pdf_reader = PyPDF2.PdfReader(io.BytesIO(pdf_data))
                num_pages = len(pdf_reader.pages)

            elif source.get("type") == "url":
                # Handle URL data
                url = source.get("url")
                if not url:
                    return 0

                # Fetch PDF from URL
                response = httpx.get(url, timeout=30.0)
                response.raise_for_status()
                pdf_data = response.content
                pdf_reader = PyPDF2.PdfReader(io.BytesIO(pdf_data))
                num_pages = len(pdf_reader.pages)

            else:
                # Unknown source type, return 0
                return 0

            # Calculate tokens using formula: pages * 2500 + (tokens when pages viewed as images)
            # Assuming each PDF page as image is approximately 1024x768 pixels
            page_image_tokens = int((1024 * 768) / 750)  # ~1050 tokens per page as image
            total_tokens = num_pages * 2500 + num_pages * page_image_tokens
            return total_tokens

        except Exception as e:
            # If we can't process the PDF, return a default estimate
            self.logger.error(f"[TOKEN_COUNT] Could not calculate PDF tokens: {e}")
            return 5000  # Default fallback for a typical PDF
