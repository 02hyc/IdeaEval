"""
Bash tool client for executing commands via remote tool server
"""
from typing import Dict, Any
from ..base_tool import BaseTool
from ..tool_names import ToolName


class BashOutputTool(BaseTool):
    """Tool for reading output from background bash shells"""

    def __init__(self):
        super().__init__()

    @property
    def name(self) -> str:
        return ToolName.BASH_OUTPUT
    
    @property
    def input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "session_id": {
                    "type": "string",
                    "description": "Bash session name (e.g., 'main', 'build', 'test'). Must contain only alphanumeric characters, dashes, and underscores."
                },
                "filter": {
                    "type": "string",
                    "description": "Optional regular expression to filter the output lines. Only lines matching this regex will be included in the result. Any lines that do not match will no longer be available to read."
                },
                "wait": {
                    "type": "number",
                    "description": "Optional wait time in seconds before checking output. If specified, will wait for this many seconds before reading and returning the output. Useful for giving commands time to produce output."
                }
            },
            "required": [
                "session_id"
            ],
            "additionalProperties": False,
            "$schema": "http://json-schema.org/draft-07/schema#"
        }
