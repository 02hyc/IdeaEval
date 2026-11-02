# Agent Tools Development Guide

This directory contains the tool framework used by the ranker agents. The tools system uses a modern architecture with separate tool registry (for tool classes) and tool service (for tool instances per agent). Each tool is organized in its own subdirectory with a clear structure that separates implementation from documentation.

## Architecture Overview

### ToolRegistry
- Manages registration and storage of tool classes
- Auto-discovers tools from the tools directory
- Provides tool metadata without creating instances

### ToolService  
- Creates and manages tool instances for each agent
- Each agent has its own tool service with separate tool instances
- Handles tool cleanup when agent is destroyed

### BaseTool
- Abstract base class for all tools
- Tools have access to their owning agent via `self.agent` (when used with agent)
- Implements standardized interface for tool execution and metadata

## Tool Structure

Each tool follows this directory structure:

```
tools/
├── tool_name/             # Individual tool directory
│   ├── __init__.py        # Python package initialization
│   ├── tool.py            # Tool implementation and schema definition
│   └── description.md     # Tool description in markdown format
```

## Creating a New Tool

### 1. Create Tool Directory

```bash
mkdir -p tools/tool_name
```

### 2. Create the Tool Implementation

Create `tool.py` with your tool class:

```python
"""
Tool Name - Brief description
"""
from typing import Dict, Any
from ..base import BaseTool
from ..registry import register_tool


@register_tool
class YourToolNameTool(BaseTool):
    """Tool for doing something useful"""
    
    @property
    def name(self) -> str:
        return "your_tool_name"  # This will be the function name in Claude
    
    @property
    def input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "param1": {
                    "type": "string",
                    "description": "Description of parameter 1"
                },
                "param2": {
                    "type": "integer", 
                    "description": "Description of parameter 2"
                }
            },
            "required": ["param1"],
            "additionalProperties": False
        }
    
    async def execute_async(self, param1: str, param2: int = None) -> str:
        """Execute the tool"""
        self.validate_input(param1=param1, param2=param2)
        
        # Your tool implementation here
        result = f"Processing {param1} with {param2}"
        
        return result
```

### 3. Create the Description

Create `description.md` with a clear, user-focused description:

```markdown
Brief description of what the tool does and when to use it. Focus on the user's perspective and include any important usage notes or limitations.
```

### 4. Add Package Files

Create `__init__.py`:

```python
# Tool description comment
```

### 5. Register Your Tool

The tool will be automatically registered when imported thanks to the `@register_tool` decorator.

## Tool Implementation Guidelines

### Schema Definition

- Use JSON Schema format for `input_schema`
- Include clear parameter descriptions
- Mark required parameters in the `required` array
- Use `additionalProperties: false` to prevent extra parameters

### Async Execution

- All tools must implement `execute_async()` method
- The base class provides a synchronous `execute()` wrapper
- Use proper error handling and return descriptive error messages

### Parameter Validation

- Call `self.validate_input(**kwargs)` at the start of `execute_async()`
- This validates required parameters against the schema
- Add custom validation logic as needed

### Error Handling

- Return descriptive error messages as strings
- For critical errors that should stop task execution, raise `CriticalTaskError`
- Log errors appropriately for debugging

### Output Format

- Always return a string result
- Format output to be readable and useful for the AI agent
- Include context and structure in your responses

## Existing Tools

- **web_search_tool/**: Web search and information retrieval
- **code_subagent_tool/**: Code execution and development tasks  
- **complete_task_tool/**: Task completion and report generation
- **task_tool/**: Launch subagents for complex, multi-step tasks
- **glob_tool/**: Fast file pattern matching with glob patterns
- **grep_tool/**: Powerful text search using ripgrep with regex support

## Tool Development Workflow

### Development Process

1. **Plan Your Tool**
   - Define the tool's purpose and scope
   - Identify input parameters and output format
   - Consider error cases and edge conditions
   - Check if similar functionality already exists

2. **Create Tool Structure**
   ```bash
   mkdir tools/your_tool_name
   cd tools/your_tool_name
   touch __init__.py tool.py description.md
   ```

3. **Implement the Tool**
   - Follow the template structure shown above
   - Start with basic functionality
   - Add parameter validation
   - Handle errors gracefully

4. **Test Thoroughly**
   - Use the testing script (see below)
   - Test with various input combinations
   - Verify error handling
   - Check integration with the ranker agents

5. **Document and Share**
   - Write clear description.md
   - Add usage examples
   - Update this README if needed

## Testing Your Tool

### Quick Testing Script

Use the built-in testing script for rapid development:

```bash
# Show available tools
python tools/test_tool.py --list

# Get tool information and schema
python tools/test_tool.py --info Glob

# Test Glob tool
python tools/test_tool.py Glob --pattern "*.py" --path /tmp

# Test search tool
python tools/test_tool.py create_search_subagent --prompt "latest AI research"
```

### Manual Testing in Python

```python
import asyncio
from tools import ToolService

async def test_my_tool():
    # Create a tool service (like an agent would)
    tool_service = ToolService()
    tool = tool_service.get_tool("your_tool_name")
    
    # Test parameter validation
    try:
        tool.validate_input(param1="test")
        print("✅ Validation passed")
    except Exception as e:
        print(f"❌ Validation failed: {e}")
    
    # Test execution
    try:
        result = await tool.execute_async(param1="test")
        print(f"✅ Result: {result}")
        return True
    except Exception as e:
        print(f"❌ Execution failed: {e}")
        return False
    finally:
        # Cleanup tool service
        await tool_service.cleanup()

# Run the test
success = asyncio.run(test_my_tool())
```

### Testing Checklist

Before submitting your tool, verify:

- [ ] **Parameter Validation**: All required parameters are validated
- [ ] **Error Handling**: Invalid inputs return helpful error messages
- [ ] **Edge Cases**: Empty inputs, extreme values, missing data handled
- [ ] **Output Format**: Results are consistently formatted and readable
- [ ] **Performance**: Tool executes within reasonable time limits
- [ ] **Documentation**: Clear description and parameter explanations
- [ ] **Integration**: Works correctly inside a ranker run

### Integration Testing

Test your tool inside a full ranker run:

1. **Run the ranker**: e.g. `uv run python rank_main.py ...` with the tool enabled for an agent
2. **Use Your Tool**: Give the agent a task that needs your tool
3. **Verify Behavior**: Check that the agent calls your tool correctly
4. **Test Error Cases**: Try invalid parameters through the agent
5. **Check Logging**: Ensure proper logging and error reporting

### Debugging Tips

**Common Issues:**

1. **Import Errors**
   ```bash
   # Test imports and tool service
   python -c "from tools import ToolService; ts = ToolService(); print(ts.get_tool('your_tool_name'))"
   ```

2. **Schema Validation Failures**
   ```python
   # Check schema format
   from tools import ToolService
   tool_service = ToolService()
   tool = tool_service.get_tool('your_tool_name')
   print(json.dumps(tool.input_schema, indent=2))
   ```

3. **Async/Await Issues**
   ```python
   # Always use await with execute_async
   result = await tool.execute_async(param="value")  # ✅ Correct
   result = tool.execute_async(param="value")        # ❌ Wrong
   ```

4. **Parameter Type Mismatches**
   ```python
   # Check parameter types in schema
   schema = tool.input_schema
   for param, info in schema['properties'].items():
       print(f"{param}: {info['type']}")
   ```

### Performance Testing

For tools that may be slow or resource-intensive:

```python
import time
import asyncio
from tools import ToolService

async def benchmark_tool():
    tool_service = ToolService()
    tool = tool_service.get_tool("your_tool_name")
    
    try:
        # Measure execution time
        start_time = time.time()
        result = await tool.execute_async(param="test")
        elapsed = time.time() - start_time
        
        print(f"Execution time: {elapsed:.2f}s")
        print(f"Result length: {len(result)} characters")
        
        # Test with different input sizes
        test_cases = ["small", "medium" * 100, "large" * 1000]
        for case in test_cases:
            start = time.time()
            await tool.execute_async(param=case)
            elapsed = time.time() - start
            print(f"Input size {len(case)}: {elapsed:.2f}s")
    finally:
        await tool_service.cleanup()

asyncio.run(benchmark_tool())
```

## Best Practices

### Code Style

- Follow Python PEP 8 style guidelines
- Use type hints for all parameters and return values
- Include docstrings for classes and methods
- Keep tool logic focused and single-purpose

### Documentation

- Write clear, concise descriptions in `description.md`
- Focus on when and how to use the tool
- Include any limitations or important notes
- Use active voice and user-friendly language

### Error Handling

- Provide helpful error messages
- Validate inputs thoroughly
- Handle edge cases gracefully
- Use appropriate exception types

### Performance

- Make tools as efficient as possible
- Use async/await properly for I/O operations
- Consider caching for expensive operations
- Provide progress feedback for long-running tasks

## Advanced Tool Development

### Tool Templates

Use the template files in `_templates/` as starting points:

```bash
# Copy template files
cp tools/_templates/tool.py.template tools/my_tool/tool.py
cp tools/_templates/description.md.template tools/my_tool/description.md
cp tools/_templates/__init__.py.template tools/my_tool/__init__.py

# Edit the files to implement your tool
```

### Complex Parameter Schemas

For tools with complex input requirements:

```python
@property
def input_schema(self) -> Dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "files": {
                "type": "array",
                "items": {"type": "string"},
                "description": "List of file paths to process"
            },
            "options": {
                "type": "object",
                "properties": {
                    "recursive": {"type": "boolean", "default": False},
                    "max_depth": {"type": "integer", "minimum": 1}
                },
                "additionalProperties": False
            },
            "format": {
                "type": "string",
                "enum": ["json", "csv", "yaml"],
                "default": "json"
            }
        },
        "required": ["files"],
        "additionalProperties": False
    }
```

### Tool Dependencies

For tools that require external libraries:

```python
async def execute_async(self, **kwargs) -> str:
    try:
        import external_library
    except ImportError:
        return "Error: This tool requires 'external_library'. Install with: pip install external_library"
    
    # Tool implementation using external_library
    result = external_library.process(kwargs)
    return str(result)
```

### Tool Composition

Tools can access other tools through their agent's tool service:

```python
async def execute_async(self, input_data: str) -> str:
    # Access other tools through the agent's tool service
    if self.agent and hasattr(self.agent, 'tool_service'):
        # Step 1: Use search tool
        search_tool = self.agent.tool_service.get_tool("create_search_subagent")
        search_result = await search_tool.execute_async(prompt=f"Research: {input_data}")
        
        # Step 2: Process with code tool
        code_tool = self.agent.tool_service.get_tool("create_code_subagent") 
        analysis = await code_tool.execute_async(prompt=f"Analyze: {search_result}")
        
        return f"Combined result:\n{analysis}"
    else:
        return "Error: Tool composition requires agent context"
```

### Error Recovery and Retries

For tools that might fail intermittently:

```python
import asyncio
from typing import Optional

async def execute_async(self, url: str, retries: int = 3) -> str:
    last_error: Optional[Exception] = None
    
    for attempt in range(retries):
        try:
            result = await self._fetch_data(url)
            return result
        except Exception as e:
            last_error = e
            if attempt < retries - 1:
                await asyncio.sleep(2 ** attempt)  # Exponential backoff
            continue
    
    return f"Error after {retries} attempts: {last_error}"
```

## Tool Testing Examples

### Example 1: Testing the Glob Tool

```bash
# Test basic functionality - list all Python files
python tools/test_tool.py Glob --pattern "*.py" --path /tmp

# Test recursive pattern
python tools/test_tool.py Glob --pattern "**/*.md"

# Test error cases
python tools/test_tool.py Glob --pattern "*.py" --path /nonexistent/path
```

### Example 2: Testing Search Tool

```bash
# Test search functionality
python tools/test_tool.py create_search_subagent --prompt "latest developments in AI safety research"

# Test with empty prompt (should fail)
python tools/test_tool.py create_search_subagent --prompt ""
```

### Example 3: Testing Custom Tool

For a hypothetical file processing tool:

```bash
# Test normal operation
python tools/test_tool.py process_files --files "file1.txt" "file2.txt" --format json

# Test with arrays and objects
python tools/test_tool.py complex_tool --items "a" "b" "c" --config '{"key": "value"}'
```

## Troubleshooting

### Common Development Issues

1. **Tool Not Discovered**
   - Check that `__init__.py` exists in tool directory
   - Verify tool class has `@register_tool` decorator
   - Ensure tool directory is directly under `tools/`

2. **Schema Validation Errors**
   - Use JSON Schema validator to check schema syntax
   - Test schema against sample inputs
   - Check required vs optional parameter definitions

3. **Import Circular Dependencies**
   - Import tools inside methods, not at module level
   - Use string-based tool references where possible
   - Consider refactoring shared code into utilities

4. **Async/Sync Confusion**
   - Always implement `execute_async()` as async
   - Use `await` when calling other async functions
   - The base class provides sync wrapper automatically

### Debugging Workflows

1. **Test Tool Loading**
   ```bash
   python -c "from tools import ToolService; ts = ToolService(); print(list(ts.get_all_tools().keys()))"
   ```

2. **Inspect Tool Schema**
   ```bash
   python tools/test_tool.py --info your_tool_name
   ```

3. **Test with Minimal Input**
   ```bash
   python tools/test_tool.py your_tool_name --required_param "minimal_value"
   ```

4. **Check Claude Integration**
   ```python
   from tools import ToolService
   tool_service = ToolService()
   tools = tool_service.get_claude_tools()
   print([t['name'] for t in tools])
   ```

## Contributing

1. Fork the repository
2. Create a feature branch for your tool
3. Follow the tool structure guidelines
4. Test your tool thoroughly
5. Submit a pull request with a clear description

## Tool Registry and Service

The tools system uses a two-tier architecture:

### ToolRegistry
- Automatically discovers and registers tool classes using the `@register_tool` decorator
- Stores tool classes (not instances) for efficient memory usage
- Provides access to tool metadata without creating instances

### ToolService  
- Creates and manages tool instances for each agent
- Each agent has its own tool service with isolated tool state
- Provides the interface agents use to access tools:
  - `get_tool(name)`: Get a specific tool instance
  - `get_all_tools()`: Get all tool instances for this service
  - `get_claude_tools()`: Get tools in Claude function calling format
  - `cleanup()`: Clean up all tool instances

### Agent Integration
- Each agent creates a ToolService instance during initialization
- Tools can access their owning agent via `self.agent` for complex workflows
- Tool state is isolated per agent, preventing interference between agents