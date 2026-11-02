"""
Glob tool for fast file pattern matching.

WARNING: Python's stdlib glob.glob() is fully synchronous and walks the entire
directory tree depth-first when given a recursive pattern. On a network
filesystem (e.g. a network mount) or under a deep nested workspace, this can take
many seconds-to-minutes per call. Because we have an asyncio event loop with
many concurrent agent tasks sharing the same loop, a sync glob on the main
thread blocks ALL other coroutines (LLM responses queue up unread, agents
hang indefinitely — this was the 2026-05-23 multi-hour freeze at c=20).

This module wraps the sync work in asyncio.to_thread() so the event loop
keeps spinning while the OS does the directory descent. It also uses
glob's `root_dir` parameter (Python 3.10+) instead of os.chdir() — chdir
is process-global and would race between concurrent agents.
"""
import asyncio
import os
import glob
from typing import Dict, Any, Optional
from toolkit.tool_handlers.base_tool_handler import BaseToolHandler, ToolState, ToolFailedError
from toolkit.tool_handlers.tool_categories import ToolName

# Cap results to prevent unbounded output. On a deep tree with `**` patterns,
# stdlib glob can return tens of thousands of paths and stat() each — both of
# which add minutes of wall time on a network FS.
_MAX_GLOB_RESULTS = 1000


def _glob_and_stat(search_dir: str, pattern: str, max_results: int) -> tuple[list[str], bool]:
    """Run glob + isfile + getmtime sort entirely on a worker thread.

    Returns (file_matches, truncated_flag). truncated=True when the raw match
    set exceeded max_results — we stop after max_results to avoid stat'ing
    tens of thousands of paths on a network filesystem.

    Uses glob.iglob with root_dir (Python 3.10+) instead of os.chdir to avoid
    the process-wide cwd race when many agents are globbing concurrently.
    """
    file_matches: list[str] = []
    truncated = False
    # iglob is lazy: we can stop once we hit max_results without forcing the
    # full directory walk to complete. This is critical on `**` patterns over
    # deep trees.
    for raw in glob.iglob(pattern, recursive=True, root_dir=search_dir):
        abs_path = raw if os.path.isabs(raw) else os.path.join(search_dir, raw)
        try:
            if os.path.isfile(abs_path):
                file_matches.append(os.path.abspath(abs_path))
                if len(file_matches) >= max_results:
                    truncated = True
                    break
        except OSError:
            # File may have been removed mid-walk; skip.
            continue
    # Sort by mtime, newest first. Each getmtime is a stat() — on a network
    # FS these can dominate runtime, but with the result cap we've bounded
    # the total cost.
    file_matches.sort(
        key=lambda p: os.path.getmtime(p) if os.path.exists(p) else 0,
        reverse=True,
    )
    return file_matches, truncated


class GlobToolHandler(BaseToolHandler):
    """Tool for fast file pattern matching using glob patterns"""

    @property
    def name(self) -> str:
        return ToolName.GLOB

    @property
    def display_result_type(self) -> str:
        return "text_result"

    @property
    def input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "The glob pattern to match files against"
                },
                "path": {
                    "type": "string",
                    "description": "The directory to search in. If not specified, the current working directory will be used. IMPORTANT: Omit this field to use the default directory. DO NOT enter \"undefined\" or \"null\" - simply omit it for the default behavior. Must be a valid absolute directory path if provided."
                }
            },
            "required": ["pattern"],
            "additionalProperties": False
        }

    async def execute_async(self, pattern: str, path: Optional[str] = None) -> Dict[str, Any]:
        """Execute the Glob tool"""
        # Save current working directory for display methods
        # This is captured during execute_async when the working directory is correct
        self._execution_working_dir = self.tool_state.get_working_dir()

        if not pattern.strip():
            raise ToolFailedError("Error: pattern parameter cannot be empty")

        # Determine search directory. Relative paths must be resolved against
        # the agent workspace, not the runner process cwd.
        if path:
            expanded = os.path.expanduser(path)
            search_dir = (
                expanded
                if os.path.isabs(expanded)
                else os.path.abspath(os.path.join(self._execution_working_dir, expanded))
            )
        else:
            search_dir = self._execution_working_dir

        if not os.path.exists(search_dir):
            raise ToolFailedError(f"Error: Path does not exist: {search_dir}")

        if not os.path.isdir(search_dir):
            raise ToolFailedError(f"Error: Path is not a directory: {search_dir}")

        try:
            # Offload the entire sync glob + stat workload to a worker thread.
            # The sync stdlib glob can take many seconds on a network FS; without
            # this, every concurrent agent on the same event loop blocks while
            # one agent is descending a deep tree.
            file_matches, truncated = await asyncio.to_thread(
                _glob_and_stat, search_dir, pattern, _MAX_GLOB_RESULTS
            )

            if not file_matches:
                agent_result = f"No files found matching pattern '{pattern}' in directory '{search_dir}'"
                return {
                    'tool_success': True,
                    'result': agent_result.strip(),
                    'display_result': {
                        'text': 'No files found'
                    }
                }

            # Format results for agent
            header = f"Found {len(file_matches)} files matching pattern '{pattern}' in '{search_dir}'"
            if truncated:
                header += f" (results capped at {_MAX_GLOB_RESULTS}; pattern matched more)"
            header += ":\n\n"
            agent_result = header
            for file_path in file_matches:
                agent_result += f"  {file_path}\n"

            agent_result = agent_result.strip()

            # Apply character length limit (30000 characters)
            if len(agent_result) > 30000:
                agent_result = agent_result[:30000] + "\n... (truncated due to length)"

            # Format results for display
            display_text = self._parse_glob_result(agent_result, pattern, path, full_output=True)

            return {
                'tool_success': True,
                'result': agent_result.strip(),
                'display_result': {
                    'text': display_text
                }
            }
        except Exception as e:
            raise ToolFailedError(f"Error executing glob pattern '{pattern}' in directory '{search_dir}': {str(e)}")

    def _parse_glob_result(self, result: str, pattern: str, path: str, full_output: bool = False) -> str:
        """Parse Glob search output into a list of paths."""
        lines = result.split('\n')

        # Extract the file list, skipping the header
        result_lines = []
        found_header = False

        for line in lines:
            # Skip header lines
            if not found_header:
                if "Found" in line and "files matching pattern" in line:
                    found_header = True
                    continue
                elif line.strip() == "":
                    continue
            else:
                # After the header, collect every non-empty line
                if line.strip():
                    # Strip indentation
                    clean_line = line.strip()
                    if clean_line.startswith("  "):
                        clean_line = clean_line[2:]  # drop leading spaces
                    result_lines.append(clean_line)

        # No explicit header: fall back to picking out path-like lines
        if not result_lines and lines:
            # Lines that look like paths (indented or containing a separator)
            for line in lines[1:]:  # skip the first line (usually a description)
                if line.strip() and ("/" in line or "\\" in line):
                    clean_line = line.strip()
                    if clean_line.startswith("  "):
                        clean_line = clean_line[2:]
                    result_lines.append(clean_line)
        return '\n'.join(result_lines)
