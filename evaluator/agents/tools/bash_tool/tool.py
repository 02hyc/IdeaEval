"""
Bash tool client for executing commands via remote tool server
"""
from typing import Dict, Any
from ..base_tool import BaseTool
from ..tool_names import ToolName


class BashTool(BaseTool):
    """Tool for executing bash commands"""

    def __init__(self):
        super().__init__()

    @property
    def name(self) -> str:
        return ToolName.BASH
    
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
                    "description": "List of primary executables or commands, ordered by importance with THE MOST IMPORTANT command FIRST (Command that affect the system state) (e.g., ['git add', 'python', 'rm']). Extract the main executables from the command line and ignore auxiliary commands like 'cd', 'source', 'activate', 'export', 'echo', etc. CRITICAL: Place the most significant/primary command at index 0. Examples:\nInput: cd /tmp && ls -ahl\nOutput: ['ls']\n\nInput: source venv/bin/activate && python script.py && pytest\nOutput: ['python', 'pytest']\n\nInput: git add . && git commit -m \"msg\"\nOutput: ['git commit', 'git add']\n\nInput: export PATH=/usr/bin && npm install && npm run build\nOutput: ['npm run', 'npm build', 'npm install'].\n\nIMPORTANT: Always return executables as a List.\nIMPORTANT: Always include subcommands as well, if any. Example:\ngit -> git add, git commit, git push\nnpm -> npm install, npm build\npip -> pip install, pip uninstall\nconda -> conda activate, conda create, conda install."
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
