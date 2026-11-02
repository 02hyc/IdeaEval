"""
FallbackLLMClient — wraps a primary + fallback LLM client.

If the primary call fails (any exception, including a 300s timeout that breaks
infinite server-error retries), the fallback client is tried transparently.
"""

import asyncio
from typing import Any, Dict, List, Optional

from agents.utils.exceptions import ContentPolicyViolationError
from utils.logger import Logger


class FallbackLLMClient:
    """
    Transparent fallback wrapper around two LLM clients with the same
    ``call_llm`` interface.

    Behaviour:
    1. Call ``primary.call_llm(...)`` inside a 300 s timeout.
    2. If it raises *any* exception, log a warning and try ``fallback.call_llm(...)``.
    3. If the fallback also fails, re-raise the **primary** exception so the
       caller's existing error-handling (runner retry logic, etc.) stays compatible.
    """

    PRIMARY_TIMEOUT_SECONDS = 300.0

    def __init__(self, primary, fallback, fallback_model_alias: str = ""):
        self.primary = primary
        self.fallback = fallback
        self._fallback_model_alias = fallback_model_alias
        self.logger = Logger()
        # Expose attributes that callers might read from the underlying client.
        self.agent_config = primary.agent_config
        self.model_id = primary.model_id
        self.provider_config = primary.provider_config

    async def call_llm(
        self,
        messages: List[Dict[str, Any]],
        system_prompt: str,
        tools: Optional[List[Dict[str, Any]]],
        use_cache: bool = True,
        enable_thinking: Optional[bool] = None,
        thinking_budget_tokens: Optional[int] = None,
    ) -> Dict[str, Any]:
        primary_exc: BaseException | None = None
        try:
            return await asyncio.wait_for(
                self.primary.call_llm(
                    messages=messages,
                    system_prompt=system_prompt,
                    tools=tools,
                    use_cache=use_cache,
                    enable_thinking=enable_thinking,
                    thinking_budget_tokens=thinking_budget_tokens,
                ),
                timeout=self.PRIMARY_TIMEOUT_SECONDS,
            )
        except ContentPolicyViolationError:
            raise
        except Exception as exc:
            primary_exc = exc
            self.logger.warning(
                f"[FALLBACK] Primary LLM failed ({exc.__class__.__name__}: {str(exc)[:200]}), "
                "switching to fallback provider..."
            )

        try:
            result = await self.fallback.call_llm(
                messages=messages,
                system_prompt=system_prompt,
                tools=tools,
                use_cache=use_cache,
                enable_thinking=enable_thinking,
                thinking_budget_tokens=thinking_budget_tokens,
            )
            # Tag the response so callers (cost tracking) know the actual model.
            if self._fallback_model_alias:
                result["_actual_model_alias"] = self._fallback_model_alias
            return result
        except ContentPolicyViolationError:
            raise
        except Exception as fallback_exc:
            self.logger.warning(
                f"[FALLBACK] Fallback LLM also failed ({fallback_exc.__class__.__name__}: "
                f"{str(fallback_exc)[:200]}). Re-raising primary exception."
            )
            raise primary_exc from fallback_exc
