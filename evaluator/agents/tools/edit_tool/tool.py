"""
Edit Tool - Performs exact string replacements in files
"""
import os
import subprocess
from typing import Dict, Any
from ..base_tool import BaseTool
from ..tool_names import ToolName


class EditTool(BaseTool):
    """Tool for performing exact string replacements in files"""

    def __init__(self):
        super().__init__()

    @property
    def name(self) -> str:
        return ToolName.EDIT
    
    @property
    def input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "The absolute path to the file to modify"
                },
                "old_string": {
                    "type": "string",
                    "description": "The text to replace"
                },
                "new_string": {
                    "type": "string", 
                    "description": "The text to replace it with (must be different from old_string)"
                },
                "replace_all": {
                    "type": "boolean",
                    "default": False,
                    "description": "Replace all occurences of old_string (default false)"
                }
            },
            "required": ["file_path", "old_string", "new_string"],
            "additionalProperties": False
        }
