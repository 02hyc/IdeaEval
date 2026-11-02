"""
TodoWrite tool for managing structured task lists during coding sessions
"""
import json
from typing import Dict, Any, List, Optional
from ..base_tool import BaseTool
from ..tool_names import ToolName


class TodoWriteTool(BaseTool):
    """Tool for creating and managing structured task lists"""

    def __init__(self):
        super().__init__()
        self._current_todos = []

    @property
    def name(self) -> str:
        return ToolName.TODO_WRITE
    
    @property
    def input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "todos": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "content": {
                                "type": "string",
                                "minLength": 1
                            },
                            "status": {
                                "type": "string",
                                "enum": ["pending", "in_progress", "completed"]
                            },
                            "id": {
                                "type": "string"
                            }
                        },
                        "required": ["content", "status", "id"],
                        "additionalProperties": False
                    },
                    "description": "The updated todo list"
                }
            },
            "required": ["todos"],
            "additionalProperties": False
        }