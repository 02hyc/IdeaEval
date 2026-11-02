"""
TodoWrite tool for managing structured task lists during coding sessions

Todos are stored as metadata in the agent's message file via the message service.
"""
from typing import Dict, Any, List, Optional
from toolkit.tool_handlers.base_tool_handler import BaseToolHandler, ToolState, ToolFailedError
from toolkit.tool_handlers.tool_categories import ToolName


class TodoWriteToolHandler(BaseToolHandler):
    """Tool for creating and managing structured task lists"""
    
    def __init__(self, tool_state: ToolState):
        super().__init__(tool_state=tool_state)
        self.requires_confirmation = False
    
    @property
    def name(self) -> str:
        return ToolName.TODO_WRITE
    
    @property
    def display_result_type(self) -> str:
        return "todo_result"

    @property
    def input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "todos": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "content": {
                                "type": "string",
                                "minLength": 1
                            },
                            "status": {
                                "type": "string",
                                "enum": ["pending", "in_progress", "completed"]
                            },
                            "id": {
                                "type": "string"
                            }
                        },
                        "required": ["content", "status", "id"],
                        "additionalProperties": False
                    },
                    "description": "The updated todo list"
                },
                "merge": {
                    "type": "boolean",
                    "description": "If true, merge with existing todos by ID. If false, replace all todos.",
                    "default": False
                }
            },
            "required": ["todos"],
            "additionalProperties": False,
            "$schema": "http://json-schema.org/draft-07/schema#"
        }
    
    def _check_message_service(self) -> None:
        """Check if message service and agent_name are configured"""
        if not self.tool_state.message_service:
            raise ToolFailedError(
                "Error: Cannot access todos - message_service not configured in toolkit. "
                "Ensure the Toolkit is initialized with message_service parameter."
            )
        if not self.tool_state.agent_name:
            raise ToolFailedError(
                "Error: Cannot access todos - agent_name not configured in toolkit. "
                "Ensure the Toolkit is initialized with agent_name parameter."
            )
    
    async def _load_todos(self) -> List[Dict[str, str]]:
        """Load existing todos from the agent's message file via message service"""
        self._check_message_service()
        return await self.tool_state.message_service.get_todos(
            task_id=self.tool_state.task_id,
            agent_name=self.tool_state.agent_name
        )
    
    async def _save_todos(self, todos: List[Dict[str, str]]) -> None:
        """Save todos to the agent's message file via message service"""
        self._check_message_service()
        success = await self.tool_state.message_service.update_todos(
            task_id=self.tool_state.task_id,
            agent_name=self.tool_state.agent_name,
            todos=todos
        )
        if not success:
            raise ToolFailedError(
                f"Error: Failed to save todos for agent '{self.tool_state.agent_name}' in task '{self.tool_state.task_id}'"
            )
    
    def _merge_todos(
        self, 
        existing: List[Dict[str, str]], 
        updates: List[Dict[str, str]]
    ) -> List[Dict[str, str]]:
        """Merge updates into existing todos by ID"""
        # Create a map of existing todos by ID
        todo_map = {todo['id']: todo for todo in existing}
        
        # Apply updates
        for update in updates:
            todo_map[update['id']] = update
        
        # Return as list, preserving order of existing + new
        result = []
        seen_ids = set()
        
        # First, add existing todos (in their original order, with updates applied)
        for todo in existing:
            if todo['id'] in todo_map:
                result.append(todo_map[todo['id']])
                seen_ids.add(todo['id'])
        
        # Then, add new todos that weren't in existing
        for update in updates:
            if update['id'] not in seen_ids:
                result.append(update)
        
        return result
    
    async def execute_async(
        self, 
        todos: List[Dict[str, str]], 
        merge: bool = False
    ) -> Dict[str, Any]:
        """Execute the TodoWrite tool to manage task lists"""
        
        # Validate the todos structure
        validation_result = self._validate_todos(todos)
        if validation_result and not validation_result.startswith("Warning:"):
            raise ToolFailedError(validation_result)

        # Handle merge logic
        if merge:
            existing_todos = await self._load_todos()
            final_todos = self._merge_todos(existing_todos, todos)
        else:
            final_todos = todos
        
        # Save the todos
        await self._save_todos(final_todos)

        # Notify the notification center that todos were updated
        if self.tool_state.notification_center:
            self.tool_state.notification_center.on_todo_updated()

        # Return success message
        result_message = (
            "Todos have been modified successfully. "
            "Ensure that you continue to use the todo list to track your progress. "
            "Please proceed with the current tasks if applicable.\n"
            "IMPORTANT: Mark todos as completed as soon as you are done with each task."
        )
        
        if validation_result and validation_result.startswith("Warning:"):
            result_message += f"\n{validation_result}"
        
        return {
            'tool_success': True,
            'result': result_message,
            'display_result': {
                'todos': final_todos
            }
        }
    
    def _validate_todos(self, todos: List[Dict[str, str]]) -> Optional[str]:
        """Validate the todos list structure and business rules"""
        
        if not isinstance(todos, list):
            return "Error: todos must be a list"
        
        if len(todos) == 0:
            return "Error: todos list cannot be empty"
        
        # Track IDs to ensure uniqueness
        seen_ids = set()
        in_progress_count = 0
        
        for i, todo in enumerate(todos):
            # Validate required fields
            if not isinstance(todo, dict):
                return f"Error: Todo item {i} must be an object"
            
            required_fields = ["content", "status", "id"]
            for field in required_fields:
                if field not in todo:
                    return f"Error: Todo item {i} missing required field: {field}"
            
            # Validate content
            content = todo.get("content", "")
            if not isinstance(content, str) or len(content.strip()) == 0:
                return f"Error: Todo item {i} content must be a non-empty string"
            
            # Validate status
            status = todo.get("status", "")
            valid_statuses = ["pending", "in_progress", "completed"]
            if status not in valid_statuses:
                return f"Error: Todo item {i} has invalid status '{status}'. Must be one of: {', '.join(valid_statuses)}"
            
            # Count in_progress items
            if status == "in_progress":
                in_progress_count += 1
            
            # Validate ID uniqueness
            todo_id = todo.get("id", "")
            if not isinstance(todo_id, str) or len(todo_id.strip()) == 0:
                return f"Error: Todo item {i} ID must be a non-empty string"
            
            if todo_id in seen_ids:
                return f"Error: Duplicate todo ID '{todo_id}' found"
            
            seen_ids.add(todo_id)
        
        # Business rule: Only one task should be in_progress at a time
        if in_progress_count > 1:
            return (
                f"Warning: Found {in_progress_count} tasks in 'in_progress' status. "
                "Consider having only one task in progress at a time for better focus."
            )

        # Check if todo list is too long and has completed todos
        completed_count = sum(1 for todo in todos if todo.get("status") == "completed")
        if completed_count > 10:
            return (
                f"Warning: Todo list is too long ({len(todos)} todos). "
                f"You have {completed_count} completed todo(s). "
                "Consider compressing the completed todos to keep the list manageable."
            )

        return None
    
    async def get_current_todos(self) -> List[Dict[str, str]]:
        """Get the current todo list"""
        return await self._load_todos()
    
    async def get_todo_by_id(self, todo_id: str) -> Optional[Dict[str, str]]:
        """Get a specific todo by ID"""
        todos = await self._load_todos()
        for todo in todos:
            if todo["id"] == todo_id:
                return todo.copy()
        return None
    
    async def get_todos_by_status(self, status: str) -> List[Dict[str, str]]:
        """Get all todos with a specific status"""
        todos = await self._load_todos()
        return [todo.copy() for todo in todos if todo["status"] == status]
