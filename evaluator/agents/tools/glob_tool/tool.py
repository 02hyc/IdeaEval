"""
Glob tool for fast file pattern matching
"""
import os
import glob
from typing import Dict, Any
from ..base_tool import BaseTool
from ..tool_names import ToolName


class GlobTool(BaseTool):
    """Tool for fast file pattern matching using glob patterns"""

    def __init__(self):
        super().__init__()

    @property
    def name(self) -> str:
        return ToolName.GLOB
    
    @property
    def input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "The glob pattern to match files against"
                },
                "path": {
                    "type": "string",
                    "description": "The directory to search in. If not specified, the current working directory will be used. IMPORTANT: Omit this field to use the default directory. DO NOT enter \"undefined\" or \"null\" - simply omit it for the default behavior. Must be a valid absolute directory path if provided."
                }
            },
            "required": ["pattern"],
            "additionalProperties": False
        }
