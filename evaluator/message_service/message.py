import pydantic
from enum import Enum
from typing import Optional, List, Dict, Any, Literal

import time
import uuid


class MessageType(str, Enum):
    NOTIFICATION = "notification"
    TOOL_USE = "tool_use"
    TOOL_RESULT = "tool_result"
    TEXT = "text"
    CONTEXT_SUMMARY = "context_summary"  # Summary of compacted messages


class TaskMessage(pydantic.BaseModel):
    """
    A single message in the conversation.
    
    Attributes:
        id: Unique message identifier (auto-generated UUID)
        task_id: The task this message belongs to
        agent_name: Which agent this message belongs to
        role: 'user' or 'assistant'
        content: Message content blocks
        content_extend: Extended metadata for the message. Known keys:
            - actual_model_alias: str - The actual model alias used when the
              fallback LLM path handled the request.
            - raw_token_usages: List[Dict[str, Any]] - Provider-native token
              usage payloads preserved for downstream auditing.
            - sub_agent_token_infos: List[Dict[str, Any]] - Normalized sub-agent
              token/cost summaries (model, agent_name, token_usage).
            - sub_agent_raw_token_usages: List[Dict[str, Any]] - Provider-native
              sub-agent token usage records for `tool_result.data.token_usages`.
            - compact_source_msg_ids: List[str] - For CONTEXT_SUMMARY messages,
              contains the IDs of messages that were compacted into this summary.
              These messages will be filtered out by fetch_in_context_messages.
        msg_type: Type of message (NOTIFICATION, TOOL_USE, TOOL_RESULT, TEXT, CONTEXT_SUMMARY)
    """
    id: str = pydantic.Field(default_factory=lambda: str(uuid.uuid4()))
    task_id: str
    agent_name: Optional[str] = None
    role: Literal['user', 'assistant']
    content: List[Dict[str, Any]]
    content_extend: Optional[Dict[str, Any]] = None
    msg_type: MessageType
    token_usages: Optional[List[Dict[str, Any]]] = None
    gmt_create: int = pydantic.Field(default_factory=lambda: int(time.time() * 1000))
    gmt_modified: int = pydantic.Field(default_factory=lambda: int(time.time() * 1000))


class AgentData(pydantic.BaseModel):
    """Messages and state for a single agent within a task."""
    task_id: str
    agent_name: str
    messages: List[TaskMessage] = pydantic.Field(default_factory=list)
    todo: List[Dict[str, Any]] = pydantic.Field(default_factory=list)  # Todo list for the agent
    created_at: int = pydantic.Field(default_factory=lambda: int(time.time() * 1000))
    updated_at: int = pydantic.Field(default_factory=lambda: int(time.time() * 1000))


class TaskInfo(pydantic.BaseModel):
    """Task-level metadata (shared across all agents in the task)."""
    task_id: str
    working_dir: str
    agents: List[str] = pydantic.Field(default_factory=list)  # List of agent names in this task
    created_at: int = pydantic.Field(default_factory=lambda: int(time.time() * 1000))
    updated_at: int = pydantic.Field(default_factory=lambda: int(time.time() * 1000))


# Backward compatibility alias
class TaskData(pydantic.BaseModel):
    """Legacy TaskData for backward compatibility. Use TaskInfo + AgentData instead."""
    task_id: str
    working_dir: str
    messages: List[TaskMessage] = pydantic.Field(default_factory=list)
