"""
WebSearch tool schema definition.
"""
from typing import Dict, Any
from ..base_tool import BaseTool
from ..tool_names import ToolName


class WebSearchTool(BaseTool):
    """Tool for searching the web using Tavily API"""

    def __init__(self):
        super().__init__()

    @property
    def name(self) -> str:
        return ToolName.WEB_SEARCH
    
    @property
    def input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "minLength": 2,
                    "description": "The search query to use"
                }
            },
            "required": ["query"],
            "additionalProperties": False
        }
