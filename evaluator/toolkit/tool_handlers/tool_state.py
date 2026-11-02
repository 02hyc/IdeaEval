import os
import time
import logging
from typing import Optional, List, Dict, Any
# from remote_tool_handler.bash_session import get_bash_manager, cleanup_bash_manager
from toolkit.tool_handlers.bash_session_pyte import get_bash_manager, cleanup_bash_manager  

class ToolState():
    def __init__(
        self,
        task_id: str,
        working_dir: str,
        agent_name: Optional[str] = None,
        message_service: Optional[Any] = None,
        notification_center: Optional[Any] = None,
        shared_workspace: Optional[str] = None,
        global_proposals_dir: Optional[str] = None,
        user_prompt: Optional[str] = None,
        end_to_end_mode: bool = False,
        cutoff_date: Optional[str] = None,
        s2_keys: Optional[List[str]] = None,
    ):
        self.type = 'remote_tool_state'
        self.logger = logging.getLogger(__name__)
        self.cutoff_date = cutoff_date
        self.s2_keys = s2_keys or []
        self.s2_key_index_ref = [0]
        self._files_read = set()
        self._file_read_timestamps = {}
        self.original_working_dir = working_dir
        # In multi-agent mode, shared_workspace is the parent directory containing
        # shared resources (papers, finalized_proposals, index files).
        # In single-agent mode, shared_workspace equals working_dir.
        self._shared_workspace = shared_workspace if shared_workspace is not None else working_dir
        # Optional global directory where finalized proposals are also copied to.
        self._global_proposals_dir = global_proposals_dir
        self.task_id = task_id
        self.agent_name = agent_name
        self.message_service = message_service
        self.notification_center = notification_center
        self._user_prompt = user_prompt.strip() if isinstance(user_prompt, str) else ""
        # Bash manager for this task's Bash Sessions (windows)
        self._bash_manager = None  # Will be initialized on first use

        # Lightweight tool memory (in-process only).
        # Used by DiscussWithPeer to support multi-turn conversations.
        self.peer_discuss_session: Optional[Dict[str, str]] = None

        # Task rejection flag (set by RejectTask tool handler)
        self.task_rejected: bool = False
        self.rejection_reason: str = ""

        # End-to-end mode: quality gate enforced on finalization
        self.end_to_end_mode = end_to_end_mode
        # When True, the quality gate in FinalizeProposal is relaxed (circuit breaker triggered)
        self.end_to_end_gate_relaxed: bool = False

    def get_working_dir(self) -> str:
        """Get the agent's working directory (sub-workspace in multi-agent mode)."""
        return self.original_working_dir

    def get_shared_workspace(self) -> str:
        """Get the shared workspace directory.

        In multi-agent mode, this is the parent directory containing shared resources
        (papers, finalized_proposals, index files). In single-agent mode, this equals
        the working directory.
        """
        return self._shared_workspace

    def get_global_proposals_dir(self) -> Optional[str]:
        """Get the global proposals directory, if configured.

        When set, finalized proposals are also copied to this directory.
        """
        return self._global_proposals_dir

    def get_user_prompt(self) -> str:
        """Get the original user research request for the current task."""
        return self._user_prompt

    # -------------------- bash session management APIs --------------------
    def get_bash_manager(self):
        """Return the bash manager for this task, ensuring working_dir is current."""
        # Use the global registry to ensure cleanup works properly
        self._bash_manager = get_bash_manager(task_id=self.task_id, working_dir=self.get_working_dir())
        return self._bash_manager

    async def bash_status(self, session_id: str) -> Dict[str, Any]:
        mgr = self.get_bash_manager()
        return await mgr.status(session_id)

    async def bash_run_command(self, command: str, session_id: Optional[str], timeout: float) -> Dict[str, Any]:
        mgr = self.get_bash_manager()
        return await mgr.run_command(command=command, window_name=session_id, timeout=timeout)

    async def bash_read_output(self, session_id: str, incremental: bool = True) -> tuple:
        mgr = self.get_bash_manager()
        return await mgr.read_output(session_id, incremental=incremental)

    async def bash_list_windows(self) -> List[Any]:
        mgr = self.get_bash_manager()
        return await mgr.list_windows()

    async def bash_kill(self, session_id: str, end_session: bool) -> Dict[str, Any]:
        mgr = self.get_bash_manager()
        ok, msg = await mgr.kill(session_id, end_session)
        return {"ok": ok, "message": msg}
    
    # -------------------- File management APIs --------------------

    def mark_file_as_read(self, file_path: str) -> None:
        """Mark a file as having been read"""
        abs_path = os.path.abspath(file_path)
        self._files_read.add(abs_path)
        self._file_read_timestamps[abs_path] = time.time()
    
    def is_file_read(self, file_path: str) -> bool:
        """Check if a file has been read"""
        return os.path.abspath(file_path) in self._files_read
    
    def is_file_read_fresh(self, file_path: str) -> bool:
        """Check if a file has been read and the read is still fresh (file hasn't been modified since read)"""
        abs_path = os.path.abspath(file_path)
        
        # Check if file was read at all
        if abs_path not in self._files_read:
            return False
        
        # Check if file still exists
        if not os.path.exists(abs_path):
            return False
        
        try:
            # Get file modification time
            file_mtime = os.path.getmtime(abs_path)
            # Get read timestamp
            read_time = self._file_read_timestamps.get(abs_path, 0)
            
            # File is fresh if it was read after the last modification
            # Allow small tolerance (1 second) for potential timing issues
            return read_time >= (file_mtime - 1.0)
        except OSError:
            # If we can't get file stats, consider it not fresh
            return False
    
    async def terminate(self):
        """Terminate the tool state and cleanup resources"""
        # Clean up bash manager for this task
        if self._bash_manager is not None:
            try:
                await cleanup_bash_manager(self.task_id)
                self._bash_manager = None
            except Exception as e:
                self.logger.error(f"Failed to cleanup bash manager: {e}")
    
    def task_state_to_dict(self) -> Dict[str, Any]:
        """Save task tool state to a JSON file"""
        if not self._files_read:
            return None
        
        # Convert sets to lists for JSON serialization
        return {
            "working_dir": "",
            "task_id": "",
            "tool_state": {
                "files_read": list(self._files_read),
                "file_read_timestamps": self._file_read_timestamps,
            }
        }

    def load_task_state(self, state_data: dict) -> None:
        """Load task tool state from a state dictionary"""
        try:
            # Validate the structure
            if "working_dir" not in state_data or "tool_state" not in state_data:
                raise ValueError(f"Invalid state file format: missing required fields for tool_state: {state_data}")
            
            tool_state = state_data["tool_state"]
            if "files_read" not in tool_state or "file_read_timestamps" not in tool_state:
                raise ValueError(f"Invalid state file format: missing tool_state fields: {tool_state}")
            
            # Load the data
            self._files_read = set(tool_state["files_read"])
            self._file_read_timestamps = tool_state["file_read_timestamps"]
            
            # Update working directory if different
            saved_working_dir = state_data["working_dir"]
            if saved_working_dir and saved_working_dir != self.get_working_dir():
                # Note: We don't change the bash session's working directory here
                # as it might be actively running commands. The caller should handle
                # directory changes if needed.
                self.logger.warning(f"Working directory is different from saved working directory: {self.get_working_dir()} -> {saved_working_dir}")
                
        except Exception as e:
            raise Exception(f"Failed to load task state from {state_data}: {e}")
