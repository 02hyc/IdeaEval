"""
Write Tool - Writes files to the local filesystem
"""
import os
from typing import Dict, Any
from ..base_tool import BaseTool
from ..tool_names import ToolName


class WriteTool(BaseTool):
    """Tool for writing files to the local filesystem"""

    def __init__(self):
        super().__init__()

    @property
    def name(self) -> str:
        return ToolName.WRITE
    
    @property
    def input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "The absolute path to the file to write (must be absolute, not relative)"
                },
                "content": {
                    "type": "string",
                    "description": "The content to write to the file"
                }
            },
            "required": ["file_path", "content"],
            "additionalProperties": False
        }
