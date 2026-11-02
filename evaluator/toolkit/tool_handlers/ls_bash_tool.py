"""
LSBash tool for listing bash windows (Bash Sessions) under current task's bash session
"""
from typing import Dict, Any
from toolkit.tool_handlers.base_tool_handler import BaseToolHandler, ToolFailedError
from toolkit.tool_handlers.tool_categories import ToolName
import os

class LSBashToolHandler(BaseToolHandler):
    """Tool for listing Bash Sessions (bash windows) for this task"""

    @property
    def name(self) -> str:
        return ToolName.LS_BASH

    @property
    def display_result_type(self) -> str:
        return "text_result"

    @property
    def input_schema(self) -> Dict[str, Any]:
        # No parameters as per spec
        return {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        }

    async def execute_async(self, **kwargs) -> Dict[str, Any]:
        """List bash windows for the current task"""
        try:
            windows = await self.tool_state.bash_list_windows()

            if not windows:
                message = "No Bash Sessions found"
            else:
                # Create compact markdown table
                lines = [
                    "| # | Name | Cmd | PID | Path | Status |",
                    "|---|------|-----|-----|------|------|"
                ]

                for w in windows:
                    lines.append(
                        f"| {w.index} | {w.name} | {w.current_command} | {w.pane_pid} | {os.path.basename(w.current_path)} | {await self.tool_state.bash_status(w.name)} |"
                    )

                message = "\n".join(lines)

            return {
                'tool_success': True,
                'result': message,
                'display_result': {
                    'text': message
                }
            }
        except Exception as e:
            raise ToolFailedError(f"Error listing Bash Sessions: {str(e)}")


