import os
import importlib
from typing import List, Dict, Any, Optional
from .base_tool import BaseTool
from utils.logger import Logger
from agents.utils.exceptions import InvalidToolInputError


def normalize_tool_name(name: str) -> str:
    """
    Normalize tool names to the canonical names used by this repo.

    Some models/providers will emit tool names that are semantically correct but
    not part of our tool registry (e.g., "Read" instead of "View").
    """
    if not isinstance(name, str):
        return str(name)
    n = name.strip()
    if n.lower() == "read":
        return "View"
    return n


def get_all_claude_tools(exclude: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """Get all tools in Claude function calling format

    Args:
        exclude: Optional list of tool directory names to skip during discovery.
    """
    logger = Logger()
    tools = []
    tools_dir = os.path.dirname(__file__)
    loaded_tools = []
    failed_tools = []
    excluded = set(exclude) if exclude is not None else set()

    for item in os.listdir(tools_dir):
        if not os.path.isdir(os.path.join(tools_dir, item)) or item.startswith('_'):
            continue
        if item in excluded:
            continue
            
        tool_file = os.path.join(tools_dir, item, "tool.py")
        if not os.path.exists(tool_file):
            continue
            
        try:
            # Import via the fully qualified package path
            module_name = f"agents.tools.{item}.tool"
            module = importlib.import_module(module_name)
            
            for attr_name in dir(module):
                attr = getattr(module, attr_name)
                if (isinstance(attr, type) and issubclass(attr, BaseTool) and 
                    attr != BaseTool and not attr.__name__.startswith('_')):
                    try:
                        tool_instance = attr()
                        tools.append(tool_instance.to_claude_tool())
                        loaded_tools.append(tool_instance.name)
                        break
                    except Exception as e:
                        failed_tools.append(f"{attr.__name__}: {e}")
        except Exception as e:
            failed_tools.append(f"{item}: {e}")
    
    # logger.info(f"[TOOLS] Successfully loaded {len(loaded_tools)} tools: {', '.join(loaded_tools)}")
    if failed_tools:
        import traceback
        logger.error(f"Failed to load {len(failed_tools)} tools: {'; '.join(failed_tools)}. Related information: {traceback.format_exc()} \n {os.listdir(tools_dir)}")
    
    return tools


def get_all_tool_instances() -> Dict[str, BaseTool]:
    """Get all tool instances
    
    Returns:
        Dictionary of tool name to BaseTool instance
    """
    logger = Logger()
    tool_instances = {}
    tools_dir = os.path.dirname(__file__)
    failed_tools = []
    
    for item in os.listdir(tools_dir):
        if not os.path.isdir(os.path.join(tools_dir, item)) or item.startswith('_'):
            continue
            
        tool_file = os.path.join(tools_dir, item, "tool.py")
        if not os.path.exists(tool_file):
            continue
            
        try:
            # Use correct package path for import
            module_name = f"agents.tools.{item}.tool"
            module = importlib.import_module(module_name)
            
            for attr_name in dir(module):
                attr = getattr(module, attr_name)
                if (isinstance(attr, type) and issubclass(attr, BaseTool) and 
                    attr != BaseTool and not attr.__name__.startswith('_')):
                    try:
                        tool_instance = attr()
                        tool_instances[tool_instance.name] = tool_instance
                        break
                    except Exception as e:
                        failed_tools.append(f"{attr.__name__}: {e}")
        except Exception as e:
            failed_tools.append(f"{item}: {e}")
    
    if failed_tools:
        logger.error(f"Failed to load {len(failed_tools)} tool instances: {'; '.join(failed_tools)}")
    
    return tool_instances


ALL_TOOL_INSTANCES = get_all_tool_instances()


def validate_tool_input(name: str, input: Dict[str, Any]) -> None:
    """Validate the input for a specific tool

    Args:
        name: The tool name
        input: The input dictionary to validate

    Returns:
        None

    Raises:
        InvalidToolInputError: If validation fails for a known tool
    """
    canonical = normalize_tool_name(name)
    tool = ALL_TOOL_INSTANCES.get(canonical)

    if tool is None:
        # Unknown tool — skip validation (ranker tools are validated at handler level)
        return

    return tool.validate_input(input)
