from typing import Dict, Any, Optional, List, Union, Tuple
import asyncio
import json
import logging

from toolkit.tool_handlers.tool_state import ToolState
from agents.tools.tool_names import ToolName

from toolkit.tool_handlers.bash_output_tool import BashOutputToolHandler
from toolkit.tool_handlers.bash_tool import BashToolHandler
from toolkit.tool_handlers.web_search_dated_handler import WebSearchDatedToolHandler
from toolkit.tool_handlers.web_fetch_dated_handler import WebFetchDatedToolHandler
from toolkit.tool_handlers.kill_bash_tool import KillBashToolHandler
from toolkit.tool_handlers.ls_bash_tool import LSBashToolHandler
from toolkit.tool_handlers.view_tool import ViewToolHandler
from toolkit.tool_handlers.write_tool import WriteToolHandler
from toolkit.tool_handlers.edit_tool import EditToolHandler
from toolkit.tool_handlers.glob_tool import GlobToolHandler
from toolkit.tool_handlers.grep_tool import GrepToolHandler
from toolkit.tool_handlers.scraper_dated_handler import ScraperDatedToolHandler
from toolkit.tool_handlers.todo_write_tool import TodoWriteToolHandler

from message_service.message import TaskMessage, MessageType
from utils.token_usage import build_raw_token_usage_records

logger = logging.getLogger("agent.toolkit")

# Maximum total tool calls allowed in a single assistant turn.
# If the model emits more than this, excess tool_use blocks are removed from the
# response before saving to the message store, and only the first MAX are executed.
# This guards against model malfunctions (e.g., emitting 500+ WebSearch calls).
MAX_TOOL_CALLS_PER_TURN = 20


class Toolkit:
    def __init__(
        self,
        task_id: str,
        working_dir: str,
        agent_name: Optional[str] = None,
        message_service: Optional[Any] = None,
        notification_center: Optional[Any] = None,
        shared_workspace: Optional[str] = None,
        global_proposals_dir: Optional[str] = None,
        allowed_tools: Optional[set] = None,
        user_prompt: Optional[str] = None,
        end_to_end_mode: bool = False,
        cutoff_date: Optional[str] = None,
    ):
        self.tool_state = ToolState(
            task_id=task_id,
            working_dir=working_dir,
            agent_name=agent_name,
            message_service=message_service,
            notification_center=notification_center,
            shared_workspace=shared_workspace,
            global_proposals_dir=global_proposals_dir,
            user_prompt=user_prompt,
            end_to_end_mode=end_to_end_mode,
            cutoff_date=cutoff_date,
        )
        all_handlers = {
            ToolName.BASH: BashToolHandler(self.tool_state),
            ToolName.WEB_SEARCH: WebSearchDatedToolHandler(self.tool_state),
            ToolName.WEB_FETCH: WebFetchDatedToolHandler(self.tool_state),
            ToolName.BASH_OUTPUT: BashOutputToolHandler(self.tool_state),
            ToolName.KILL_BASH: KillBashToolHandler(self.tool_state),
            ToolName.LS_BASH: LSBashToolHandler(self.tool_state),
            ToolName.VIEW: ViewToolHandler(self.tool_state),
            ToolName.WRITE: WriteToolHandler(self.tool_state),
            ToolName.EDIT: EditToolHandler(self.tool_state),
            ToolName.GLOB: GlobToolHandler(self.tool_state),
            ToolName.GREP: GrepToolHandler(self.tool_state),
            ToolName.SCRAPER: ScraperDatedToolHandler(self.tool_state),
            ToolName.TODO_WRITE: TodoWriteToolHandler(self.tool_state),
        }
        if allowed_tools is not None:
            self.tool_handlers = {k: v for k, v in all_handlers.items() if k in allowed_tools}
        else:
            self.tool_handlers = all_handlers

    @staticmethod
    def truncate_tool_calls(response: Dict[str, Any]) -> Tuple[Dict[str, Any], Optional[str]]:
        """Truncate excessive tool calls in an LLM response before saving.

        If the response contains more than MAX_TOOL_CALLS_PER_TURN tool_use blocks,
        only the first MAX_TOOL_CALLS_PER_TURN are kept.  The rest are removed from
        ``response["content"]`` (and ``response["thinking"]`` is left untouched).

        Returns:
            A tuple of (possibly-modified response, notification_text_or_None).
            The notification text should be appended to the tool-result message so
            the agent knows what happened.
        """
        content = response.get("content")
        if not content:
            return response, None

        tool_use_blocks = [b for b in content if isinstance(b, dict) and b.get("type") == "tool_use"]
        total = len(tool_use_blocks)
        if total <= MAX_TOOL_CALLS_PER_TURN:
            return response, None

        # Determine which tool_use block IDs to keep (first N in order).
        kept_ids = set()
        for block in tool_use_blocks:
            if len(kept_ids) >= MAX_TOOL_CALLS_PER_TURN:
                break
            kept_ids.add(block["id"])

        # Rebuild content: keep all non-tool_use blocks + only the kept tool_use blocks.
        new_content = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                if block["id"] in kept_ids:
                    new_content.append(block)
                # else: drop silently
            else:
                new_content.append(block)

        dropped = total - MAX_TOOL_CALLS_PER_TURN
        response["content"] = new_content

        # Count dropped calls per tool name for informative logging.
        from collections import Counter
        all_names = [b["name"] for b in tool_use_blocks]
        kept_names = [b["name"] for b in tool_use_blocks if b["id"] in kept_ids]
        dropped_counter = Counter(all_names)
        dropped_counter.subtract(Counter(kept_names))
        drop_details = ", ".join(
            f"{name}: {count} dropped" for name, count in sorted(dropped_counter.items()) if count > 0
        )

        logger.warning(
            f"[Toolkit] Truncated {dropped} tool calls from {total} to {MAX_TOOL_CALLS_PER_TURN}. {drop_details}"
        )

        notification = (
            f"<notification>\n"
            f"<source>system</source>\n"
            f"<content>\n"
            f"WARNING: You attempted {total} tool calls in a single turn, but the system "
            f"limit is {MAX_TOOL_CALLS_PER_TURN} total tool calls per turn. "
            f"{dropped} tool calls were dropped and NOT executed ({drop_details}). "
            f"Only the first {MAX_TOOL_CALLS_PER_TURN} were kept and executed.\n\n"
            f"To avoid this: make at most {MAX_TOOL_CALLS_PER_TURN} tool calls per turn. "
            f"Review results first, then make additional calls in your next turn if needed.\n"
            f"</content>\n"
            f"</notification>"
        )
        return response, notification

    async def handle_tool_use(self, tool_use_message: TaskMessage) -> Optional[TaskMessage]:
        tool_results_content: List[Dict[str, Any]] = []
        sub_agent_token_infos: List[Dict[str, Any]] = []
        sub_agent_raw_token_usages: List[Dict[str, Any]] = []

        # These tools are pure network calls and safe to run concurrently.
        parallelizable_tools = {ToolName.WEB_SEARCH}

        # Phase 1: start parallelizable tool calls immediately.
        # We preserve response ordering by awaiting in original order later.
        pending: List[Optional[asyncio.Task]] = []
        tool_use_blocks: List[Dict[str, Any]] = []
        for block in tool_use_message.content:
            if block.get("type") != "tool_use":
                continue
            tool_use_blocks.append(block)
            tool_name = block["name"]
            if tool_name in parallelizable_tools and tool_name in self.tool_handlers:
                tool_handler = self.tool_handlers[tool_name]
                pending.append(asyncio.create_task(tool_handler.execute(block["input"])))
            else:
                pending.append(None)

        # Phase 2: resolve results in original tool-call order.
        for idx, block in enumerate(tool_use_blocks):
            tool_name = block["name"]
            task = pending[idx]
            try:
                if task is not None:
                    result = await task
                elif tool_name in self.tool_handlers:
                    tool_handler = self.tool_handlers[tool_name]
                    # Use execute() instead of execute_async() - it catches ToolFailedError
                    # and returns error results gracefully instead of raising exceptions
                    result = await tool_handler.execute(block["input"])
                else:
                    result = {
                        "success": False,
                        "result": f"Tool '{tool_name}' is not available.",
                        "display_result": {
                            "type": "error",
                            "data": {},
                            "tool_success": False,
                            "error": f"Tool '{tool_name}' is not available.",
                        },
                        "error": None,
                        "error_type": None,
                    }
            except Exception as e:
                logger.error(f"[Toolkit] Unhandled exception in tool '{tool_name}': {e}", exc_info=True)
                error_msg = f"{tool_name} failed: {e}"
                result = {
                    "success": False,
                    "result": error_msg,
                    "display_result": {
                        "type": "error",
                        "data": {},
                        "tool_success": False,
                        "error": error_msg,
                    },
                    "error": None,
                    "error_type": None,
                }

            # Collect sub-agent token info if present
            if result.get("sub_agent_token_infos"):
                current_infos = result["sub_agent_token_infos"]
                sub_agent_token_infos.extend(current_infos)
                if not result.get("sub_agent_raw_token_usages"):
                    for info in current_infos:
                        if not isinstance(info, dict):
                            continue
                        sub_agent_raw_token_usages.extend(
                            build_raw_token_usage_records(
                                role="sub_agent",
                                model_name=str(info.get("model", "") or ""),
                                raw_token_usages=info.get("raw_token_usages"),
                                agent_name=str(info.get("agent_name", "") or ""),
                            )
                        )
            elif result.get("sub_agent_token_info"):
                current_info = result["sub_agent_token_info"]
                sub_agent_token_infos.append(current_info)
                if (
                    isinstance(current_info, dict)
                    and not result.get("sub_agent_raw_token_usages")
                ):
                    sub_agent_raw_token_usages.extend(
                        build_raw_token_usage_records(
                            role="sub_agent",
                            model_name=str(current_info.get("model", "") or ""),
                            raw_token_usages=current_info.get("raw_token_usages"),
                            agent_name=str(current_info.get("agent_name", "") or ""),
                        )
                    )
            if result.get("sub_agent_raw_token_usages"):
                sub_agent_raw_token_usages.extend(result["sub_agent_raw_token_usages"])

            # Format content for Claude API
            content = self._format_tool_result_content(result["result"])
            tool_result_block = {
                "type": "tool_result",
                "tool_use_id": block["id"],
                "content": content,
            }
            display_result = result.get("display_result")
            if isinstance(display_result, dict):
                tool_result_block["display_result"] = display_result

            tool_results_content.append(tool_result_block)
        if len(tool_results_content) > 0:
            content_extend = None
            if sub_agent_token_infos or sub_agent_raw_token_usages:
                content_extend = {}
                if sub_agent_token_infos:
                    content_extend["sub_agent_token_infos"] = sub_agent_token_infos
                if sub_agent_raw_token_usages:
                    content_extend["sub_agent_raw_token_usages"] = sub_agent_raw_token_usages
            return TaskMessage(
                task_id=tool_use_message.task_id,
                role="user",
                content=tool_results_content,
                msg_type=MessageType.TOOL_RESULT,
                content_extend=content_extend,
            )
        return None
    
    def _format_tool_result_content(self, result: Union[str, List, Dict, Any]) -> Union[str, List[Dict[str, Any]]]:
        """
        Format tool result content for Claude API.
        
        Claude requires tool_result content to be either:
        1. A string directly
        2. An array of content blocks with 'type' field (e.g., {"type": "text", "text": "..."})
        
        Args:
            result: The raw tool result
            
        Returns:
            Properly formatted content for tool_result
        """
        if isinstance(result, str):
            # String content is valid as-is
            return result
        elif isinstance(result, list):
            # Check if list items already have proper 'type' field
            formatted = []
            for item in result:
                if isinstance(item, dict) and "type" in item:
                    # Already properly formatted
                    formatted.append(item)
                elif isinstance(item, str):
                    formatted.append({"type": "text", "text": item})
                else:
                    # Convert to JSON string
                    formatted.append({"type": "text", "text": json.dumps(item, ensure_ascii=False, indent=2)})
            return formatted
        elif isinstance(result, dict):
            # Convert dict to JSON string in a text block
            return json.dumps(result, ensure_ascii=False, indent=2)
        else:
            # Convert any other type to string
            return str(result)