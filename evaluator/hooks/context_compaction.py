"""
Context compaction hook that compresses conversation history when it exceeds token threshold.
"""

import os
from datetime import datetime
from typing import List, Dict, Any, Optional

from utils.logger import Logger
from utils.timezone import get_timezone
from message_service.message import TaskMessage, MessageType
from agents.config import AgentConfig
from agents.llm.token_counter import TokenCounter
from .base import Hook, HookContext


class ContextCompactionHook(Hook):
    """
    Hook that compacts context when token count exceeds threshold.
    
    When triggered:
    1. Counts total tokens in context
    2. If exceeding threshold, summarizes older messages
    3. Keeps the most recent N messages intact
    4. Adds a system reminder about the compaction
    """

    def __init__(
        self,
        config: AgentConfig,
        token_counter: TokenCounter,
        message_service,
        llm_client,
        working_dir: str = None,
        user_prompt: str = "",
        end_to_end_mode: bool = False,
    ):
        self.config = config
        self.token_counter = token_counter
        self.message_service = message_service
        self.llm_client = llm_client
        self.keep_recent = config.keep_recent_messages
        self.working_dir = working_dir
        self.user_prompt = user_prompt
        self.end_to_end_mode = end_to_end_mode
        self.logger = Logger()

        # Context budget warning (before compaction)
        self.warn_ratio = 0.80
        self.warn_cooldown_turns = 5
        self.warn_lookback_messages = 3
        self._turn = 0
        self._last_warn_turn: Optional[int] = None

        # Timestamp formatting (keep consistent with NotificationCenter)
        self._tz = get_timezone("Asia/Shanghai")
        self._warn_marker = "CONTEXT BUDGET WARNING"
    
    @property
    def name(self) -> str:
        return "context_compaction"
    
    @property
    def priority(self) -> int:
        return 10  # Run early in the hook chain
    
    async def pre_run(self, context: HookContext) -> HookContext:
        """Check token count and compact if needed"""
        self._turn += 1
        # Count tokens (reuse if already computed by earlier hooks)
        thinking_enabled = bool(context.enable_thinking)
        token_count = context.get_metadata("token_count")
        if token_count is None:
            token_count = await self._count_tokens(context, thinking_enabled=thinking_enabled)
            context.set_metadata("token_count", token_count)
        # Apply safety margin: trigger compaction at 90% of threshold
        # This provides buffer for estimation inaccuracies
        SAFETY_MARGIN = 0.90
        threshold = int(context.max_context_tokens * SAFETY_MARGIN)
        
        self.logger.debug(
            f"[ContextCompaction] Token count: {token_count}, Threshold: {threshold} (90% of {context.max_context_tokens})"
        )

        # Early warning: remind the agent to persist important state before compaction.
        # Only emit when we're above warn threshold but still below compaction threshold
        # (avoid adding messages when we're already going to compact).
        warn_threshold = int(context.max_context_tokens * self.warn_ratio)
        if token_count >= warn_threshold and token_count <= threshold:
            # Avoid pushing us over the compaction threshold by adding extra text when already very close.
            MIN_BUFFER_TOKENS = 1000
            if token_count < (threshold - MIN_BUFFER_TOKENS):
                await self._maybe_emit_budget_warning(context, token_count=token_count, max_tokens=context.max_context_tokens)
        
        if token_count > threshold:
            self.logger.info(
                f"[ContextCompaction] Context exceeds threshold ({token_count} > {threshold}). Compacting..."
            )

            original_token_count = token_count
            max_compaction_rounds = 5  # Prevent infinite loops
            compaction_round = 0
            # If a compaction round reduces less than this fraction of the token count,
            # further rounds are unlikely to help (the kept-recent messages dominate).
            # Bail out early to avoid wasting LLM summarization calls.
            MIN_REDUCTION_RATIO = 0.02  # 2%

            while token_count > threshold and compaction_round < max_compaction_rounds:
                compaction_round += 1
                prev_token_count = token_count
                self.logger.info(
                    f"[ContextCompaction] Compaction round {compaction_round}: {token_count} tokens"
                )

                # Dynamically reduce keep_recent if we're way over threshold
                # If after compaction we're still over, we need to keep fewer messages
                effective_keep_recent = self.keep_recent
                if compaction_round > 1:
                    # Each round, reduce the number of kept messages
                    effective_keep_recent = max(2, self.keep_recent - (compaction_round - 1) * 2)
                    self.logger.info(
                        f"[ContextCompaction] Reducing kept messages to {effective_keep_recent}"
                    )

                # Compact messages — if the summarization LLM call fails
                # (e.g. Bedrock outage), abort the compaction loop and fall
                # through to emergency truncation instead of propagating the
                # exception and killing the entire task.
                try:
                    await self._compact(context, keep_recent_override=effective_keep_recent)
                except Exception as e:
                    self.logger.error(
                        f"[ContextCompaction] Summarization LLM failed in round "
                        f"{compaction_round}: {e}. Aborting compaction loop; "
                        f"will fall through to emergency truncation."
                    )
                    break

                # Refetch messages after compaction
                context.messages = await self.message_service.fetch_in_context_messages(
                    context.task_id, context.agent_name
                )

                # Recount tokens
                token_count = await self._count_tokens(context, thinking_enabled=thinking_enabled)
                self.logger.info(
                    f"[ContextCompaction] After round {compaction_round}: {token_count} tokens"
                )

                # Early bail-out on diminishing returns: if this round barely reduced
                # the token count, the remaining tokens are in the kept-recent messages
                # (which compaction never touches). Further rounds will just re-summarize
                # tiny prefix messages and waste LLM calls. Let emergency truncation
                # handle the large kept messages instead.
                if prev_token_count > 0:
                    reduction_ratio = (prev_token_count - token_count) / prev_token_count
                    if reduction_ratio < MIN_REDUCTION_RATIO:
                        self.logger.info(
                            f"[ContextCompaction] Round {compaction_round} only reduced "
                            f"{reduction_ratio:.1%} (< {MIN_REDUCTION_RATIO:.0%} threshold). "
                            f"Kept-recent messages likely dominate the token count. "
                            f"Stopping compaction loop early to avoid wasting LLM calls."
                        )
                        break

            # --- Post-loop: check threshold BEFORE injecting system reminder ---
            # Rotate compacted messages to archive file first (non-fatal cleanup)
            try:
                await self.message_service.rotate_compacted_messages(
                    context.task_id, context.agent_name
                )
            except Exception as e:
                self.logger.warning(f"[ContextCompaction] Message rotation failed (non-fatal): {e}")

            # Refetch after rotation and get clean token count
            context.messages = await self.message_service.fetch_in_context_messages(
                context.task_id, context.agent_name
            )
            post_compact_token_count = await self._count_tokens(context, thinking_enabled=thinking_enabled)
            self.logger.info(
                f"[ContextCompaction] After compaction loop ({compaction_round} rounds): "
                f"{post_compact_token_count} tokens"
            )

            # Emergency truncation if still over threshold
            # IMPORTANT: Do this BEFORE injecting system reminder, because:
            # 1. The reminder adds tokens (MEMORY.md, lessons, todo) which would
            #    inflate the count and make the threshold check inaccurate.
            # 2. The reminder appended to a message that emergency truncation might
            #    drop would be completely wasted.
            if post_compact_token_count > threshold:
                self.logger.info(
                    f"[ContextCompaction] Still over threshold after {compaction_round} rounds "
                    f"({post_compact_token_count} > {threshold}). Performing emergency truncation."
                )
                try:
                    await self._emergency_truncate(context, threshold, thinking_enabled=thinking_enabled)
                except Exception as e:
                    self.logger.error(
                        f"[ContextCompaction] Emergency truncation failed (non-fatal): {e}. "
                        "Proceeding with oversized context."
                    )

                # Rotate after emergency truncation as well (non-fatal)
                try:
                    await self.message_service.rotate_compacted_messages(
                        context.task_id, context.agent_name
                    )
                except Exception as e:
                    self.logger.warning(f"[ContextCompaction] Post-emergency rotation failed (non-fatal): {e}")

                # Refetch after emergency truncation
                context.messages = await self.message_service.fetch_in_context_messages(
                    context.task_id, context.agent_name
                )

            # Now inject system reminder (MEMORY.md, lessons, todo list) into the
            # last user message. This is done AFTER emergency truncation so the
            # reminder is attached to a message that will actually be kept.
            await self._add_system_reminder(context, working_dir=self.working_dir)

            # Final refetch to pick up the injected reminder
            context.messages = await self.message_service.fetch_in_context_messages(
                context.task_id, context.agent_name
            )

            # Mark that compaction happened
            context.set_metadata('compacted', True)
            context.set_metadata('original_token_count', original_token_count)

            # Log final token count
            new_token_count = await self._count_tokens(context, thinking_enabled=thinking_enabled)
            self.logger.info(
                f"[ContextCompaction] Compaction complete. New token count: {new_token_count}"
            )

        return context

    async def _maybe_emit_budget_warning(self, context: HookContext, *, token_count: int, max_tokens: int) -> None:
        """Persist a standard notification warning (anti-spam via cooldown + marker lookback)."""
        # Cooldown
        if self._last_warn_turn is not None and (self._turn - self._last_warn_turn) < self.warn_cooldown_turns:
            return

        # Lookback suppression
        if self._recently_contains_marker(context.messages, marker=self._warn_marker, lookback=self.warn_lookback_messages):
            return

        # Load template
        template_path = os.path.join(os.path.dirname(__file__), "context_budget_warning_notification.md")
        try:
            with open(template_path, "r", encoding="utf-8") as f:
                template = f.read()
        except Exception as e:
            self.logger.warning(f"[ContextCompaction] Failed to load warning template {template_path}: {e}")
            template = f"## {self._warn_marker}\n\nContext usage is high. Please take durable notes now.\n"

        pct = (float(token_count) / float(max_tokens) * 100.0) if max_tokens else 100.0
        body = template.format(token_count=token_count, max_context_tokens=max_tokens, pct=pct)

        timestamp = datetime.now(self._tz).strftime("%Y-%m-%d %H:%M:%S %Z")
        wrapped = f"""<notification>
<source>system</source>
<timestamp>{timestamp}</timestamp>
<content>
{body}
</content>
</notification>"""

        msg = TaskMessage(
            task_id=context.task_id,
            agent_name=context.agent_name,
            role="user",
            content=[{"type": "text", "text": wrapped}],
            msg_type=MessageType.NOTIFICATION,
        )

        await self.message_service.on_receive_user_message(
            task_id=context.task_id,
            message=msg,
            agent_name=context.agent_name,
        )

        # Refresh so the agent sees it immediately this turn.
        context.messages = await self.message_service.fetch_in_context_messages(context.task_id, context.agent_name)

        self._last_warn_turn = self._turn
        self.logger.info(
            f"[ContextCompaction] Emitted budget warning at {token_count}/{max_tokens} tokens (~{pct:.1f}%)"
        )

    def _recently_contains_marker(self, messages: List[TaskMessage], *, marker: str, lookback: int) -> bool:
        if not messages or lookback <= 0:
            return False
        for msg in messages[-lookback:]:
            try:
                for block in msg.content or []:
                    if isinstance(block, dict) and block.get("type") == "text":
                        if marker in (block.get("text") or ""):
                            return True
            except Exception:
                continue
        return False
    
    async def _count_tokens(self, context: HookContext, *, thinking_enabled: bool) -> int:
        """Count total context tokens"""
        llm_messages = self.message_service.get_llm_compatible_messages(context.messages)
        return await self.token_counter.count_tokens(
            system_prompt=context.system_prompt,
            messages=llm_messages,
            tools=context.tools,
            # Use the agent's actual thinking setting for consistency.
            # (TokenCounter has internal fallbacks if CountTokens rejects tool_use histories.)
            thinking_enabled=thinking_enabled
        )
    
    def _find_safe_split_point(self, messages: List[TaskMessage], min_keep: int) -> int:
        """
        Find a safe split point that doesn't break tool_use/tool_result pairs.
        
        Claude requires every tool_result to have its corresponding tool_use in context.
        We need to find a split point where:
        1. We keep at least `min_keep` messages
        2. The kept messages don't start with orphaned tool_results
        
        Args:
            messages: List of all messages
            min_keep: Minimum number of messages to keep (from the end)
            
        Returns:
            Index where we should split (messages[:index] will be summarized)
        """
        if len(messages) <= min_keep:
            return 0
        
        # Start from the desired split point and move backwards if needed
        split_idx = len(messages) - min_keep
        
        # Check if the first kept message (messages[split_idx]) or subsequent messages
        # contain tool_results that reference tool_uses before the split
        while split_idx > 0:
            # Collect all tool_use IDs in the kept portion
            kept_tool_use_ids = set()
            for msg in messages[split_idx:]:
                if isinstance(msg.content, list):
                    for block in msg.content:
                        if isinstance(block, dict) and block.get("type") == "tool_use":
                            kept_tool_use_ids.add(block.get("id"))
            
            # Check if any tool_result in kept portion references a tool_use before split
            orphaned_results = False
            for msg in messages[split_idx:]:
                if isinstance(msg.content, list):
                    for block in msg.content:
                        if isinstance(block, dict) and block.get("type") == "tool_result":
                            tool_use_id = block.get("tool_use_id")
                            if tool_use_id and tool_use_id not in kept_tool_use_ids:
                                # This tool_result references a tool_use that would be summarized
                                orphaned_results = True
                                break
                if orphaned_results:
                    break
            
            if not orphaned_results:
                # Safe split point found
                break
            
            # Move split point back to include more messages
            split_idx -= 1
        
        return split_idx
    
    async def _compact(self, context: HookContext, keep_recent_override: int = None) -> None:
        """
        Compact old messages into a summary.

        Strategy:
        - Keep the last `keep_recent` messages intact
        - Ensure tool_use/tool_result pairs are not broken
        - Summarize all earlier messages into a single summary message
        - Append summary message with compact_source_msg_ids to mark compacted messages
        - Original messages are preserved but filtered out by fetch_in_context_messages

        Args:
            context: Hook context with messages
            keep_recent_override: Override the default keep_recent value (for aggressive compaction)
        """
        messages = context.messages
        keep_recent = keep_recent_override if keep_recent_override is not None else self.keep_recent

        if len(messages) <= keep_recent:
            self.logger.info(
                f"[ContextCompaction] Not enough messages to compact ({len(messages)} <= {keep_recent})"
            )
            return
        
        # Find safe split point that doesn't break tool_use/tool_result pairs
        split_idx = self._find_safe_split_point(messages, keep_recent)
        
        if split_idx == 0:
            self.logger.info(
                "[ContextCompaction] Cannot find safe split point, skipping compaction"
            )
            return
        
        # Split messages at safe point
        messages_to_summarize = messages[:split_idx]
        kept_count = len(messages) - split_idx
        
        self.logger.info(
            f"[ContextCompaction] Summarizing {len(messages_to_summarize)} messages, keeping {kept_count}"
        )
        
        # Collect IDs of messages being compacted
        compacted_ids = [msg.id for msg in messages_to_summarize]
        
        # Generate summary
        summary = await self._generate_summary(messages_to_summarize)
        
        # Load summary wrapper template
        wrapper_path = os.path.join(os.path.dirname(__file__), "context_summary_wrapper.md")
        with open(wrapper_path, 'r', encoding='utf-8') as f:
            wrapper_template = f.read()
        summary_text = wrapper_template.format(summary=summary, kept_count=kept_count)
        
        # Create summary message with compacted IDs in content_extend
        summary_message = TaskMessage(
            task_id=context.task_id,
            agent_name=context.agent_name,
            role="user",
            content=[{
                "type": "text",
                "text": summary_text
            }],
            content_extend={"compact_source_msg_ids": compacted_ids},
            msg_type=MessageType.CONTEXT_SUMMARY
        )
        
        # Insert summary message right after the last compacted message
        await self._insert_summary_message(context.task_id, context.agent_name, summary_message, compacted_ids)

    async def _emergency_truncate(self, context: HookContext, threshold: int, *, thinking_enabled: bool) -> None:
        """
        Last-resort hard truncation when summarization rounds fail to shrink context.

        Strategy: iteratively mark the oldest non-summary in-context messages as
        compacted (without generating a new summary) until tokens fall below threshold.
        Tool-use/tool-result pairs are dropped together to avoid orphans.
        """
        messages = context.messages
        token_count = await self._count_tokens(context, thinking_enabled=thinking_enabled)

        # Build a set of tool_use IDs and their paired tool_result IDs so we can
        # drop them together.
        tool_use_msg_indices: Dict[str, int] = {}   # tool_use block id -> msg index
        tool_result_msg_indices: Dict[str, int] = {}  # tool_use_id ref -> msg index
        for idx, msg in enumerate(messages):
            if not isinstance(msg.content, list):
                continue
            for block in msg.content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "tool_use":
                    tool_use_msg_indices[block.get("id", "")] = idx
                elif block.get("type") == "tool_result":
                    tool_result_msg_indices[block.get("tool_use_id", "")] = idx

        dropped_ids: List[str] = []
        dropped_indices: set = set()
        i = 0
        while token_count > threshold and i < len(messages):
            msg = messages[i]
            # Never drop summary messages (they replaced earlier content)
            if msg.msg_type == MessageType.CONTEXT_SUMMARY:
                i += 1
                continue
            if i in dropped_indices:
                i += 1
                continue

            # Collect this message and any paired tool messages
            batch_indices = {i}
            if isinstance(msg.content, list):
                for block in msg.content:
                    if not isinstance(block, dict):
                        continue
                    if block.get("type") == "tool_use":
                        pair_idx = tool_result_msg_indices.get(block.get("id", ""))
                        if pair_idx is not None:
                            batch_indices.add(pair_idx)
                    elif block.get("type") == "tool_result":
                        pair_idx = tool_use_msg_indices.get(block.get("tool_use_id", ""))
                        if pair_idx is not None:
                            batch_indices.add(pair_idx)

            for bi in batch_indices:
                if bi not in dropped_indices:
                    dropped_indices.add(bi)
                    dropped_ids.append(messages[bi].id)

            # Estimate remaining tokens by temporarily filtering
            remaining = [m for j, m in enumerate(messages) if j not in dropped_indices]
            old_messages = context.messages
            context.messages = remaining
            token_count = await self._count_tokens(context, thinking_enabled=thinking_enabled)
            context.messages = old_messages

            i += 1

        if not dropped_ids:
            self.logger.error("[ContextCompaction] Emergency truncation could not drop any messages")
            return

        self.logger.info(
            f"[ContextCompaction] Emergency truncation: dropping {len(dropped_ids)} messages "
            f"to bring context from {await self._count_tokens(context, thinking_enabled=thinking_enabled)} "
            f"to ~{token_count} tokens (threshold {threshold})"
        )

        # Mark dropped messages as compacted (reuse existing mechanism)
        truncation_note = TaskMessage(
            task_id=context.task_id,
            agent_name=context.agent_name,
            role="user",
            content=[{
                "type": "text",
                "text": (
                    "<system_note>\n"
                    f"Emergency context truncation: {len(dropped_ids)} older messages were dropped "
                    "because context exceeded the maximum token limit after repeated compaction attempts. "
                    "Check your MEMORY.md and todo list for prior state.\n"
                    "</system_note>"
                )
            }],
            content_extend={"compact_source_msg_ids": dropped_ids},
            msg_type=MessageType.CONTEXT_SUMMARY,
        )
        await self._insert_summary_message(context.task_id, context.agent_name, truncation_note, dropped_ids)

        # Refresh context
        context.messages = await self.message_service.fetch_in_context_messages(
            context.task_id, context.agent_name
        )

    async def _generate_summary(self, messages: List[TaskMessage]) -> str:
        """Use LLM to generate a summary of messages"""
        # Convert messages to readable format
        conversation_text = self._format_messages_for_summary(messages)
        
        # Load and format summary prompt from hooks folder
        prompt_path = os.path.join(os.path.dirname(__file__), "context_summary_prompt.md")
        with open(prompt_path, 'r', encoding='utf-8') as f:
            summary_prompt_template = f.read()
        prompt = summary_prompt_template.format(conversation=conversation_text)
        
        # Call LLM
        response = await self.llm_client.call_llm(
            messages=[{"role": "user", "content": prompt}],
            system_prompt="You are a helpful assistant that creates concise, accurate summaries.",
            tools=None,
            enable_thinking=False
        )
        
        # Extract text from response
        if response and "content" in response:
            for block in response["content"]:
                if block.get("type") == "text":
                    return block.get("text", "")
        
        return "Summary generation failed."
    
    def _format_messages_for_summary(self, messages: List[TaskMessage]) -> str:
        """Format messages into a readable text for summarization.

        Applies two levels of truncation to prevent exceeding the compaction LLM's context:
        1. Per-message truncation: Each message limited to ~5000 chars
        2. Total truncation: Final text limited to ~300K chars (~100K tokens)

        The compaction LLM has 150K token context. We target ~100K tokens for the
        conversation text, leaving room for system prompt and output.
        """
        # Target max chars for the conversation text (~3 chars per token)
        # Leave headroom for system prompt, summary prompt template, and output tokens
        MAX_TOTAL_CHARS = 300000  # ~100K tokens
        MAX_PER_MESSAGE_CHARS = 5000

        formatted_parts = []
        total_chars = 0

        for i, msg in enumerate(messages):
            role = msg.role.upper()
            content_text = self._extract_text_from_content(msg.content)

            # Truncate very long individual messages
            if len(content_text) > MAX_PER_MESSAGE_CHARS:
                content_text = content_text[:MAX_PER_MESSAGE_CHARS] + "\n... [truncated for summary]"

            part = f"### Turn {i+1} ({role})\n{content_text}"
            part_len = len(part) + 2  # +2 for "\n\n" separator

            # Check if adding this part would exceed total limit
            if total_chars + part_len > MAX_TOTAL_CHARS:
                remaining = len(messages) - i
                formatted_parts.append(f"### ... [{remaining} more turns truncated due to length] ...")
                self.logger.warning(
                    f"[ContextCompaction] Truncated {remaining} turns from summary input due to length limit"
                )
                break

            formatted_parts.append(part)
            total_chars += part_len

        return "\n\n".join(formatted_parts)
    
    def _extract_text_from_content(self, content: Any) -> str:
        """Extract text from message content"""
        if isinstance(content, str):
            return content
        
        if isinstance(content, list):
            texts = []
            for item in content:
                if isinstance(item, dict):
                    item_type = item.get("type", "")
                    if item_type == "text":
                        texts.append(item.get("text", ""))
                    elif item_type == "tool_use":
                        texts.append(f"[Tool: {item.get('name', 'unknown')}]")
                    elif item_type == "tool_result":
                        result_content = item.get("content", "")
                        if isinstance(result_content, str):
                            texts.append(f"[Tool Result: {result_content[:500]}...]" if len(result_content) > 500 else f"[Tool Result: {result_content}]")
                    elif item_type == "thinking":
                        texts.append("[Thinking block]")
            return "\n".join(texts)
        
        return str(content)
    
    async def _insert_summary_message(
        self,
        task_id: str,
        agent_name: str,
        summary_message: TaskMessage,
        compacted_msg_ids: List[str]
    ) -> None:
        """Insert summary message right after the last compacted message in the full message list.

        The summary message should be positioned so that when fetch_in_context_messages filters
        out compacted messages, the result is: [summary, kept_messages...]

        Args:
            task_id: The task ID
            agent_name: The agent name
            summary_message: The summary message to insert
            compacted_msg_ids: List of message IDs that were compacted into this summary
        """
        agent_data = await self.message_service._fetch_agent_data(task_id, agent_name)
        if not agent_data:
            return

        # Find the position right after the last compacted message in the full message list
        compacted_id_set = set(compacted_msg_ids)
        last_compacted_idx = -1

        for i, msg in enumerate(agent_data.messages):
            if msg.id in compacted_id_set:
                last_compacted_idx = i

        # Insert right after the last compacted message
        # If no compacted messages found (shouldn't happen), insert at beginning
        insert_idx = last_compacted_idx + 1 if last_compacted_idx >= 0 else 0

        agent_data.messages.insert(insert_idx, summary_message)
        await self.message_service._save_agent_data(agent_data)

        self.logger.info(
            f"[ContextCompaction] Inserted summary message at index {insert_idx}, compacted {len(compacted_msg_ids)} messages"
        )
    
    async def _add_system_reminder(self, context: HookContext, working_dir: str = None) -> None:
        """Add a system reminder to the last user message about the compaction, including todo list and memory"""
        # Load the reminder template from hooks folder
        reminder_path = os.path.join(os.path.dirname(__file__), "context_compacted_reminder.md")
        with open(reminder_path, 'r', encoding='utf-8') as f:
            reminder_template = f.read()

        # Fetch current todo list from storage (authoritative source)
        todos = await self.message_service.get_todos(context.task_id, context.agent_name)

        # Format the todo list
        todo_section = self._format_todo_list(todos)

        # Auto-inject MEMORY.md and lessons.md content if they exist
        memory_content = "(No MEMORY.md found - consider creating one to persist your state)"
        lessons_content = ""
        if working_dir:
            # Read MEMORY.md
            memory_path = os.path.join(working_dir, "memories", "MEMORY.md")
            if os.path.exists(memory_path):
                try:
                    with open(memory_path, 'r', encoding='utf-8') as f:
                        content = f.read().strip()
                    if content:
                        memory_content = content
                        self.logger.info("[ContextCompaction] Auto-injected MEMORY.md content")
                    else:
                        memory_content = "(MEMORY.md is empty - consider updating it with your current state)"
                        self.logger.info("[ContextCompaction] MEMORY.md exists but is empty")
                except Exception as e:
                    self.logger.warning(f"[ContextCompaction] Failed to read MEMORY.md: {e}")
                    memory_content = "(Failed to read MEMORY.md)"

            # Read lessons.md
            lessons_path = os.path.join(working_dir, "memories", "lessons.md")
            if os.path.exists(lessons_path):
                try:
                    with open(lessons_path, 'r', encoding='utf-8') as f:
                        content = f.read().strip()
                    if content:
                        lessons_content = f"\n\n## Your Learned Lessons\n{content}"
                        self.logger.info("[ContextCompaction] Auto-injected lessons.md content")
                except Exception as e:
                    self.logger.warning(f"[ContextCompaction] Failed to read lessons.md: {e}")

        # Build user prompt section
        user_prompt_section = ""
        if self.user_prompt:
            user_prompt_section = (
                "## Original User Research Request (verbatim)\n\n"
                "<user_prompt>\n"
                f"{self.user_prompt}\n"
                "</user_prompt>\n\n"
                "All your work must stay aligned with this request."
            )

        # In end-to-end mode, append critical state so the agent doesn't
        # lose awareness of the quality gate and refutation tracking.
        end_to_end_section = ""
        if self.end_to_end_mode and working_dir:
            e2e_lines = [
                "\n## End-to-End Mode: Active",
                "This task is running in **end-to-end mode**. A quality gate is enforced on "
                "`FinalizeProposal` (all 7 evaluation scores must be >= 3).",
                "- Check `refuted_proposals_paths.md` in the shared workspace for proposals that failed the gate.",
                "- Check `./research_proposal_drafts/` for your in-progress and evaluated drafts.",
                "- **If you have already received a fallback notification** (the system told you the quality gate "
                "has been relaxed), the gate is now OFF — finalize the best available draft immediately "
                "using `FinalizeProposal`. Do NOT start new proposals.",
                "- Otherwise, do NOT revisit or resurrect refuted proposals — pursue fresh directions.",
            ]
            end_to_end_section = "\n".join(e2e_lines)

        # Build the reminder with memory content, lessons, todo section, and user prompt
        reminder_text = reminder_template.format(
            memory_content=memory_content + lessons_content,
            todo_section=todo_section,
            user_prompt_section=user_prompt_section,
        )
        if end_to_end_section:
            reminder_text += "\n" + end_to_end_section
        
        # Fetch non-compacted messages to find the last user message in context
        in_context_messages = await self.message_service.fetch_in_context_messages(
            context.task_id, context.agent_name
        )
        if not in_context_messages:
            return
        
        # Find the last user message among non-compacted messages
        last_user_msg_id = None
        for msg in reversed(in_context_messages):
            if msg.role == "user":
                last_user_msg_id = msg.id
                break
        
        if not last_user_msg_id:
            return
        
        # Update the message in storage
        agent_data = await self.message_service._fetch_agent_data(context.task_id, context.agent_name)
        if not agent_data:
            return
        
        for msg in agent_data.messages:
            if msg.id == last_user_msg_id:
                # Append system reminder to content
                if isinstance(msg.content, list):
                    msg.content.append({
                        "type": "text",
                        "text": reminder_text
                    })
                elif isinstance(msg.content, str):
                    msg.content = [
                        {"type": "text", "text": msg.content},
                        {"type": "text", "text": reminder_text}
                    ]
                break
        
        # Save updated messages
        await self.message_service._save_agent_data(agent_data)
        self.logger.info("[ContextCompaction] Added system reminder with todo list to last user message")
    
    def _format_todo_list(self, todos: List[Dict[str, Any]]) -> str:
        """Format the todo list as a markdown section"""
        if not todos:
            return "## Your Current Todo List\nNo active todos."
        
        # Build markdown table
        lines = [
            "## Your Current Todo List (Authoritative)",
            "| ID | Task | Status |",
            "|-----|------|--------|"
        ]
        
        for todo in todos:
            todo_id = todo.get("id", "?")
            content = todo.get("content", "Unknown task")
            status = todo.get("status", "pending")
            # Escape pipe characters in content
            content = content.replace("|", "\\|")
            # Truncate long content
            if len(content) > 80:
                content = content[:77] + "..."
            lines.append(f"| {todo_id} | {content} | {status} |")
        
        # Add summary of status counts
        status_counts = {}
        for todo in todos:
            status = todo.get("status", "pending")
            status_counts[status] = status_counts.get(status, 0) + 1
        
        summary_parts = [f"{count} {status}" for status, count in status_counts.items()]
        lines.append(f"\n*Summary: {', '.join(summary_parts)}*")
        
        return "\n".join(lines)
