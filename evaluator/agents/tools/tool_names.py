"""
Tool Names Enum - Centralized registry of all tool names for easy maintenance
"""
from enum import Enum


class ToolName(str, Enum):
    """Enum containing all available tool names in the system"""

    # File operations
    VIEW = "View"
    WRITE = "Write"
    EDIT = "Edit"
    # MULTI_EDIT = "MultiEdit"

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

    # Memory and state management
    TODO_WRITE = "TodoWrite"

    # Plan management
    UPDATE_PLAN = "UpdatePlan"

    def __str__(self) -> str:
        """Return the string value of the enum"""
        return self.value
