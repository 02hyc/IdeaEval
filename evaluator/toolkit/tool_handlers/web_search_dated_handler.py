"""Date-aware WebSearch handler for the ranker pipeline.

Uses the team-hosted web-search-fetch-service for cutoff-aware web search.
"""
from __future__ import annotations

import os
from typing import Any, Dict

import aiohttp

from .base_tool_handler import BaseToolHandler, ToolState, ToolFailedError


class WebSearchDatedToolHandler(BaseToolHandler):
    """Web search with server-side date cutoff enforcement."""

    def __init__(self, tool_state: ToolState):
        super().__init__(tool_state=tool_state)
        self.requires_confirmation = False

    @property
    def name(self) -> str:
        return "WebSearch"

    @property
    def display_result_type(self) -> str:
        return "web_search_result"

    @property
    def input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query",
                },
            },
            "required": ["query"],
            "additionalProperties": False,
        }

    async def execute_async(
        self,
        query: str,
        allowed_domains: list[str] | None = None,
        blocked_domains: list[str] | None = None,
    ) -> Dict[str, Any]:
        import asyncio
        import random

        query = query.strip()
        if not query:
            raise ToolFailedError("Error: empty query")

        payload: dict[str, Any] = {"query": query}
        cutoff_date = self.tool_state.cutoff_date
        if cutoff_date:
            payload["cutoff_date"] = cutoff_date

        service_url = os.getenv("WEB_TOOLS_SERVICE_URL", "http://localhost:8900").rstrip("/")
        service_token = os.getenv("WEB_TOOLS_SERVICE_TOKEN", "")

        headers = {
            "Authorization": f"Bearer {service_token}",
            "Content-Type": "application/json",
        }

        # Retry transient service-side failures ("temporarily unavailable" =
        # the wrapper service couldn't reach upstream serper inside its own
        # 30 s timeout, typically during burst startup). Up to 2 retries with
        # exponential backoff + jitter. Other errors bubble up immediately.
        max_retries = 2
        agent_result = "(web search failed)"
        for attempt in range(max_retries + 1):
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.post(
                        f"{service_url}/search",
                        json=payload,
                        headers=headers,
                        timeout=aiohttp.ClientTimeout(total=300),
                    ) as resp:
                        if resp.status == 401:
                            raise ToolFailedError("Web search service auth failed (401)")
                        if resp.status != 200:
                            body = await resp.text()
                            raise ToolFailedError(
                                f"Web search service error: {resp.status} — {body[:200]}"
                            )
                        data = await resp.json()

                success = bool(data.get("success"))
                agent_result = data.get("agent_result", "" if success else "(web search failed)")

                if not success and attempt < max_retries and self._is_transient(agent_result):
                    wait_s = (2 ** attempt) * 5 + random.uniform(0, 1)
                    await asyncio.sleep(wait_s)
                    continue

                return {
                    "tool_success": True,
                    "result": agent_result if success else agent_result or "(no results)",
                    "display_result": {
                        "type": "web_search_result",
                        "data": {},
                        "tool_success": True,
                    },
                }

            except ToolFailedError:
                raise
            except asyncio.TimeoutError:
                # Hard 300 s timeout: service stalled (often during startup
                # burst). Same retry logic as soft fails — give it a beat
                # and try again before giving up.
                if attempt < max_retries:
                    wait_s = (2 ** attempt) * 5 + random.uniform(0, 1)
                    await asyncio.sleep(wait_s)
                    continue
                raise ToolFailedError("Error during web search: TimeoutError: ")
            except Exception as e:
                raise ToolFailedError(f"Error during web search: {type(e).__name__}: {e}")

        # Exhausted retries — surface the last seen agent_result string.
        return {
            "tool_success": True,
            "result": agent_result or "Search failed: service temporarily unavailable",
            "display_result": {"type": "web_search_result", "data": {}, "tool_success": True},
        }

    @staticmethod
    def _is_transient(agent_result: str) -> bool:
        """Heuristic: is the failure message worth retrying?"""
        if not agent_result:
            return False
        s = agent_result.lower()
        return (
            "temporarily unavailable" in s
            or "service unavailable" in s
            or "rate limit" in s
            or "try again" in s
        )
