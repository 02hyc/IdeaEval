"""
Write Tool - Writes files to the local filesystem
"""
import os
from typing import Dict, Any
from toolkit.tool_handlers.base_tool_handler import BaseToolHandler, ToolState, ToolFailedError
from toolkit.tool_handlers.utils import generate_diff_data
from toolkit.tool_handlers.tool_categories import ToolName


class WriteToolHandler(BaseToolHandler):
    """Tool for writing files to the local filesystem"""
    
    def __init__(self, tool_state: ToolState):
        super().__init__(tool_state=tool_state)
        self.requires_confirmation = True
        # Dictionary to store write operation data for multiple files
        self._write_operations = {}
    
    @property
    def name(self) -> str:
        return ToolName.WRITE
    
    @property
    def display_result_type(self) -> str:
        return "edit_result"

    @property
    def input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Path to the file to write. Relative paths are resolved against the workspace working directory."
                },
                "content": {
                    "type": "string",
                    "description": "The content to write to the file"
                }
            },
            "required": ["file_path", "content"],
            "additionalProperties": False
        }

    async def execute_async(self, file_path: str, content: str, update_file: bool = True) -> Dict[str, Any]:
        """Execute the tool"""
        # Resolve relative paths against working directory
        if not os.path.isabs(file_path):
            working_dir = self.tool_state.get_working_dir()
            file_path = os.path.abspath(os.path.join(working_dir, file_path))

        # Check if this is an existing file (should have been read first)
        file_exists = os.path.exists(file_path) and os.path.isfile(file_path)
        
        # Store original content for diff generation
        original_content = ""
        if file_exists:
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    original_content = f.read()
            except:
                original_content = ""
        
        # Backup the original content before any modifications (only if update_file=True)
        # Mark as was_created=True if file didn't exist before
        if update_file:
            self.backup_file_state(file_path, original_content, was_created=not file_exists)
        
        # Check if existing file has been read first and the read is still fresh
        if file_exists:
            if not self.is_file_read_fresh(file_path):
                if not self.is_file_read(file_path):
                    raise ToolFailedError(f"Error: You must read the file's contents before overwriting: {file_path}")
                else:
                    raise ToolFailedError(f"Error: The file {file_path} has been modified since it was last read. Please read the file again before overwriting.")
        
        # Check if we're trying to write to a directory
        if os.path.exists(file_path) and os.path.isdir(file_path):
            raise ToolFailedError(f"Error: Path is a directory, not a file: {file_path}")
        
        try:
            # Ensure parent directory exists
            parent_dir = os.path.dirname(file_path)
            if parent_dir and not os.path.exists(parent_dir):
                try:
                    os.makedirs(parent_dir, exist_ok=True)
                except Exception as e:
                    raise ToolFailedError(f"Error: Could not create parent directory {parent_dir}: {str(e)}")
                
            if update_file:
                # Write the content to the file
                with open(file_path, 'w', encoding='utf-8') as f:
                    f.write(content)

                # Refresh the file read status
                self.mark_file_as_read(file_path)
            
            # Generate cat -n style output for agent result
            lines = content.split('\n')
            cat_output = []
            for i, line in enumerate(lines, 1):
                spaces = " " * (6 - len(str(i)))
                cat_output.append(f"{spaces}{i}\t{line}")
            
            cat_snippet = '\n'.join(cat_output)
            rel_path = os.path.relpath(file_path, self.tool_state.get_working_dir())
            agent_result = f"The file {rel_path} has been updated."
            
            # Generate diff data for display using the same format as edit_tool
            diff_data = self._generate_diff_data_for_display(original_content, content, file_path)
            
            return {
                'tool_success': True,
                'result': agent_result,
                'display_result': diff_data
            }
            
        except PermissionError:
            raise ToolFailedError(f"Error: Permission denied writing to file: {file_path}")
        
        except OSError as e:
            if e.errno == 36:  # File name too long
                raise ToolFailedError(f"Error: File name too long: {file_path}")
            elif e.errno == 28:  # No space left on device
                raise ToolFailedError(f"Error: No space left on device for file: {file_path}")
            else:
                raise ToolFailedError(f"Error: OS error writing file {file_path}: {str(e)}")
        
        except Exception as e:
            raise ToolFailedError(f"Error writing file {file_path}: {str(e)}")
    
    def _generate_diff_data_for_display(self, old_content, new_content, filename) -> Dict[str, Any]:
        """Generate diff data for display using the same format as edit_tool"""
        difftext = generate_diff_data(
            old_content=old_content,
            new_content=new_content)
        return {
            'old_file': filename if old_content else None,
            'new_file': filename,
            'diff': difftext
        }