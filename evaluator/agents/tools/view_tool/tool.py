"""
View Tool - Supports viewing text, images, and directory listings
"""
from typing import Dict, Any
from ..base_tool import BaseTool
from ..tool_names import ToolName


class ViewTool(BaseTool):
    """Tool for viewing files, images, and directories"""

    def __init__(self):
        super().__init__()

    @property
    def name(self) -> str:
        return ToolName.VIEW
    
    @property
    def input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path to file or directory. Relative paths are resolved against the workspace root."
                },
                "view_range": {
                    "type": "array",
                    "description": "Line range for text files in format [start_line, end_line] where lines are indexed starting at 1. You can use [start_line, -1] to view from start_line to the end. If not provided, the entire file is displayed (truncating from the middle if it exceeds 16,000 characters). WARNING: Do NOT use view_range for SKILL.md files—always read skills in full to get complete context. Only applies to text files; for directories and images it is ignored.",
                    "items": {
                        "type": "integer"
                    },
                    "minItems": 2,
                    "maxItems": 2
                },
                "description": {
                    "type": "string",
                    "description": "Why I need to view this"
                }
            },
            "required": ["path", "description"],
            "additionalProperties": False
        }

