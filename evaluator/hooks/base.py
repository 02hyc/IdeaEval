"""
Base classes for the hook system.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional

from message_service.message import TaskMessage


@dataclass
class HookContext:
    """
    Context passed through hooks.
    
    Hooks can read and modify this context. The modified context
    is passed to subsequent hooks and eventually to agent.run().
    """
    task_id: str
    agent_name: str
    messages: List[TaskMessage]
    system_prompt: str
    tools: List[Dict[str, Any]]
    max_context_tokens: int = 150000  # Agent's max context token limit
    enable_thinking: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def set_metadata(self, key: str, value: Any) -> None:
        """Set a metadata value for hooks to share data"""
        self.metadata[key] = value
    
    def get_metadata(self, key: str, default: Any = None) -> Any:
        """Get a metadata value"""
        return self.metadata.get(key, default)


class Hook(ABC):
    """
    Base class for all hooks.
    
    Hooks are executed in priority order (lower priority runs first).
    Override pre_run() to modify context before agent.run().
    Override post_run() to perform actions after agent.run().
    """
    
    @property
    @abstractmethod
    def name(self) -> str:
        """Hook name for logging and identification"""
        pass
    
    @property
    def priority(self) -> int:
        """
        Hook priority. Lower values run first.
        
        Suggested ranges:
        - 0-20: Early hooks (context modification, validation)
        - 50-70: Middle hooks (logging, metrics)
        - 80-100: Late hooks (cleanup)
        
        Default: 100
        """
        return 100
    
    async def pre_run(self, context: HookContext) -> HookContext:
        """
        Called before agent.run().
        
        Can modify the context (messages, metadata, etc.).
        Must return the (possibly modified) context.
        
        Args:
            context: The hook context containing messages and metadata
            
        Returns:
            The modified context to pass to the next hook or agent
        """
        return context
    
    async def post_run(self, context: HookContext, response: Optional[Dict[str, Any]]) -> None:
        """
        Called after agent.run().
        
        Useful for logging, metrics collection, cleanup, etc.
        Cannot modify the response.
        
        Args:
            context: The hook context (may have been modified by pre_run)
            response: The agent's response (or None if agent failed)
        """
        pass

    async def on_content_filter(self, context: HookContext, error: Exception) -> bool:
        """
        Called when an LLM call is blocked by a content filter.

        Hooks may persist a recovery message or update state, then return True
        to indicate the caller should retry the LLM call once with refreshed
        context. Return False to leave the error unhandled.
        """
        return False

    async def on_text_response_without_tools(
        self,
        context: HookContext,
        response: Optional[Dict[str, Any]],
    ) -> bool:
        """
        Called when an agent returns text without tool calls.

        Hooks may persist a user notification and return True to request another
        LLM turn instead of accepting the text response as final.
        """
        return False
