"""
Bash tool client for executing commands via remote tool server
"""
from typing import Dict, Any
from ..base_tool import BaseTool
from ..tool_names import ToolName


class KillBashTool(BaseTool):
    """Tool for killing background bash shells"""

    def __init__(self):
        super().__init__()

    @property
    def name(self) -> str:
        return ToolName.KILL_BASH
    
    @property
    def input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "session_id": {
                    "type": "string",
                    "description": "Bash session name (e.g., 'main', 'build', 'test'). Must contain only alphanumeric characters, dashes, and underscores. Note: 'main' session cannot be killed with end_session=True."
                },
                "end_session": {
                    "type": "boolean",
                    "description": "If true, kill the Bash Session. Otherwise send interrupt (C-c). Note: Cannot end 'main' session."
                }
            },
            "required": [
                "session_id"
            ],
            "additionalProperties": False,
            "$schema": "http://json-schema.org/draft-07/schema#"
        }
