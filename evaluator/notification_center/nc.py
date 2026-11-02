import asyncio
import os
from datetime import datetime
from typing import List, Optional

from utils.logger import Logger
from utils.timezone import get_timezone
from message_service.message import TaskMessage, MessageType


# Shanghai timezone for all timestamps
SHANGHAI_TZ = get_timezone("Asia/Shanghai")


class NotificationCenter:
    def __init__(
        self,
        task_id: str,
        working_dir: str,
        message_service,
        user_prompt: str = "",
        agent_id: Optional[int] = None,
        enqueue_initial_notification: bool = True,
    ):
        self.logger = Logger()

        self.task_id = task_id
        self.notification_queue = asyncio.Queue()

        self.working_dir = working_dir
        self.message_service = message_service
        self.agent_id = agent_id
        # Use agent_id in agent_name if provided (for multi-agent mode)
        if agent_id is not None:
            self.agent_name = f"lead_agent_{agent_id:02d}"
        else:
            self.agent_name = "lead_agent"
        self.user_prompt = user_prompt

        # Turn counter for tracking updates
        self.turn_count = 0
        self.last_todo_update_turn = 0
        self.todo_reminder_interval = 20
        self.last_todo_reminder_turn = 0
        # Todo reminder is the only periodic notification now

        if enqueue_initial_notification:
            initial_msg = self._build_initial_notification()
            self.notification_queue.put_nowait(self._create_notification("user_request", initial_msg))

    def _get_shanghai_time(self) -> str:
        """Get current time in Shanghai timezone"""
        return datetime.now(SHANGHAI_TZ).strftime("%Y-%m-%d %H:%M:%S %Z")

    def _create_notification(self, source: str, content: str) -> TaskMessage:
        """Create a notification with timestamp"""
        timestamp = self._get_shanghai_time()
        formatted_content = f"""<notification>
<source>{source}</source>
<timestamp>{timestamp}</timestamp>
<content>
{content}
</content>
</notification>"""
        return TaskMessage(
            task_id=self.task_id,
            role="user",
            content=[{"type": "text", "text": formatted_content}],
            msg_type=MessageType.NOTIFICATION
        )

    def _build_initial_notification(self) -> str:
        """Build the initial notification containing the user's research prompt."""
        shared_skills_path = os.path.join(os.path.dirname(self.working_dir), "internal_context", "skills")
        if not os.path.isdir(shared_skills_path):
            shared_skills_path = os.path.join(self.working_dir, "internal_context", "skills")
        return f"""# Research Request

<user_prompt>
{self.user_prompt}
</user_prompt>

Follow the workflow in your system instructions (Steps 1-7).

## Important Notes

- Keep all exploration, filtering, and proposal writing tightly aligned with the user's topic, scope, and explicit preferences. Diversity, preferred directions, and old drafts are secondary heuristics only.
- Start by reading the relevant skill files in `{shared_skills_path}/` (especially `literature-review`, `existing-work-exploration`, `research-proposal-writing`, and `verification-scope-and-baselines`).
- Always do deep literature review before proposing — search broadly (web + local KB), follow citation trails, and make evidence durable (scrape/summarize).
- Build the knowledge base needed for this request — scrape, summarize, and consolidate papers into topic files that support the user's goal. Do not broaden into unrelated areas unless necessary.
- Prioritize quality without endless iteration. Use evaluation feedback for up to two revision rounds, then finalize the best available version.
"""

    async def add_notification(self, notification: TaskMessage):
        await self.notification_queue.put(notification)

    async def fetch_notifications(self, include_empty: bool = True) -> List[TaskMessage]:
        notifications = []
        while not self.notification_queue.empty():
            notifications.append(await self.notification_queue.get())

        if len(notifications) == 0:
            if not include_empty:
                return []
            # Generate a gentle nudge as fallback
            fallback = self._get_idle_fallback()
            return [fallback] if fallback else []
        return notifications

    def _get_idle_fallback(self) -> Optional[TaskMessage]:
        """When no notifications are queued and the agent didn't call tools, send a gentle nudge."""
        return self._create_notification("system",
            "You did not call any tools on your last turn. Continue working on the current user's goal — "
            "explore, write, evaluate, or finalize proposals that serve that goal.")

    def is_notification_center_empty(self) -> bool:
        return self.notification_queue.empty()

    # ========== Todo List Tracking ==========

    async def check_todo_status(self) -> Optional[str]:
        """Check if agent needs todo reminder"""
        todos = await self.message_service.get_todos(self.task_id, self.agent_name)

        if not todos:
            if self.turn_count > 5:
                return "You have not created a todo list yet. For long-horizon tasks, use TodoWrite to create a structured task list to track your progress."
        else:
            turns_since_update = self.turn_count - self.last_todo_update_turn
            turns_since_reminder = self.turn_count - self.last_todo_reminder_turn
            if turns_since_update >= self.todo_reminder_interval and turns_since_reminder >= self.todo_reminder_interval:
                incomplete = [t for t in todos if t.get('status') != 'completed']
                in_progress = [t for t in todos if t.get('status') == 'in_progress']
                if incomplete:
                    if in_progress:
                        if turns_since_reminder >= self.todo_reminder_interval * 2:
                            return (
                                f"Your todo list has {len(incomplete)} incomplete items (including {len(in_progress)} in progress). "
                                "If you're actively working on a task, you can ignore this reminder. Update todos when you complete or change tasks."
                            )
                        return None
                    else:
                        return (
                            f"Your todo list has {len(incomplete)} incomplete items but none are marked 'in_progress'. "
                            "Consider updating your todos: mark your current task as in_progress, or mark completed tasks as done."
                        )
        return None

    def on_todo_updated(self):
        """Called when todos are updated by the agent"""
        self.last_todo_update_turn = self.turn_count

    # ========== Turn Complete Handling ==========

    async def on_turn_complete(self):
        """Called after each agent turn to generate periodic notifications"""
        self.turn_count += 1
        await self._generate_periodic_notifications()

    async def _generate_periodic_notifications(self):
        """Generate notifications based on current state"""
        # Check todo status
        todo_msg = await self.check_todo_status()
        if todo_msg:
            self.last_todo_reminder_turn = self.turn_count
            await self.add_notification(self._create_notification("system", todo_msg))
