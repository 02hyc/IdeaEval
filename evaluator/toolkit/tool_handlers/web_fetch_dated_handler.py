"""Date-aware WebFetch handler for the ranker pipeline.

Uses the team-hosted web-tools-service for cutoff-aware web page fetching.
"""
from __future__ import annotations

import os
from typing import Any, Dict

import aiohttp

from .base_tool_handler import BaseToolHandler, ToolState, ToolFailedError


class WebFetchDatedToolHandler(BaseToolHandler):
    """Web fetch with server-side date cutoff enforcement."""

    def __init__(self, tool_state: ToolState):
        super().__init__(tool_state=tool_state)
        self.requires_confirmation = False

    @property
    def name(self) -> str:
        return "WebFetch"

    @property
    def display_result_type(self) -> str:
        return "web_fetch_result"

    @property
    def input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "The URL to fetch content from",
                },
                "prompt": {
                    "type": "string",
                    "description": "What to extract or focus on from the page content",
                },
            },
            "required": ["url", "prompt"],
            "additionalProperties": False,
        }

    async def execute_async(self, url: str, prompt: str) -> Dict[str, Any]:
        url = url.strip()
        if not url:
            raise ToolFailedError("Error: empty URL")

        payload: dict[str, Any] = {"url": url, "mode": "rules"}
        cutoff_date = self.tool_state.cutoff_date
        if cutoff_date:
            payload["cutoff_date"] = cutoff_date

        service_url = os.getenv("WEB_TOOLS_SERVICE_URL", "http://localhost:8900").rstrip("/")
        service_token = os.getenv("WEB_TOOLS_SERVICE_TOKEN", "")

        headers = {
            "Authorization": f"Bearer {service_token}",
            "Content-Type": "application/json",
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{service_url}/fetch",
                    json=payload,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=60),
                ) as resp:
                    if resp.status == 401:
                        raise ToolFailedError("Web fetch service auth failed (401)")
                    if resp.status != 200:
                        body = await resp.text()
                        raise ToolFailedError(f"Web fetch service error: {resp.status} — {body[:200]}")
                    data = await resp.json()

            agent_result = data.get("agent_result", "")

            if not data.get("success"):
                return {
                    "tool_success": True,
                    "result": agent_result or "(fetch filtered or failed)",
                    "display_result": {"type": "web_fetch_result", "data": {}, "tool_success": True},
                }

            return {
                "tool_success": True,
                "result": agent_result,
                "display_result": {"type": "web_fetch_result", "data": {}, "tool_success": True},
            }

        except ToolFailedError:
            raise
        except Exception as e:
            raise ToolFailedError(f"Error during web fetch: {str(e)}")
