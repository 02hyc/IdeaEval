"""
Base class for remote tool services - mirrors alab BaseTool interface
"""
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional
import os
import time
import logging
from toolkit.tool_handlers.tool_state import ToolState
from toolkit.tool_handlers.tool_categories import get_tool_category, is_interruptible_tool

logger = logging.getLogger(__name__)

class ToolFailedError(Exception):
    pass

class BaseToolHandler(ABC):
    """Abstract base class for all remote tool services - matches alab BaseTool interface"""

    def __init__(self, tool_state: ToolState):
        self.tool_state = tool_state
        self.requires_confirmation = False  # Tools can override this
        self.interrupt_requested = False  # Flag to track interrupt requests
        # Store backup data for file restoration on interrupt
        # Format: {file_path: {'content': original_content, 'was_created': bool}}
        self._file_backup = {}
        logger.info(f"Initialized {self.name} handler")
    
    @property
    @abstractmethod
    def name(self) -> str:
        """Tool name"""
        pass
    
    @property
    @abstractmethod
    def display_result_type(self) -> str:
        """Display result type"""
        pass

    @property
    @abstractmethod
    def input_schema(self) -> Dict[str, Any]:
        """Input schema"""
        pass
    
    @abstractmethod
    async def execute_async(self, **kwargs) -> Dict[str, Any]:
        """Execute the tool asynchronously - matches BaseTool interface"""
        pass

    def validate_input(self, **kwargs) -> bool:
        """Validate input parameters against schema"""
        # Basic validation - can be extended
        required_fields = self.input_schema.get("required", [])
        for field in required_fields:
            if field not in kwargs:
                raise ToolFailedError(f"Error: Missing required parameter {field}")
        return True
    
    async def execute(self, params: Dict[str, Any]) -> Dict[str, Any]:
        # Clear any previous backups before starting new execution
        self._file_backup.clear()
        
        try:
            self.validate_input(**params)
            result = await self.execute_async(**params)
            outer = {
                "success": result['tool_success'],    # this is the tool_success
                "result": result['result'],
                "display_result": {
                    'type': self.display_result_type,
                    'data': result['display_result'],
                    'tool_success': result['tool_success'],
                    'error': None,
                },
                "error": None,
                "error_type": None,
            }
            # Forward sub-agent token usage so Toolkit.handle_tool_use can
            # propagate it for cost tracking.
            for key in ("sub_agent_token_infos", "sub_agent_token_info", "sub_agent_raw_token_usages"):
                if key in result:
                    outer[key] = result[key]
            return outer
        except ToolFailedError as e:
            return {
                "success": False,    # this is the tool_success
                "result": str(e),
                "display_result": {
                    'type': self.display_result_type,
                    'data': {},
                    'tool_success': False,
                    'error': str(e),
                },
                "error": None,
                "error_type": None,
            }
        except Exception as e:
            # Catch-all: prevent unhandled exceptions in any tool
            # (especially sub-agent tools like SummarizePaper,
            # EvaluateProposal, DiscussWithPeer) from propagating up
            # and killing the lead agent task.
            logger.error(f"[{self.name}] Unhandled exception in execute: {e}", exc_info=True)
            error_msg = f"{self.name} failed: {e}"
            return {
                "success": False,
                "result": error_msg,
                "display_result": {
                    'type': self.display_result_type,
                    'data': {},
                    'tool_success': False,
                    'error': error_msg,
                },
                "error": None,
                "error_type": None,
            }
        
    def is_file_read(self, file_path: str) -> bool:
        """Check if a file has been read"""
        return self.tool_state.is_file_read(file_path)
    
    def is_file_read_fresh(self, file_path: str) -> bool:
        """Check if a file has been read and the read is still fresh (file hasn't been modified since read)"""
        return self.tool_state.is_file_read_fresh(file_path)
    
    def mark_file_as_read(self, file_path: str) -> None:
        """Mark a file as having been read"""
        self.tool_state.mark_file_as_read(file_path)

    @property
    def tool_category(self) -> str:
        """Tool category for interrupt handling.

        Categories are defined in tool_categories.py:
        - 'edit': Edit-type tools (Edit, MultiEdit, Write) - wait for completion
        - 'readonly': Read-only tools (Read, Grep, Glob, LSTool) - direct interrupt
        - 'bash': Bash execution tool - send interrupt signal but keep session
        - 'bash_output': BashOutput tool - end execution without updating read pointer
        - 'kill_bash': KillBash tool - wait for completion

        Returns the category based on tool name from tool_categories.py
        """
        return get_tool_category(self.name)

    def backup_file_state(self, file_path: str, content: str, was_created: bool = False):
        """Backup file state before modification.
        
        Args:
            file_path: Path to the file being modified
            content: Original content of the file
            was_created: True if the file was created by this tool (didn't exist before)
        """
        self._file_backup[file_path] = {
            'content': content,
            'was_created': was_created
        }
        logger.debug(f"Backed up file state for: {file_path} (was_created={was_created})")
    
    def restore_file_state(self):
        """Restore backed up file states.
        
        This is called when a tool is interrupted to restore files to their
        original state before the tool execution. If a file was created by
        the tool, it will be deleted instead of restored.
        """
        for file_path, backup_info in self._file_backup.items():
            try:
                was_created = backup_info['was_created']
                
                if was_created:
                    # File was created by this tool, delete it
                    if os.path.exists(file_path):
                        os.remove(file_path)
                        logger.info(f"Deleted newly created file: {file_path}")
                else:
                    # File existed before, restore its content
                    original_content = backup_info['content']
                    with open(file_path, 'w', encoding='utf-8') as f:
                        f.write(original_content)
                    logger.info(f"Restored file state for: {file_path}")
            except Exception as e:
                logger.error(f"Failed to restore/delete file {file_path}: {e}")
        self._file_backup.clear()

    async def handle_interrupt(self):
        """Handle interrupt request for this tool.

        This is a hook method that can be overridden by subclasses to implement
        custom interrupt behavior. The default implementation sets the
        interrupt flag and restores file state if any files were backed up.
        """
        self.interrupt_requested = True
        logger.info(f"Interrupt requested for tool: {self.name}")
        
        # Restore file state if we have backups (for file editing tools)
        if self._file_backup:
            logger.info(f"Restoring {len(self._file_backup)} file(s) due to interrupt")
            self.restore_file_state()

    async def is_interruptible(self) -> bool:
        """Check if the tool can be interrupted."""
        return is_interruptible_tool(self.name)
    