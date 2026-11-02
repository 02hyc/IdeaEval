"""
Tool Categories Configuration for Interrupt Handling
====================================================

This module defines the categorization of all remote tools for interrupt handling.

Tool Categories:
- 'edit': Edit-type tools (Edit, MultiEdit, Write) - wait for completion when interrupted
- 'readonly': Read-only tools (Read, Grep, Glob, LSBashTool) - directly interrupt
- 'bash': Bash execution tool - send interrupt signal but keep session alive
- 'bash_output': BashOutput tool - end execution without updating read pointer
- 'kill_bash': KillBash tool - wait for completion when interrupted
"""

from enum import Enum


class ToolName(str, Enum):
    """Enum containing all available tool names in the system"""

    # File operations
    WRITE = "Write"
    EDIT = "Edit"
    VIEW = "View"
    MULTI_EDIT = "MultiEdit"

    # Search and navigation
    GLOB = "Glob"
    GREP = "Grep"

    # Bash operations
    BASH = "Bash"
    BASH_OUTPUT = "BashOutput"
    KILL_BASH = "KillBash"
    LS_BASH = "LSBash"
    
    # Web operations
    WEB_FETCH = "WebFetch"
    WEB_SEARCH = "WebSearch"
    
    # Scraping operations
    SCRAPER = "Scraper"

    # Proposal evaluation
    EVALUATE_PROPOSAL = "EvaluateProposal"
    # Lightweight peer discussion
    DISCUSS_WITH_PEER = "DiscussWithPeer"

    # Proposal finalization
    FINALIZE_PROPOSAL = "FinalizeProposal"
    
    # Task management
    TODO_WRITE = "TodoWrite"
    
    # Communication
    SEND_MESSAGE_TO_USER = "SendMessageToUser"
    
    # Task rejection (input guardrail)
    REJECT_TASK = "RejectTask"

    def __str__(self) -> str:
        return self.value


from typing import Dict

# Tool name to category mapping
TOOL_CATEGORIES: Dict[str, str] = {
    # Edit-type tools - wait for completion
    ToolName.EDIT: 'edit',
    ToolName.MULTI_EDIT: 'edit',
    ToolName.WRITE: 'edit',

    # Read-only tools - direct interrupt
    ToolName.VIEW: 'readonly',
    ToolName.GREP: 'readonly',
    ToolName.GLOB: 'readonly',
    ToolName.LS_BASH: 'readonly',
    ToolName.WEB_FETCH: 'readonly',
    ToolName.WEB_SEARCH: 'readonly',
    ToolName.SCRAPER: 'readonly',
    ToolName.TODO_WRITE: 'readonly',
    ToolName.EVALUATE_PROPOSAL: 'readonly',
    ToolName.DISCUSS_WITH_PEER: 'readonly',
    ToolName.FINALIZE_PROPOSAL: 'edit',
    
    # Communication tools - direct interrupt
    ToolName.SEND_MESSAGE_TO_USER: 'readonly',
    
    # Task rejection - readonly (just sets a flag)
    ToolName.REJECT_TASK: 'readonly',

    # Bash execution - send interrupt signal, keep session
    ToolName.BASH: 'bash',

    # BashOutput - end execution, don't update pointer
    ToolName.BASH_OUTPUT: 'bash_output',

    # KillBash - wait for completion
    ToolName.KILL_BASH: 'kill_bash',
}


def get_tool_category(tool_name: str) -> str:
    """Get the category for a given tool name.

    Args:
        tool_name: Name of the tool

    Returns:
        Category string, defaults to 'readonly' if tool not found
    """
    return TOOL_CATEGORIES.get(tool_name, 'readonly')


def is_waitable_tool(tool_name: str) -> bool:
    """Check if a tool should wait for completion when interrupted.

    Args:
        tool_name: Name of the tool

    Returns:
        True if tool should wait for completion (edit, kill_bash), False otherwise
    """
    category = get_tool_category(tool_name)
    return category in ('edit', 'kill_bash')


def is_interruptible_tool(tool_name: str) -> bool:
    """Check if a tool can be directly interrupted.

    Args:
        tool_name: Name of the tool

    Returns:
        True if tool can be directly interrupted (readonly, bash_output), False otherwise
    """
    category = get_tool_category(tool_name)
    return category in ('readonly', 'bash_output', 'bash', 'edit')
