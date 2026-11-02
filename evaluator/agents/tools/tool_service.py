"""Tool service implementation for managing tool instances per agent."""

from __future__ import annotations

import asyncio
import importlib
import importlib.util
import inspect
import os
import uuid
from typing import Any, Dict, Iterable, List, Optional, Tuple, Type

from .base_tool import BaseTool
from utils.logger import Logger


logger = Logger()


class ToolWrapper:
    """Lightweight proxy around tool handlers with lazy instantiation."""

    def __init__(
        self,
        *,
        name: str,
        tool_instance: BaseTool,
        handler_module: str,
        handler_class_name: str,
        tool_kind: str,
        tool_service: "ToolService",  # Forward reference
        agent: Optional[Any] = None,
    ) -> None:
        self.name = name
        self._tool_instance = tool_instance
        self._handler_module = handler_module
        self._handler_class_name = handler_class_name
        self._tool_kind = tool_kind  # "remote" or "internal"
        self._tool_service = tool_service  # Reference to ToolService for getting tool_state
        self.agent = agent

        self._handler: Optional[Any] = None
        self._requires_confirmation: Optional[bool] = None

    def __getattribute__(self, attr: str):  # noqa: D401 - delegation helper
        """Return attributes from wrapper or underlying handler."""
        if attr == "__class__":
            handler = object.__getattribute__(self, "_handler")
            if handler is not None:
                return handler.__class__

            handler_module = object.__getattribute__(self, "_handler_module")
            handler_class_name = object.__getattribute__(self, "_handler_class_name")
            try:
                module = importlib.import_module(handler_module)
                handler_cls = getattr(module, handler_class_name)
                return handler_cls
            except Exception:
                return ToolWrapper
        return object.__getattribute__(self, attr)

    def __getattr__(self, attr: str) -> Any:
        handler = self._ensure_handler(require_event_loop=(self._tool_kind == "remote"))
        return getattr(handler, attr)

    @property
    def input_schema(self) -> Dict[str, Any]:
        return self._tool_instance.input_schema

    @property
    def description(self) -> str:
        return self._tool_instance.description

    def to_claude_tool(self) -> Dict[str, Any]:
        return self._tool_instance.to_claude_tool()

    def validate_input(self, **kwargs) -> bool:
        schema = self.input_schema
        required = schema.get("required", [])
        for field in required:
            if field not in kwargs or kwargs[field] is None:
                raise ValueError(f"Missing required parameter: {field}")

        properties = schema.get("properties", {})
        additional_allowed = schema.get("additionalProperties", True)

        for key, value in kwargs.items():
            if key not in properties:
                if not additional_allowed:
                    raise ValueError(f"Unexpected parameter: {key}")
                continue

            expected_type = properties[key].get("type")
            if expected_type is None or value is None:
                continue

            if expected_type == "array" and not isinstance(value, list):
                raise ValueError(f"Parameter '{key}' must be an array")
            if expected_type == "string" and not isinstance(value, str):
                raise ValueError(f"Parameter '{key}' must be a string")
            if expected_type == "integer" and not isinstance(value, int):
                raise ValueError(f"Parameter '{key}' must be an integer")
            if expected_type == "number" and not isinstance(value, (int, float)):
                raise ValueError(f"Parameter '{key}' must be a number")
            if expected_type == "boolean" and not isinstance(value, bool):
                raise ValueError(f"Parameter '{key}' must be a boolean")

        return True

    def requires_confirmation(self) -> bool:
        if self._requires_confirmation is None:
            handler = self._ensure_handler(require_event_loop=(self._tool_kind == "remote"))
            self._requires_confirmation = getattr(handler, "requires_confirmation", False)
        return bool(self._requires_confirmation)

    async def execute_async(self, **kwargs) -> Any:
        handler = self._ensure_handler(require_event_loop=True)
        if hasattr(handler, "execute_async") and inspect.iscoroutinefunction(handler.execute_async):
            return await handler.execute_async(**kwargs)
        if hasattr(handler, "execute_async"):
            return handler.execute_async(**kwargs)
        # Fallback to execute with params dict
        return await self.execute(kwargs)

    async def execute(self, params: Dict[str, Any]) -> Any:
        handler = self._ensure_handler(require_event_loop=True)
        execute_callable = getattr(handler, "execute", None)
        result = None
        if execute_callable is None:
            raise AttributeError(f"Handler for tool '{self.name}' does not implement execute()")
        if inspect.iscoroutinefunction(execute_callable):
            result = await execute_callable(params)
        else:
            result = execute_callable(params)
        return result
    
    async def execute_with_confirmation(self, params: Dict[str, Any]) -> Any:
        """
        Execute tool with confirmation if required.
        For Write/Edit/MultiEdit tools, handles preview → confirm → apply flow.
        For other tools, handles normal confirmation flow.
        """
        # Check if confirmation is needed
        needs_confirmation = self.requires_confirmation()
        auto_approve = getattr(self.agent, 'auto_approve_tools', False) if self.agent else False
        
        # ExitPlanMode always requires confirmation, even when auto_approve is True
        if self.name == 'ExitPlanMode' and needs_confirmation:
            auto_approve = False
        
        # Special handling for Write/Edit/MultiEdit tools
        if self.name in ["Write", "Edit", "MultiEdit"]:
            return await self._execute_edit_tool_with_preview(params, needs_confirmation, auto_approve)
        
        # Normal tool execution with confirmation
        if needs_confirmation and not auto_approve:
            confirmation_handler = getattr(self.agent, 'confirmation_handler', None) if self.agent else None
            if confirmation_handler:
                confirmed = await confirmation_handler(self.name, params)
                if confirmed is False:
                    # User rejected
                    return {
                        "success": False,
                        "result": f"Tool execution denied by user: {self.name}",
                        "display_result": {
                            'type': 'text',
                            'data': {},
                            'tool_success': False,
                            'error': f"Tool execution denied by user: {self.name}",
                        },
                        "error": f"Tool execution denied by user: {self.name}",
                        "error_type": "UserRejection"
                    }
        
        # Execute normally
        result = await self.execute(params)
        
        # Special handling for ExitPlanMode tool - modify the result message when approved
        if self.name == 'ExitPlanMode' and result.get('success'):
            result['result'] = "User has approved your plan. Please execute the task according to your plan."
        
        return result
    
    async def _execute_edit_tool_with_preview(
        self, params: Dict[str, Any], needs_confirmation: bool, auto_approve: bool
    ) -> Any:
        """
        Execute Write/Edit/MultiEdit tools with preview → confirm → apply flow.
        """
        handler = self._ensure_handler(require_event_loop=True)
        
        # Mark file as read to bypass read check
        file_path = params.get("file_path")
        if file_path:
            handler.mark_file_as_read(file_path)
        
        # If needs confirmation and not auto-approved
        if needs_confirmation and not auto_approve:
            confirmation_handler = getattr(self.agent, 'confirmation_handler', None) if self.agent else None
            if confirmation_handler:
                # Let confirmation_handler handle preview display and confirmation
                # Pass a callback that can execute preview
                confirmed = await confirmation_handler(
                    self.name, 
                    params,
                    preview_callback=lambda: self.execute({**params, "update_file": False})
                )
                
                if confirmed is False:
                    # User rejected
                    return {
                        "success": False,
                        "result": f"Tool execution denied by user: {self.name}",
                        "display_result": {
                            'type': 'text',
                            'data': {},
                            'tool_success': False,
                            'error': f"Tool execution denied by user: {self.name}",
                        },
                        "error": f"Tool execution denied by user: {self.name}",
                        "error_type": "UserRejection"
                    }
                
                # User confirmed, execute apply
                apply_params = dict(params)
                apply_params["update_file"] = True
                return await self.execute(apply_params)
        
        # Auto-approve path: preview for validation, then apply
        try:
            preview_params = dict(params)
            preview_params["update_file"] = False
            await self.execute(preview_params)
        except Exception:
            # Ignore preview errors
            pass
        
        # Execute apply
        apply_params = dict(params)
        apply_params["update_file"] = True
        return await self.execute(apply_params)

    async def cleanup(self) -> None:
        if self._handler is not None:
            cleanup_fn = getattr(self._handler, "cleanup", None)
            if cleanup_fn:
                if inspect.iscoroutinefunction(cleanup_fn):
                    await cleanup_fn()
                else:
                    cleanup_fn()

        self._handler = None
        self._requires_confirmation = None

    def _ensure_handler(self, *, require_event_loop: bool) -> Any:
        if self._handler is not None:
            return self._handler

        # Get tool_state from ToolService instead of creating it
        tool_state = self._tool_service.get_tool_state(self._tool_kind)

        module = importlib.import_module(self._handler_module)
        handler_cls = getattr(module, self._handler_class_name, None)
        if handler_cls is None:
            raise AttributeError(
                f"Handler class '{self._handler_class_name}' not found in module '{self._handler_module}'"
            )

        handler = handler_cls(tool_state=tool_state)
        if self.agent is not None:
            setattr(handler, "agent", self.agent)
        self._handler = handler
        self._requires_confirmation = getattr(handler, "requires_confirmation", False)
        return handler


class ToolService:
    """Create and manage tool instances for an agent."""

    def __init__(
        self,
        *,
        agent: Optional[Any] = None,
        working_dir: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> None:
        self.agent = agent
        self.working_dir = os.path.abspath(working_dir or os.getcwd())
        self.session_id = session_id or uuid.uuid4().hex

        self._tool_wrappers: Dict[str, ToolWrapper] = {}
        self._claude_tools: List[Dict[str, Any]] = []

        # Tool state management - ToolService now owns tool states
        self._remote_tool_state: Optional[Any] = None
        self._internal_tool_state: Optional[Any] = None

        self._load_tools()

    def set_agent(self, agent: Any) -> None:
        self.agent = agent
        for wrapper in self._tool_wrappers.values():
            wrapper.agent = agent

    def get_tool(self, name: str) -> ToolWrapper:
        if name not in self._tool_wrappers:
            raise ValueError(f"Tool '{name}' is not available")
        return self._tool_wrappers[name]

    def get_all_tools(self) -> Dict[str, ToolWrapper]:
        return dict(self._tool_wrappers)

    def get_claude_tools(self) -> Iterable[Dict[str, Any]]:
        return list(self._claude_tools)

    def get_tool_state(self, tool_kind: str) -> Any:
        """Get or create tool state for the specified kind (remote or internal)."""
        if tool_kind == "remote":
            if self._remote_tool_state is None:
                self._remote_tool_state = self._create_tool_state(tool_kind="remote")
            return self._remote_tool_state
        else:  # internal
            if self._internal_tool_state is None:
                self._internal_tool_state = self._create_tool_state(tool_kind="internal")
            return self._internal_tool_state

    def _create_tool_state(self, *, tool_kind: str) -> Any:
        """Create a tool state instance for the specified kind."""
        if tool_kind == "remote":
            module = importlib.import_module("tool_server.remote_tool_handler.tool_state")
            state_cls = getattr(module, "ToolState")
            task_id = f"{self.session_id}".replace(" ", "-")
            return state_cls(task_id=task_id, working_dir=self.working_dir)
        else:  # internal
            module = importlib.import_module("tool_server.internal_tool_handler.tool_state")
            state_cls = getattr(module, "ToolState")
            return state_cls(working_dir=self.working_dir)

    async def cleanup(self) -> None:
        for wrapper in self._tool_wrappers.values():
            try:
                await wrapper.cleanup()
            except Exception as exc:  # pragma: no cover - best effort cleanup
                logger.error("Failed to cleanup tool '%s': %s" % (wrapper.name, exc))

        # Cleanup tool states owned by ToolService
        if self._remote_tool_state is not None:
            try:
                terminate_fn = getattr(self._remote_tool_state, "terminate", None)
                if terminate_fn:
                    if inspect.iscoroutinefunction(terminate_fn):
                        await terminate_fn()
                    else:
                        terminate_fn()
            except Exception as exc:
                logger.error("Failed to cleanup remote tool state: %s" % exc)
            self._remote_tool_state = None

        if self._internal_tool_state is not None:
            try:
                terminate_fn = getattr(self._internal_tool_state, "terminate", None)
                if terminate_fn:
                    if inspect.iscoroutinefunction(terminate_fn):
                        await terminate_fn()
                    else:
                        terminate_fn()
            except Exception as exc:
                logger.error("Failed to cleanup internal tool state: %s" % exc)
            self._internal_tool_state = None

        self._tool_wrappers.clear()
        self._claude_tools = []

    def _load_tools(self) -> None:
        claude_tools = []
        failed_tools = []

        for tool_class in self._discover_tool_classes():
            try:
                tool_instance = tool_class()
            except Exception as exc:
                logger.error("Failed to initialize tool class %s: %s" % (tool_class.__name__, exc))
                failed_tools.append(tool_class.__name__)
                continue

            handler_info = self._resolve_handler(tool_class)
            if handler_info is None:
                logger.error("No handler found for tool %s" % tool_class.__name__)
                continue

            handler_module, handler_class_name, tool_kind = handler_info

            wrapper = ToolWrapper(
                name=tool_instance.name,
                tool_instance=tool_instance,
                handler_module=handler_module,
                handler_class_name=handler_class_name,
                tool_kind=tool_kind,
                tool_service=self,  # Pass self instead of working_dir and session_id
                agent=self.agent,
            )

            self._tool_wrappers[tool_instance.name] = wrapper
            claude_tools.append(tool_instance.to_claude_tool())
        
        if failed_tools:
            logger.error("Failed to load {len(failed_tools)} tools: {', '.join(failed_tools)}")
        else:
            logger.info(f"[AGENT INITIALIZATION] Successfully loaded all ({len(claude_tools)}) tools.")

        self._claude_tools = claude_tools

    def _discover_tool_classes(self) -> Iterable[Type[BaseTool]]:
        tools_dir = os.path.dirname(__file__)
        for entry in os.listdir(tools_dir):
            entry_path = os.path.join(tools_dir, entry)
            if not os.path.isdir(entry_path) or entry.startswith("_"):
                continue

            module_name = f"agents.tools.{entry}.tool"
            try:
                module = importlib.import_module(module_name)
            except Exception as exc:
                logger.error("Failed to import tool module %s: %s" % (module_name, exc))
                continue

            for attr_name in dir(module):
                attr = getattr(module, attr_name)
                if (
                    inspect.isclass(attr)
                    and issubclass(attr, BaseTool)
                    and attr is not BaseTool
                    and not attr.__name__.startswith("_")
                ):
                    yield attr

    def _resolve_handler(self, tool_class: Type[BaseTool]) -> Optional[Tuple[str, str, str]]:
        module_name = tool_class.__module__  # e.g. agents.tools.glob_tool.tool
        try:
            parts = module_name.split(".")
            if len(parts) == 4 and parts[0] == "agents" and parts[1] == "tools":
                # New format: agents.tools.{tool_module}.tool
                tool_module = parts[2]
            elif len(parts) == 3:
                # Old format: tools.{tool_module}.tool
                tool_module = parts[1]
            else:
                return None
        except (ValueError, IndexError):
            return None

        handler_class_name = f"{tool_class.__name__}Handler"

        remote_module = f"tool_server.remote_tool_handler.{tool_module}"
        internal_module = f"tool_server.internal_tool_handler.{tool_module}"

        if importlib.util.find_spec(remote_module) is not None:
            return (remote_module, handler_class_name, "remote")

        if importlib.util.find_spec(internal_module) is not None:
            return (internal_module, handler_class_name, "internal")

        return None


__all__ = ["ToolService", "ToolWrapper"]
