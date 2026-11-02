"""
LSBash tool client for listing Bash Sessions (tmux sessions)
"""
from typing import Dict, Any
from ..base_tool import BaseTool
from ..tool_names import ToolName


class LSBashTool(BaseTool):
    """Tool client for listing tmux sessions under current task"""

    def __init__(self):
        super().__init__()

    @property
    def name(self) -> str:
        return ToolName.LS_BASH

    @property
    def input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
            "$schema": "http://json-schema.org/draft-07/schema#"
        }


