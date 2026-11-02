"""
Hook executor that manages and runs registered hooks.
"""

from typing import List, Dict, Any, Optional

from utils.logger import Logger
from .base import Hook, HookContext


class HookExecutor:
    """
    Executes registered hooks in priority order.
    
    Hooks are sorted by priority (lower first) and executed sequentially.
    Each hook receives the context from the previous hook.
    """
    
    def __init__(self):
        self._hooks: List[Hook] = []
        self.logger = Logger()
    
    def register(self, hook: Hook) -> None:
        """
        Register a hook.
        
        Hooks are automatically sorted by priority after registration.
        
        Args:
            hook: The hook to register
        """
        self._hooks.append(hook)
        self._hooks.sort(key=lambda h: h.priority)
        self.logger.info(f"Registered hook: {hook.name} (priority: {hook.priority})")
    
    def unregister(self, hook_name: str) -> bool:
        """
        Unregister a hook by name.
        
        Args:
            hook_name: Name of the hook to remove
            
        Returns:
            True if hook was found and removed, False otherwise
        """
        for i, hook in enumerate(self._hooks):
            if hook.name == hook_name:
                self._hooks.pop(i)
                self.logger.info(f"Unregistered hook: {hook_name}")
                return True
        return False
    
    def list_hooks(self) -> List[str]:
        """Return list of registered hook names in execution order"""
        return [hook.name for hook in self._hooks]
    
    async def run_pre_hooks(self, context: HookContext) -> HookContext:
        """
        Run all pre_run hooks in priority order.
        
        Each hook receives the context from the previous hook.
        If any hook raises an exception, execution stops and the
        exception propagates.
        
        Args:
            context: The initial hook context
            
        Returns:
            The final context after all hooks have run
        """
        for hook in self._hooks:
            try:
                context = await hook.pre_run(context)
            except Exception as e:
                self.logger.error(f"Hook {hook.name} pre_run failed: {e}")
                raise
        return context
    
    async def run_post_hooks(self, context: HookContext, response: Optional[Dict[str, Any]]) -> None:
        """
        Run all post_run hooks in priority order.
        
        Post hooks cannot modify the response. Exceptions are logged
        but do not stop execution of remaining hooks.
        
        Args:
            context: The hook context
            response: The agent's response
        """
        for hook in self._hooks:
            try:
                await hook.post_run(context, response)
            except Exception as e:
                self.logger.error(f"Hook {hook.name} post_run failed: {e}")
                # Continue executing other post hooks even if one fails

    async def run_content_filter_hooks(self, context: HookContext, error: Exception) -> bool:
        """
        Run hooks that can recover from content-filter blocks.

        Returns True as soon as one hook handles the error and requests a retry.
        Exceptions are logged and remaining hooks are still offered the error.
        """
        for hook in self._hooks:
            try:
                if await hook.on_content_filter(context, error):
                    return True
            except Exception as e:
                self.logger.error(f"Hook {hook.name} on_content_filter failed: {e}")
        return False

    async def run_text_response_hooks(self, context: HookContext, response: Optional[Dict[str, Any]]) -> bool:
        """
        Run hooks for text-only responses.

        Returns True as soon as one hook requests another LLM turn.
        Exceptions are logged and remaining hooks are still offered the response.
        """
        for hook in self._hooks:
            try:
                if await hook.on_text_response_without_tools(context, response):
                    return True
            except Exception as e:
                self.logger.error(f"Hook {hook.name} on_text_response_without_tools failed: {e}")
        return False
