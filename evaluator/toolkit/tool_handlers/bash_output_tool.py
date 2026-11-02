"""
BashOutput tool for retrieving output from background bash sessions
"""
import asyncio
from typing import Dict, Any, Optional
from datetime import datetime
from toolkit.tool_handlers.base_tool_handler import BaseToolHandler, ToolState, ToolFailedError
import re
from toolkit.tool_handlers.tool_categories import ToolName
from toolkit.tool_handlers.bash_utils import normalize_bash_status, strip_trailing_prompt_lines

class BashOutputToolHandler(BaseToolHandler):
    """Tool for retrieving output from background bash sessions"""
    
    def __init__(self, tool_state: ToolState):
        super().__init__(tool_state=tool_state)
        self.requires_confirmation = False
        self.current_read_pointer = None
        self.current_session_id = None
        
    @property
    def name(self) -> str:
        return ToolName.BASH_OUTPUT

    @property
    def display_result_type(self) -> str:
        return "bash_result"

    @property
    def input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "session_id": {
                    "type": "string",
                    "description": "Bash session window name (e.g., 'main', 'build', 'test'). Must contain only alphanumeric characters, dashes, and underscores."
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

    async def execute_async(self, session_id: str, filter: Optional[str] = None, wait: Optional[float] = None) -> Dict[str, Any]:
        """Retrieve output from a bash session window"""
        try:
            # Validate that the session_id exists
            windows = await self.tool_state.bash_list_windows()
            window_names = [w.name for w in windows]

            if session_id not in window_names:
                raise ToolFailedError(f"Bash session not found: {session_id}. Available sessions: {', '.join(window_names) if window_names else 'none'}")

            self.current_session_id = session_id
            self.current_read_pointer = self.tool_state.get_bash_manager().get_read_pointer(session_id)
            # If wait is specified, wait for that many seconds before checking output
            if wait is not None and wait > 0:
                while wait > 0:
                    status = await self.tool_state.bash_status(session_id)
                    if status != "running":
                        break
                    await asyncio.sleep(1)
                    wait -= 1

            full_output, status = await self.tool_state.bash_read_output(session_id, incremental=True)
            status = normalize_bash_status(status)
            latest_status = normalize_bash_status(await self.tool_state.bash_status(session_id))
            if latest_status:
                status = latest_status

            full_output = strip_trailing_prompt_lines(full_output)
            # Apply filter if provided
            if filter:
                output_lines = full_output.split('\n')
                try:
                    pattern = re.compile(filter)
                    output_lines = [line for line in output_lines if pattern.search(line)]
                except re.error:
                    output_lines = []
                full_output = '\n'.join(output_lines).strip()

            timestamp = datetime.now().isoformat()

            result = self._format_bash_output(
                session_id,
                full_output,
                status,
                timestamp
            )
            return {
                'tool_success': True,
                'result': result,
                'display_result': {
                    'output': full_output if full_output else "",
                    'status': status
                }
            }

        except Exception as e:
            raise ToolFailedError(f"Error retrieving bash output: {str(e)}")

    async def handle_interrupt(self):
        """Handle interrupt request for this tool."""
        await super().handle_interrupt()
        if self.current_read_pointer is not None:
            self.tool_state.get_bash_manager().set_read_pointer(window_name=self.current_session_id, read_pointer=self.current_read_pointer)
    
    def _format_bash_output(self, bash_id: str, output: str, status: str, timestamp: str) -> str:
        """Format successful bash output in the expected format"""

        result = ""

        result += output if output else "(no new output)"

        result += (
            f"\n\n<timestamp>{timestamp}</timestamp>\n"
            f"<system-reminder>\n"
        )

        if status == "running":
            result += f"Command is still running. If you need to check the output, use BashOutput or LSBash."
        elif status == "completed":
            if output:
                result += "Command is completed."
            else:
                result += "Command is completed. No new output was produced since the last check."
        elif status == "error":
            result += "Command failed."
        else:
            result += f"Command status: {status}."

        if status == "running" and output == "":
            result += f"\n\nNo output received yet. If the command seems unresponsive or is taking longer than expected, use KillBash to interrupt it."

        result += "\n</system-reminder>"

        return result
