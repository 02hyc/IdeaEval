"""
Bash tool for executing commands in persistent shell sessions with multi-session support
"""
from typing import Dict, Any, Optional, List
from toolkit.tool_handlers.base_tool_handler import BaseToolHandler, ToolState, ToolFailedError
import logging
from toolkit.tool_handlers.tool_categories import ToolName
from toolkit.tool_handlers.bash_utils import normalize_bash_status, strip_trailing_prompt_lines
logger = logging.getLogger(__name__)

class BashToolHandler(BaseToolHandler):
    """Tool for executing bash commands in persistent shell sessions with multi-session support"""

    def __init__(self, tool_state: ToolState):
        super().__init__(tool_state=tool_state)
        self.requires_confirmation = True
        self.current_session_id: Optional[str] = None  # Track current session for interrupt
    
    @property
    def name(self) -> str:
        return ToolName.BASH
    
    @property
    def display_result_type(self) -> str:
        return "bash_result"

    @property
    def input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "The command to execute"
                },
                "executables": {
                    "type": "array",
                    "items": {
                        "type": "string"
                    },
                    "description": "List of primary executables or commands being executed, ordered by importance with THE MOST IMPORTANT command FIRST (e.g., ['git status', 'python', 'rm']). Extract the main executables from the command line and ignore auxiliary commands like 'cd', 'source', 'activate', 'export', 'echo', etc. CRITICAL: Place the most significant/primary command at index 0. Examples:\nInput: cd /tmp && ls -la\nOutput: ['ls']\n\nInput: source venv/bin/activate && python script.py && pytest\nOutput: ['python', 'pytest']\n\nInput: git add . && git commit -m \"msg\" && git push\nOutput: ['git push', 'git commit', 'git add']\n\nInput: export PATH=/usr/bin && npm install && npm run build\nOutput: ['npm run build', 'npm install']"
                },
                "wait": {
                    "type": "number",
                    "description": "Wait for a specific seconds (max 30). If command exceeds wait, it continues to run in background and current output is returned."
                },
                "description": {
                    "type": "string",
                    "description": "Clear, concise description of what this command does in 5-10 words. Examples:\nInput: ls\nOutput: Lists files in current directory\n\nInput: git status\nOutput: Shows working tree status\n\nInput: npm install\nOutput: Installs package dependencies\n\nInput: mkdir foo\nOutput: Creates directory 'foo'"
                },
                "session_id": {
                    "type": "string",
                    "description": "Bash session name (e.g., 'main', 'build', 'test'). If missing, uses 'main' session. You can specify custom names to run commands in different sessions. Name must contain only alphanumeric characters, dashes, and underscores (spaces and special characters will be replaced with dashes)."
                }
            },
            "required": [
                "command", "executables"
            ],
            "additionalProperties": False,
            "$schema": "http://json-schema.org/draft-07/schema#"
        }
    
    async def execute_async(self, command: str, executables: List[str], wait: Optional[float] = None, description: Optional[str] = None, session_id: Optional[str] = None) -> Dict[str, Any]:
        """Execute the bash command via tmux-backed session sessions."""
        # Track current session for interrupt handling
        self.current_session_id = session_id

        # Validate timeout (seconds)
        timeout = wait
        if timeout is not None:
            if timeout > 30:
                logger.error(f"Wait {wait}s exceeds maximum of 30s")
                raise ToolFailedError("Error: Wait cannot exceed 30s")
            if timeout <= 0:
                logger.error(f"Invalid wait {wait}s, must be positive")
                raise ToolFailedError("Error: Wait must be positive")
        else:
            timeout = 10.0  # Default per spec

        try:

            status = await self.tool_state.bash_status(session_id)
            if status == 'running':
                # system_reminder = f"Warning: A previous command was still running in session '{session_id}' when this new command was submitted. If this is not expected, you can use KillBash to interrupt the session."
                # Reject this command
                session_id = session_id if session_id else 'main'
                return {
                    'tool_success': False,
                    'result': f"Error: A previous command was still running in session '{session_id}' when this new command was submitted. You need to use KillBash to interrupt the session. Command rejected.",
                    'display_result': {
                        'output': f"Error: A previous command was still running in session '{session_id}' when this new command was submitted.",
                        'status': 'error',
                        'session_id': session_id
                    }
                }
            result = await self.tool_state.bash_run_command(command=command, session_id=session_id, timeout=timeout or 30.0)

            status = normalize_bash_status(result.get('status', 'running'))
            # Strip shell prompts so no-output commands are not mistaken for real output.
            output_text = strip_trailing_prompt_lines(result.get('output', ''))
            if output_text and len(output_text) > 30000:
                output_text = "... (output truncated - showing last 30000 characters)\n" + output_text[-30000:]

            display_output_text = "" + output_text if output_text else ""

            if output_text and status == 'running':
                output_text += '\n\n<system-reminder>\nCommand is still running. If you need to check the output, use BashOutput or LSBash.\n</system-reminder>'
            elif not output_text and status == 'running':
                output_text = (
                    "<system-reminder>\n"
                    "Command is still running and has produced no output yet. "
                    "If you need to check the output, use BashOutput or LSBash.\n"
                    "</system-reminder>"
                )
            elif not output_text and status == 'completed':
                output_text = "Command completed with no output."

            self.current_session_id = None

            return {
                'tool_success': True if status != 'error' else False,
                'result': output_text if output_text else "Command failed without output.",
                'display_result': {
                    'output': display_output_text if display_output_text else "",
                    'status': status,
                    'session_id': result.get('session_id')
                }
            }

        except Exception as e:
            logger.error(f"Error executing command '{command}': {str(e)}", exc_info=True)
            raise ToolFailedError(f"Error executing command {command}: {type(e)}: {str(e)}")

    async def handle_interrupt(self):
        """Send interrupt signal (Ctrl+C) to the bash session without closing it."""
        await super().handle_interrupt()

        if self.current_session_id:
            logger.info(f"Sending interrupt signal to Bash session: {self.current_session_id}")
            try:
                # Send interrupt signal (Ctrl+C) but don't end the session
                result = await self.tool_state.bash_kill(
                    session_id=self.current_session_id,
                    end_session=False  # Keep session alive
                )
                if result.get('ok'):
                    logger.info(f"Interrupt signal sent successfully to session {self.current_session_id}")
                else:
                    logger.warning(f"Failed to send interrupt: {result.get('message')}")
            except Exception as e:
                logger.error(f"Error sending interrupt to Bash session: {e}", exc_info=True)
        else:
            logger.warning("No current session to interrupt")
