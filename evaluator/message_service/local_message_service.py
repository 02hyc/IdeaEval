"""
Local Message Service

Manages conversation state for multiple agents within tasks.

Storage structure:
    data_storage/
        {task_id}/
            task_info.json              # Task metadata
            {agent_name}.json           # Messages for each agent
"""

import os
import re
import json
import glob as globmod
from datetime import datetime
from typing import List, Dict, Any, Optional

from message_service.message import TaskMessage, TaskInfo, AgentData, MessageType
from utils.logger import Logger


class LocalMessageService:
    """
    Message service that stores task and agent data in local files.
    
    Each task is a folder containing:
    - task_info.json: Task-level metadata
    - {agent_name}.json: Messages for each agent
    """
    
    def __init__(self, data_dir: str = "data_storage"):
        self.data_dir_path = data_dir
        os.makedirs(self.data_dir_path, exist_ok=True)
        self.logger = Logger()
    
    # -------------------------------------------------------------------------
    # Path helpers
    # -------------------------------------------------------------------------
    
    def _get_task_dir(self, task_id: str) -> str:
        """Get the directory path for a task."""
        return os.path.join(self.data_dir_path, task_id)
    
    def _get_task_info_path(self, task_id: str) -> str:
        """Get the path to task_info.json for a task."""
        return os.path.join(self._get_task_dir(task_id), "task_info.json")
    
    def _get_agent_data_path(self, task_id: str, agent_name: str) -> str:
        """Get the path to an agent's message file."""
        return os.path.join(self._get_task_dir(task_id), f"{agent_name}.json")
    
    # -------------------------------------------------------------------------
    # Task-level operations
    # -------------------------------------------------------------------------
    
    async def create_task(self, working_dir: str) -> Optional[str]:
        """
        Create a new task.
        
        Args:
            working_dir: The working directory for this task
            
        Returns:
            The task_id if successful, None otherwise
        """
        if not working_dir:
            self.logger.error(f"Working directory is None: {working_dir}")
            return None
        
        task_id = datetime.now().strftime("%Y-%m-%d-%H-%M-%S-%f")[:-3]
        task_dir = self._get_task_dir(task_id)
        os.makedirs(task_dir, exist_ok=True)
        
        task_info = TaskInfo(
            task_id=task_id,
            working_dir=working_dir,
            agents=[],
        )
        await self._save_task_info(task_info)
        
        self.logger.info(f"Created task: {task_id}")
        return task_id
    
    async def _save_task_info(self, task_info: TaskInfo):
        """Save task info to disk atomically (write to .tmp, then os.replace)."""
        path = self._get_task_info_path(task_info.task_id)
        tmp_path = path + ".tmp"
        with open(tmp_path, 'w', encoding='utf-8') as f:
            json.dump(task_info.model_dump(), f, indent=2, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    
    async def _fetch_task_info(self, task_id: str) -> Optional[TaskInfo]:
        """Load task info from disk."""
        path = self._get_task_info_path(task_id)
        if not os.path.exists(path):
            self.logger.error(f"Task info not found: {path}")
            return None
        with open(path, 'r', encoding='utf-8') as f:
            return TaskInfo.model_validate(json.load(f))
    
    async def get_task_working_dir(self, task_id: str) -> Optional[str]:
        """Get the working directory for a task."""
        task_info = await self._fetch_task_info(task_id)
        return task_info.working_dir if task_info else None
    
    # -------------------------------------------------------------------------
    # Agent-level operations
    # -------------------------------------------------------------------------
    
    async def _save_agent_data(self, agent_data: AgentData):
        """Save agent data to disk atomically (write to .tmp, then os.replace)."""
        agent_data.updated_at = int(datetime.now().timestamp() * 1000)
        path = self._get_agent_data_path(agent_data.task_id, agent_data.agent_name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp_path = path + ".tmp"
        with open(tmp_path, 'w', encoding='utf-8') as f:
            json.dump(agent_data.model_dump(), f, indent=2, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    
    async def _fetch_agent_data(self, task_id: str, agent_name: str) -> Optional[AgentData]:
        """Load agent data from disk."""
        path = self._get_agent_data_path(task_id, agent_name)
        if not os.path.exists(path):
            return None
        with open(path, 'r', encoding='utf-8') as f:
            return AgentData.model_validate(json.load(f))
    
    async def _get_or_create_agent_data(self, task_id: str, agent_name: str) -> AgentData:
        """Get existing agent data or create new one."""
        agent_data = await self._fetch_agent_data(task_id, agent_name)
        if agent_data is None:
            # Create new agent data
            agent_data = AgentData(
                task_id=task_id,
                agent_name=agent_name,
                messages=[],
            )
            await self._save_agent_data(agent_data)
            
            # Register agent in task info
            task_info = await self._fetch_task_info(task_id)
            if task_info and agent_name not in task_info.agents:
                task_info.agents.append(agent_name)
                task_info.updated_at = int(datetime.now().timestamp() * 1000)
                await self._save_task_info(task_info)
        
        return agent_data
    
    # -------------------------------------------------------------------------
    # Message operations
    # -------------------------------------------------------------------------
    
    async def fetch_in_context_messages(
        self, 
        task_id: str, 
        agent_name: str = "default"
    ) -> List[TaskMessage]:
        """
        Fetch messages for an agent in a task, excluding compacted messages.
        
        Messages that have been compacted into a context summary are filtered out.
        A message is considered compacted if its ID appears in any summary message's
        content_extend["compact_source_msg_ids"].
        
        Args:
            task_id: The task ID
            agent_name: The agent name (default: "default")
            
        Returns:
            List of non-compacted messages for the agent
        """
        agent_data = await self._fetch_agent_data(task_id, agent_name)
        if agent_data is None:
            self.logger.warning(f"No agent data found for {agent_name} in task {task_id}")
            return []
        
        # Collect all compacted message IDs from summary messages
        compacted_ids = set()
        for msg in agent_data.messages:
            if msg.content_extend and "compact_source_msg_ids" in msg.content_extend:
                compacted_ids.update(msg.content_extend["compact_source_msg_ids"])
        
        # Filter out compacted messages
        if compacted_ids:
            return [msg for msg in agent_data.messages if msg.id not in compacted_ids]
        
        return agent_data.messages
    
    async def on_receive_user_message(
        self, 
        task_id: str, 
        message: TaskMessage | List[TaskMessage],
        agent_name: str = "default"
    ):
        """
        Add user message(s) to an agent's conversation.
        
        Args:
            task_id: The task ID
            message: Single message or list of messages
            agent_name: The agent name (default: "default")
        """
        agent_data = await self._get_or_create_agent_data(task_id, agent_name)
        
        if isinstance(message, TaskMessage):
            message.agent_name = agent_name
            agent_data.messages.append(message)
        elif isinstance(message, list):
            for msg in message:
                msg.agent_name = agent_name
            agent_data.messages.extend(message)
        
        await self._save_agent_data(agent_data)
    
    async def on_receive_agent_response(
        self, 
        task_id: str, 
        response: Dict[str, Any],
        agent_name: str = "default"
    ) -> Optional[TaskMessage]:
        """
        Add agent response to the conversation.
        
        Args:
            task_id: The task ID
            response: The raw LLM response (may include 'thinking' key for extended thinking)
            agent_name: The agent name (default: "default")
            
        Returns:
            The created TaskMessage
        """
        agent_data = await self._get_or_create_agent_data(task_id, agent_name)
        
        # Build content array - thinking blocks must come first when present
        content = []
        
        # Add thinking blocks first (required by Claude when thinking is enabled)
        if "thinking" in response and response["thinking"]:
            content.extend(response["thinking"])
        
        # Add regular content (text, tool_use)
        content.extend(response["content"])
        
        # Propagate actual model alias when a FallbackLLMClient was used.
        content_extend = None
        raw_token_usages = response.get("raw_token_usages")
        actual_model = response.get("_actual_model_alias")
        if actual_model or raw_token_usages:
            content_extend = {}
            if actual_model:
                content_extend["actual_model_alias"] = actual_model
            if raw_token_usages:
                content_extend["raw_token_usages"] = raw_token_usages

        message = TaskMessage(
            task_id=task_id,
            agent_name=agent_name,
            role="assistant",
            content=content,
            content_extend=content_extend,
            msg_type=MessageType.TOOL_USE if any(
                block["type"] == "tool_use" for block in response["content"]
            ) else MessageType.TEXT,
            token_usages=response.get("token_usages")
        )
        agent_data.messages.append(message)
        await self._save_agent_data(agent_data)
        
        return message
    
    # -------------------------------------------------------------------------
    # Utility methods
    # -------------------------------------------------------------------------
    
    # Allowed keys per content block type for the Anthropic Messages API.
    # Extra keys (e.g. SDK-injected ``parsed_output``) cause 400 errors.
    _ALLOWED_BLOCK_KEYS: Dict[str, set] = {
        "text": {"type", "text", "cache_control", "citations"},
        "tool_use": {"type", "id", "name", "input", "cache_control"},
        "tool_result": {"type", "tool_use_id", "content", "is_error", "cache_control"},
        "thinking": {"type", "thinking", "signature"},
        "reasoning": {"type", "id", "encrypted_content", "summary"},
    }

    @classmethod
    def _sanitize_content_block(cls, block: Dict[str, Any]) -> Dict[str, Any]:
        """Strip fields the API does not accept from a content block."""
        allowed = cls._ALLOWED_BLOCK_KEYS.get(block.get("type", ""))
        if allowed is None:
            return block  # unknown type — pass through as-is
        return {k: v for k, v in block.items() if k in allowed}

    # Some LLM providers (e.g. OpenAI-compatible routers) cap the number of
    # tool_calls per assistant message at 128.  Use 120 for safety headroom.
    _MAX_TOOL_USE_PER_MESSAGE = 120

    @classmethod
    def get_llm_compatible_messages(cls, messages: List[TaskMessage]) -> List[Dict[str, Any]]:
        """Convert TaskMessages to LLM-compatible format.

        Also splits assistant messages that contain more than
        ``_MAX_TOOL_USE_PER_MESSAGE`` tool_use blocks into multiple
        assistant→user round-trips so providers with a per-message tool_calls
        limit (typically 128) do not reject the request.
        """
        llm_compatible_messages = []
        for message in messages:
            llm_compatible_messages.append({
                "role": message.role,
                "content": [cls._sanitize_content_block(b) for b in message.content],
            })

        llm_compatible_messages = cls._split_oversized_tool_use_messages(
            llm_compatible_messages
        )
        return llm_compatible_messages

    @classmethod
    def _split_oversized_tool_use_messages(
        cls, messages: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Split assistant messages whose tool_use count exceeds the limit.

        For each oversized assistant message, the immediately following user
        message (which should contain the matching tool_result blocks) is also
        split so that every tool_use in a chunk has its corresponding
        tool_result in the paired user message.

        Non-tool content blocks (text, thinking) stay in the first chunk;
        non-tool_result user blocks (e.g. notification text) go in the last
        user chunk.
        """
        limit = cls._MAX_TOOL_USE_PER_MESSAGE
        result: List[Dict[str, Any]] = []
        i = 0
        while i < len(messages):
            msg = messages[i]

            # Only care about assistant messages
            if msg["role"] != "assistant":
                result.append(msg)
                i += 1
                continue

            content = msg["content"]
            tool_use_blocks = [
                b for b in content
                if isinstance(b, dict) and b.get("type") == "tool_use"
            ]

            # No split needed
            if len(tool_use_blocks) <= limit:
                result.append(msg)
                i += 1
                continue

            # --- Split needed ---
            non_tool_blocks = [
                b for b in content
                if not (isinstance(b, dict) and b.get("type") == "tool_use")
            ]

            # Peek at the next message — it should be a user message with
            # tool_result blocks matching the tool_use IDs above.
            next_msg = messages[i + 1] if i + 1 < len(messages) else None
            has_paired_user = next_msg is not None and next_msg["role"] == "user"

            if not has_paired_user:
                # Safety: truncate if there is no paired user message (should
                # not happen in practice).
                result.append({
                    "role": "assistant",
                    "content": non_tool_blocks + tool_use_blocks[:limit],
                })
                i += 1
                continue

            # Build tool_use_id → tool_result mapping from the user message
            tool_result_map: Dict[str, Dict[str, Any]] = {}
            remaining_user_blocks: List[Dict[str, Any]] = []
            for b in next_msg["content"]:
                if isinstance(b, dict) and b.get("type") == "tool_result":
                    tool_result_map[b.get("tool_use_id", "")] = b
                else:
                    remaining_user_blocks.append(b)

            # Chunk the tool_use blocks
            chunks = [
                tool_use_blocks[j : j + limit]
                for j in range(0, len(tool_use_blocks), limit)
            ]

            for ci, chunk in enumerate(chunks):
                is_first = ci == 0
                is_last = ci == len(chunks) - 1

                # Assistant chunk
                asst_content = (non_tool_blocks + chunk) if is_first else list(chunk)
                result.append({"role": "assistant", "content": asst_content})

                # Paired user chunk with matching tool_results
                chunk_results = [
                    tool_result_map[tu.get("id")]
                    for tu in chunk
                    if tu.get("id") in tool_result_map
                ]
                user_content: List[Dict[str, Any]] = list(chunk_results)
                if is_last and remaining_user_blocks:
                    user_content.extend(remaining_user_blocks)
                # Ensure the user message is never empty
                if not user_content:
                    user_content = [{"type": "text", "text": " "}]
                result.append({"role": "user", "content": user_content})

            # We consumed both the assistant and user message
            i += 2
            continue

        return result
    
    async def list_agents(self, task_id: str) -> List[str]:
        """List all agents in a task."""
        task_info = await self._fetch_task_info(task_id)
        return task_info.agents if task_info else []
    
    async def get_agent_message_count(self, task_id: str, agent_name: str) -> int:
        """Get the number of messages for an agent."""
        agent_data = await self._fetch_agent_data(task_id, agent_name)
        return len(agent_data.messages) if agent_data else 0
    
    # -------------------------------------------------------------------------
    # Message file rotation
    # -------------------------------------------------------------------------

    def _get_next_archive_path(self, task_id: str, agent_name: str) -> str:
        """Determine the next archive file path with a monotonically increasing counter."""
        task_dir = self._get_task_dir(task_id)
        pattern = os.path.join(task_dir, f"{agent_name}.archive_*.json")
        existing = sorted(globmod.glob(pattern))
        if existing:
            # Extract the highest counter from existing archive files
            last = os.path.basename(existing[-1])
            match = re.search(r'\.archive_(\d+)\.json$', last)
            next_num = int(match.group(1)) + 1 if match else 0
        else:
            next_num = 0
        return os.path.join(task_dir, f"{agent_name}.archive_{next_num:04d}.json")

    async def rotate_compacted_messages(self, task_id: str, agent_name: str) -> int:
        """
        Move compacted messages to an archive file after compaction.

        Scans all CONTEXT_SUMMARY messages for compact_source_msg_ids, collects
        the referenced (compacted) messages, writes them to an archive file, then
        removes them from the active agent data file.

        The archive is written FIRST for crash safety: if we crash before updating
        the active file, fetch_in_context_messages still filters them correctly.

        Returns:
            Number of archived messages (0 if nothing to archive).
        """
        agent_data = await self._fetch_agent_data(task_id, agent_name)
        if agent_data is None:
            return 0

        # Collect ALL compacted message IDs from CONTEXT_SUMMARY messages
        compacted_ids: set = set()
        for msg in agent_data.messages:
            if msg.content_extend and "compact_source_msg_ids" in msg.content_extend:
                compacted_ids.update(msg.content_extend["compact_source_msg_ids"])

        if not compacted_ids:
            return 0

        # Partition: archive vs keep
        archive_messages = []
        keep_messages = []
        for msg in agent_data.messages:
            if msg.id in compacted_ids:
                archive_messages.append(msg)
            else:
                keep_messages.append(msg)

        if not archive_messages:
            return 0

        # Write archive FIRST (crash-safe ordering)
        archive_path = self._get_next_archive_path(task_id, agent_name)
        archive_data = {
            "task_id": task_id,
            "agent_name": agent_name,
            "archived_at": int(datetime.now().timestamp() * 1000),
            "message_count": len(archive_messages),
            "messages": [m.model_dump() for m in archive_messages],
        }
        tmp_archive = archive_path + ".tmp"
        with open(tmp_archive, 'w', encoding='utf-8') as f:
            json.dump(archive_data, f, indent=2, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_archive, archive_path)

        # Now update the active file (remove archived messages)
        agent_data.messages = keep_messages
        await self._save_agent_data(agent_data)

        self.logger.info(
            f"Rotated {len(archive_messages)} compacted messages to {os.path.basename(archive_path)}"
        )
        return len(archive_messages)

    # -------------------------------------------------------------------------
    # Task discovery (for crash recovery)
    # -------------------------------------------------------------------------

    def find_latest_task_id(self) -> Optional[str]:
        """
        Find the latest task ID by scanning the data directory.

        Task directories are timestamp-named (YYYY-MM-DD-HH-MM-SS-mmm), so
        lexicographic sort gives chronological order.

        Returns:
            The latest task_id that has a valid task_info.json, or None.
        """
        if not os.path.isdir(self.data_dir_path):
            return None

        entries = []
        for name in os.listdir(self.data_dir_path):
            task_dir = os.path.join(self.data_dir_path, name)
            if os.path.isdir(task_dir) and os.path.isfile(os.path.join(task_dir, "task_info.json")):
                entries.append(name)

        if not entries:
            return None

        # Sort lexicographically (timestamp format is sortable)
        entries.sort()
        return entries[-1]

    def find_latest_resumable_task_id(self) -> Optional[str]:
        """Return the newest task that passes resume validation."""
        if not os.path.isdir(self.data_dir_path):
            return None

        entries = []
        for name in os.listdir(self.data_dir_path):
            task_dir = os.path.join(self.data_dir_path, name)
            if os.path.isdir(task_dir) and os.path.isfile(os.path.join(task_dir, "task_info.json")):
                entries.append(name)

        for task_id in sorted(entries, reverse=True):
            if self.validate_task_for_resume(task_id):
                return task_id
        return None

    def validate_task_for_resume(self, task_id: str) -> bool:
        """
        Validate that a task is suitable for resumption.

        Checks:
        1. task_info.json exists and is valid JSON with a valid TaskInfo
        2. At least one agent data file exists with >= 1 message

        Returns:
            True if the task can be resumed, False otherwise.
        """
        try:
            task_dir = self._get_task_dir(task_id)
            info_path = os.path.join(task_dir, "task_info.json")
            if not os.path.isfile(info_path):
                return False

            with open(info_path, 'r', encoding='utf-8') as f:
                info = json.load(f)
            # Validate it parses as TaskInfo
            TaskInfo.model_validate(info)

            # Check that at least one agent data file has messages
            for fname in os.listdir(task_dir):
                if fname.endswith('.json') and fname != 'task_info.json' and '.archive_' not in fname:
                    fpath = os.path.join(task_dir, fname)
                    try:
                        with open(fpath, 'r', encoding='utf-8') as f:
                            data = json.load(f)
                        if isinstance(data.get("messages"), list) and len(data["messages"]) >= 1:
                            return True
                    except Exception:
                        continue

            return False
        except Exception as e:
            self.logger.warning(f"validate_task_for_resume failed for {task_id}: {e}")
            return False

    # -------------------------------------------------------------------------
    # Todo operations
    # -------------------------------------------------------------------------
    
    async def get_todos(self, task_id: str, agent_name: str) -> List[Dict[str, Any]]:
        """
        Get the todo list for an agent.
        
        Args:
            task_id: The task ID
            agent_name: The agent name
            
        Returns:
            List of todo items
        """
        agent_data = await self._fetch_agent_data(task_id, agent_name)
        if agent_data is None:
            return []
        return agent_data.todo
    
    async def update_todos(
        self, 
        task_id: str, 
        agent_name: str, 
        todos: List[Dict[str, Any]]
    ) -> bool:
        """
        Update the todo list for an agent.
        
        Args:
            task_id: The task ID
            agent_name: The agent name
            todos: The new todo list
            
        Returns:
            True if successful, False otherwise
        """
        agent_data = await self._fetch_agent_data(task_id, agent_name)
        if agent_data is None:
            self.logger.error(f"No agent data found for {agent_name} in task {task_id}")
            return False
        
        agent_data.todo = todos
        await self._save_agent_data(agent_data)
        return True
