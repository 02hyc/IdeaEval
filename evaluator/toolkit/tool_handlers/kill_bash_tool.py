"""
KillBash tool for terminating background bash sessions
"""
from typing import Dict, Any, Optional
from toolkit.tool_handlers.base_tool_handler import BaseToolHandler, ToolState, ToolFailedError
from toolkit.tool_handlers.tool_categories import ToolName


class KillBashToolHandler(BaseToolHandler):
    """Tool for terminating background bash sessions"""
    
    def __init__(self, tool_state: ToolState):
        super().__init__(tool_state=tool_state)
        self.requires_confirmation = False
    
    @property
    def name(self) -> str:
        return ToolName.KILL_BASH
    
    @property
    def display_result_type(self) -> str:
        return "text_result"

    @property
    def input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "session_id": {
                    "type": "string",
                    "description": "Bash session window name (e.g., 'main', 'build', 'test'). Must contain only alphanumeric characters, dashes, and underscores. Note: 'main' window cannot be killed with end_session=True."
                },
                "end_session": {
                    "type": "boolean",
                    "description": "If true, kill the Bash Session (window). Otherwise send interrupt (C-c). Note: Cannot end 'main' session."
                }
            },
            "required": [
                "session_id"
            ],
            "additionalProperties": False,
            "$schema": "http://json-schema.org/draft-07/schema#"
        }
    
    async def execute_async(self, session_id: str, end_session: Optional[bool] = False) -> Dict[str, Any]:
        """Terminate or interrupt a bash session window"""
        try:
            # Validate that the session_id exists
            windows = await self.tool_state.bash_list_windows()
            window_names = [w.name for w in windows]

            if session_id not in window_names:
                raise ToolFailedError(f"Bash session not found: {session_id}. Available sessions: {', '.join(window_names) if window_names else 'none'}")

            result = await self.tool_state.bash_kill(session_id, bool(end_session))
            ok, msg = result.get('ok', False), result.get('message', '')
            if not ok:
                raise ToolFailedError(f"Error: {msg}")
            output, status = await self.tool_state.bash_read_output(session_id)
            if output:
                result = f"{output}\n\n<system-reminder>\n{msg}\n</system-reminder>"
            else:
                result = msg
            return {
                'tool_success': True,
                'result': result,
                'display_result': {
                    'text': msg
                }
            }
        except Exception as e:
            raise ToolFailedError(f"Error terminating {session_id}: {str(e)}")